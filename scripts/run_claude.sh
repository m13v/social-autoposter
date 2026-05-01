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
trap 'rm -f "$SIDE_LOG"' EXIT
claude --session-id "$SESSION_ID" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} "$@" | tee "$SIDE_LOG"
RC=${PIPESTATUS[0]}

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
