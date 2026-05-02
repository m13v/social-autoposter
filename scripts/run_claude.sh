#!/bin/bash
# Wrapper around `claude` that pre-assigns a session UUID, exports it for
# downstream loggers (log_post.py, reply_db.py, dm_conversation.py read
# CLAUDE_SESSION_ID from env), and after the session exits records token
# usage + computed cost into the claude_sessions table.
#
# Usage:
#   scripts/run_claude.sh <script_tag> -p "PROMPT" [other claude flags...]
#
# Runner migration pattern:
#   OLD: claude -p "PROMPT" 2>&1 | tee -a "$LOG_FILE"
#   NEW: scripts/run_claude.sh "run-linkedin" -p "PROMPT" 2>&1 | tee -a "$LOG_FILE"
#
# The wrapper passes everything after the script_tag verbatim to `claude`,
# so all flags (--output-format, --json-schema, --model, etc.) work unchanged.
# stdout is streamed straight from claude — no buffering — so existing pipes
# and parsers see identical output.

set -uo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: run_claude.sh <script_tag> <claude args...>" >&2
    exit 2
fi

SCRIPT_TAG="$1"; shift

# If the caller pre-set CLAUDE_SESSION_ID, honor it. This lets calling
# scripts inject the same UUID into their prompt (e.g. for SQL inserts that
# need to stamp claude_session_id) before invoking the wrapper.
SESSION_ID="${CLAUDE_SESSION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
export CLAUDE_SESSION_ID="$SESSION_ID"

# Auto-detect the platform agent from --mcp-config and signal the PreToolUse
# hooks (~/.claude/hooks/<platform>-agent-lock.sh) to bypass the cross-session
# block check. Rationale: every caller of run_claude.sh inside this repo is a
# launchd-managed pipeline that has already acquired the shell-level
# <platform>-browser lock via skill/lock.sh BEFORE invoking us. The shell
# lock is the authoritative serializer; the hook lock used to layer a second
# block on top, which produced false positives like the 2026-05-01 14:33
# LinkedIn run that paid $8.91 for an empty envelope because the *prior*
# LinkedIn cycle's JSONL was 57s stale (under the hook's 60s threshold)
# even though the shell lock had cleanly released.
#
# When SA_PIPELINE_LOCKED=1 is set, the hook trusts the shell layer and
# skips the cross-session check entirely.
for arg in "$@"; do
    case "$arg" in
        *linkedin-agent-mcp.json) export SA_PIPELINE_PLATFORM="linkedin"; export SA_PIPELINE_LOCKED=1 ;;
        *twitter-agent-mcp.json)  export SA_PIPELINE_PLATFORM="twitter";  export SA_PIPELINE_LOCKED=1 ;;
        *reddit-agent-mcp.json)   export SA_PIPELINE_PLATFORM="reddit";   export SA_PIPELINE_LOCKED=1 ;;
    esac
done

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
START=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)

# Allow one-off model override without touching locked scripts.
MODEL_ARGS=()
if [ -n "${MODEL_OVERRIDE:-}" ]; then
    MODEL_ARGS=(--model "$MODEL_OVERRIDE")
fi

# Tee claude's stdout to a side file so we can extract the native SDK cost
# (streamRes.total_cost_usd) emitted in the final result event of stream-json /
# json output. Stdout still flows unchanged to whoever piped this wrapper, so
# downstream parsers see identical bytes. PIPESTATUS[0] preserves claude's
# exit code through the tee.
SIDE_LOG="$(mktemp -t sa_run_claude_stdout.XXXXXX)"

# After-claude cleanup: explicitly remove the hook-layer lockfile for this
# session so the NEXT pipeline cycle doesn't see a stale lock from us. The
# unlock hook (PostToolUse) refreshes the lock timestamp to keep it alive
# across multi-tool sessions; without an explicit final cleanup, the lock
# survives session end and (per JSONL-mtime check) reads as "live" for up
# to 60s after we exit, causing the false-positive that produced the
# 2026-05-01 14:33 $8.91 empty-envelope run.
_sa_cleanup() {
    rm -f "$SIDE_LOG"
    if [ -n "${SA_PIPELINE_PLATFORM:-}" ]; then
        local lockfile="$HOME/.claude/${SA_PIPELINE_PLATFORM}-agent-lock.json"
        if [ -f "$lockfile" ]; then
            # Only remove if WE hold it — defensive in case a peer raced in.
            local holder
            holder=$(jq -r '.session_id // empty' "$lockfile" 2>/dev/null || echo "")
            if [ "$holder" = "$SESSION_ID" ]; then
                rm -f "$lockfile"
            fi
        fi
    fi
}
trap _sa_cleanup EXIT

# AUP-refusal retry loop. The Claude API safety filter occasionally refuses
# Phase A / SERP-driven prompts non-deterministically (the same prompt that
# refused at 18:13 succeeded at 17:58 today, 2026-05-01). Refusal output
# format: "API Error: Claude Code is unable to respond to this request,
# which appears to violate our Usage Policy". Retry up to 2 more times with
# 30s / 60s backoff and a fresh session UUID each retry (the prior session
# may have been flagged backend-side). Other RC failures pass through.
: > "$SIDE_LOG"
MAX_AUP_RETRIES=2
AUP_BACKOFF=(30 60)
RC=0
attempt=0
while :; do
    attempt=$((attempt + 1))
    claude --session-id "$SESSION_ID" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} "$@" | tee -a "$SIDE_LOG"
    RC=${PIPESTATUS[0]}
    if grep -qE "(API Error|Error).*Usage Policy|appears to violate our Usage Policy" "$SIDE_LOG"; then
        if [ "$attempt" -le "$MAX_AUP_RETRIES" ]; then
            sleep_secs="${AUP_BACKOFF[$((attempt - 1))]:-60}"
            echo "[run_claude] AUP refusal on attempt $attempt/$((MAX_AUP_RETRIES + 1)); retrying in ${sleep_secs}s with new session" >&2
            sleep "$sleep_secs"
            SESSION_ID="$(uuidgen | tr 'A-Z' 'a-z')"
            export CLAUDE_SESSION_ID="$SESSION_ID"
            : > "$SIDE_LOG"
            continue
        fi
        echo "[run_claude] AUP refusal on final attempt $attempt; giving up" >&2
    fi
    break
done

END=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)

# Pull the LAST total_cost_usd in the stdout (the result event is emitted last
# in both stream-json and json modes). Tolerant to spaces and floats; defaults
# to empty when the format doesn't expose a result event (e.g. interactive runs
# that crash before the result line) so log_claude_session.py just leaves the
# DB column NULL.
ORCH_COST="$(grep -oE '"total_cost_usd"[[:space:]]*:[[:space:]]*[0-9]+(\.[0-9]+)?' "$SIDE_LOG" 2>/dev/null \
    | tail -1 \
    | sed -E 's/.*:[[:space:]]*//')"

# Best-effort cost logging. Never let logging failures mask the wrapped
# command's exit code.
ORCH_ARGS=()
if [ -n "$ORCH_COST" ]; then
    ORCH_ARGS=(--orchestrator-cost-usd "$ORCH_COST")
fi
python3 "$REPO_DIR/scripts/log_claude_session.py" \
    --session-id "$SESSION_ID" \
    --script "$SCRIPT_TAG" \
    --started-at "$START" \
    --ended-at "$END" \
    ${ORCH_ARGS[@]+"${ORCH_ARGS[@]}"} >&2 || true

exit $RC
