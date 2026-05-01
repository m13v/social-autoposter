#!/usr/bin/env bash
# click-followup.sh — Click-driven DM follow-up loop.
#
# When a recipient clicks the cal.com short link we sent in a DM, that's a
# high-intent signal. The default outreach pipeline never reads
# short_link_clicks, so warm leads sit idle. This driver:
#
#   1. Calls scripts/scan_click_followups.py to find DMs with new clicks
#      that haven't been followed up on.
#   2. Per platform, builds a prompt that injects the click signal as
#      context and asks Claude to send a soft, low-pressure nudge in the
#      existing chat thread (no brand-new outreach).
#   3. After a verified send, calls scripts/mark_click_followup.py to
#      stamp dms.last_click_followup_at = NOW() so the same click
#      doesn't trigger another follow-up.
#
# Mirrors dm-outreach-<platform>.sh structure (lock, env load, prompt
# build, run_claude.sh with the per-platform MCP config, summary log).
# Called by launchd (com.m13v.social-click-followup-<platform>) twice a
# day.
#
# Usage:
#   click-followup.sh --platform reddit
#   click-followup.sh --platform twitter
#   click-followup.sh --platform linkedin
#   click-followup.sh --platform reddit --dry-run
#
# --dry-run: print candidates only, do NOT invoke Claude / send anything.

set -euo pipefail

PLATFORM=""
DRY_RUN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform) PLATFORM="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [ -z "$PLATFORM" ]; then
    echo "ERROR: --platform required (reddit|twitter|linkedin)"
    exit 1
fi

case "$PLATFORM" in
    reddit|twitter|linkedin) ;;
    x) PLATFORM="twitter" ;;
    *) echo "ERROR: unknown platform '$PLATFORM'"; exit 1 ;;
esac

# Pipeline lock at top. Browser lock acquired later, just before the MCP
# step, so peers can use the profile during DB scan + prompt build.
source "$(dirname "$0")/lock.sh"
acquire_lock "click-followup-$PLATFORM" 2700

# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/bin/python3}"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/click-followup-${PLATFORM}-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== Click-driven DM follow-up: platform=$PLATFORM dry_run=$DRY_RUN $(date) ==="

# Scan for candidates. Pure read-only, safe to call any time.
CAND_JSON=$("$PYTHON_BIN" "$REPO_DIR/scripts/scan_click_followups.py" --platform "$PLATFORM" --max 25 2>>"$LOG_FILE" || echo "[]")
COUNT=$(echo "$CAND_JSON" | "$PYTHON_BIN" -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
log "Scanner found $COUNT click-followup candidate(s) on $PLATFORM"

if [ "$COUNT" -eq 0 ]; then
    log "No candidates, nothing to do."
    "$PYTHON_BIN" "$REPO_DIR/scripts/log_run.py" --script "click_followup_$PLATFORM" --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed $(( $(date +%s) - RUN_START )) 2>/dev/null || true
    exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY-RUN: candidates JSON below"
    echo "$CAND_JSON" | tee -a "$LOG_FILE"
    log "DRY-RUN complete, no sends."
    exit 0
fi

# Project context (booking links, qualification, voice). Reused from
# engage-dm-replies pattern.
PROJECTS=$("$PYTHON_BIN" -c "
import json
c = json.load(open('$REPO_DIR/config.json'))
for p in c.get('projects', []):
    line = f\"- {p['name']}: {p.get('description','')} | website: {p.get('website','')}\"
    if p.get('booking_link'):
        line += f\" | booking_link: {p['booking_link']}\"
    q = p.get('qualification') or {}
    if q.get('question'):
        line += f\" | qualifying_question: {q['question']}\"
    print(line)
" 2>/dev/null || echo "")

export CLAUDE_SESSION_ID=$(uuidgen | tr 'A-Z' 'a-z')

# Per-platform MCP config + send instructions
case "$PLATFORM" in
    reddit)
        MCP_CONFIG="$HOME/.claude/browser-agent-configs/reddit-agent-mcp.json"
        BROWSER_LOCK="reddit-browser"
        SEND_INSTRUCTIONS=$(cat <<'EOM'
## How to send the follow-up DM on Reddit (use mcp__reddit-agent__* tools ONLY):
1. Navigate to the existing chat at chat_url. If chat_url is null, navigate to https://www.reddit.com/message/compose/?to=THEIR_AUTHOR.
2. Reddit Chat: type the message in the existing thread (do NOT start a new chat if the thread exists).
3. The send_dm / compose_dm tool returns JSON with "ok" and "verified" fields. The send only counts when BOTH are true.

CRITICAL: ALL browser calls MUST be mcp__reddit-agent__* tools. NEVER use mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
EOM
)
        ;;
    twitter)
        MCP_CONFIG="$HOME/.claude/browser-agent-configs/twitter-agent-mcp.json"
        BROWSER_LOCK="twitter-browser"
        SEND_INSTRUCTIONS=$(cat <<'EOM'
## How to send the follow-up DM on X/Twitter (use mcp__twitter-agent__* tools ONLY):
1. Navigate to chat_url (the existing DM thread). If chat_url is null, navigate to https://x.com/messages/compose and start a thread to THEIR_AUTHOR.
2. Type the message in the existing DM thread.
3. The send_dm tool returns JSON with "ok" and "verified" fields. Send counts only when BOTH true.
4. If the recipient requires premium/verification or has DMs locked, mark error and stop. Do NOT bypass.

CRITICAL: ALL browser calls MUST be mcp__twitter-agent__* tools.
EOM
)
        ;;
    linkedin)
        MCP_CONFIG="$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json"
        BROWSER_LOCK="linkedin-browser"
        SEND_INSTRUCTIONS=$(cat <<'EOM'
## How to send the follow-up DM on LinkedIn (use mcp__linkedin-agent__* tools ONLY):
1. Navigate to chat_url (the existing thread). If chat_url is null, navigate to /messaging/ and open the thread with THEIR_AUTHOR.
2. Type the message in the existing thread (do NOT send a new InMail or open a new conversation).
3. send_dm returns JSON with "ok"/"verified". Send counts only when both true.
4. NEVER call /voyager/api/* of any kind. NEVER multi-page scroll. Read-only DOM ops + a single send keystroke per DM.
5. If a login/checkpoint page appears, print SESSION_INVALID and stop.

CRITICAL: ALL browser calls MUST be mcp__linkedin-agent__* tools.
EOM
)
        ;;
esac

PROMPT_FILE=$(mktemp)
cat > "$PROMPT_FILE" <<PROMPT_EOF
You are the Social Autoposter click-driven follow-up bot for $PLATFORM.

Read $SKILL_FILE for content rules (tone, anti-AI detection, no em dashes).

## Task: Soft-nudge follow-up to recipients who clicked our cal.com link

Each row below is a DM we already sent where the recipient went on to click
the booking short link we included (\`short_link_clicks > 0\`). They did NOT
book; the click is the most recent signal we have. This is high intent,
but they bounced at the booking page or got distracted, so the right play
is a SHORT, low-pressure follow-up in the SAME chat thread.

CRITICAL RULES:
1. Send in the existing thread. Never start a new chat if a thread exists.
2. Do NOT mention "you clicked our link" — they'll find it creepy. Reference
   the conversation topic instead, then add a soft helper line.
3. Keep it 1-2 sentences. Like a text. No em dashes.
4. Do NOT send another booking link. They have it. Asking a question is
   higher EV than spamming the URL again.
5. If their last reply already asked us a question we haven't answered,
   answer THAT first. The click is context, not the prompt.

## COMMITMENT GUARDRAILS (violating any of these is a critical failure)
- NEVER offer calls, meetings, demos, or video chats outside the existing
  booking link they already have.
- NEVER agree to podcasts, X Spaces, interviews.
- NEVER move the conversation off-platform (Telegram, Discord, email).
- NEVER promise links, files, or resources you don't have in config.json.
- NEVER make time-bound commitments ("this week", "tomorrow").
- NEVER reveal that we track clicks. The click count is internal context.

## Soft-nudge examples (good):
- "btw if there's a specific use case you wanted me to walk through before booking, happy to riff on it here"
- "no rush on the call, what's the sticking point right now? could probably answer half of it in dm"
- "ya curious what your gut reaction was — does this actually solve a problem you have today, or is it more of a 'maybe later' thing?"

## Soft-nudge examples (bad):
- "I noticed you visited the booking page!" (creepy)
- "Just bumping this in case you missed it 🙂" (cringe + emoji)
- "Want me to send the link again?" (they have it; pointless)
- "Are you still interested?" (low-effort, makes them feel pressured)

## Project context:
$PROJECTS

## Per-platform send instructions
$SEND_INSTRUCTIONS

## Candidates (each row has the conversation history; click signal is internal context, do NOT mention it):
$CAND_JSON

## For each candidate, in order:

1. Read \`recent_messages\` carefully. Find their last inbound message.
   - If they asked a direct question we never answered, lead with that answer.
   - Otherwise, write a fresh angle that builds on the last topic exchanged.

2. Compose the follow-up using the soft-nudge style above. Pull the
   conversational tone from the existing thread, do not pivot voice.

3. Send via the platform's mcp__<platform>-agent__* send tool.

4. Inspect the tool's return value:

(A) ok=true AND verified=true -> success. Mark sent + stamp click followup:
    CLAUDE_SESSION_ID=$CLAUDE_SESSION_ID $PYTHON_BIN $REPO_DIR/scripts/dm_conversation.py log-outbound \\
        --dm-id DM_ID --content "FULL_DM_TEXT" --verified
    $PYTHON_BIN $REPO_DIR/scripts/mark_click_followup.py --dm-id DM_ID --verified --note "click-followup; clicks=N"

(B) ok=false OR verified=false -> send didn't land. Do NOT call
    mark_click_followup.py. Run:
    psql "\$DATABASE_URL" -c "UPDATE dms SET claude_session_id='$CLAUDE_SESSION_ID'::uuid WHERE id=DM_ID;"
    Then move on to the next candidate.

(C) Rate limit / account block / other thrown exception -> stop the entire run
    immediately. Do NOT call mark_click_followup.py. Print STOPPED_RATE_LIMIT
    and exit.

5. After all candidates processed, print a one-line summary:
   FOLLOWUP_DONE platform=$PLATFORM sent=<n> failed=<m> total=<c>

DO NOT issue raw "UPDATE dms SET status=..." commands. Status is set by
dm_send_log.py / dm_conversation.py only. Click-followup stamps go via
mark_click_followup.py only.
PROMPT_EOF

# Acquire the platform browser lock NOW, just before the MCP step.
log "Acquiring $BROWSER_LOCK lock for Claude/MCP step..."
acquire_lock "$BROWSER_LOCK" 3600

# ensure_browser_healthy is defined in lock.sh for reddit/twitter/linkedin
if declare -f ensure_browser_healthy >/dev/null; then
    ensure_browser_healthy "$PLATFORM" || log "WARN: ensure_browser_healthy returned non-zero, continuing"
fi

gtimeout 2700 "$REPO_DIR/scripts/run_claude.sh" "click-followup-$PLATFORM" \
    --strict-mcp-config --mcp-config "$MCP_CONFIG" \
    -p "$(cat "$PROMPT_FILE")" 2>&1 | tee -a "$LOG_FILE" \
    || log "WARNING: click-followup claude exited with code $?"

rm -f "$PROMPT_FILE"

# Summary
SENT_AFTER=$("$PYTHON_BIN" -c "
import os, sys
sys.path.insert(0, '$REPO_DIR/scripts')
import db as dbmod
db = dbmod.get_conn()
cur = db.execute(\"SELECT COUNT(*) FROM dms WHERE platform = %s AND last_click_followup_at >= NOW() - INTERVAL '1 hour'\", ['$PLATFORM'])
print(cur.fetchone()[0])
" 2>/dev/null || echo "0")
log "Click follow-up summary ($PLATFORM): stamped_in_last_hour=$SENT_AFTER"

RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
_COST=$("$PYTHON_BIN" "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START" --scripts "click-followup-$PLATFORM" 2>/dev/null || echo "0.0000")
"$PYTHON_BIN" "$REPO_DIR/scripts/log_run.py" --script "click_followup_$PLATFORM" --posted "$SENT_AFTER" --skipped 0 --failed 0 --cost "$_COST" --elapsed "$RUN_ELAPSED" 2>/dev/null || true

find "$LOG_DIR" -name "click-followup-${PLATFORM}-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== Click follow-up complete ($PLATFORM): $(date) ==="
