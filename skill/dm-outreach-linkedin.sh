#!/usr/bin/env bash
# dm-outreach-linkedin.sh — Outbound LinkedIn DM outreach.
# Scans for DM candidates (users who engaged on our posts), then sends LinkedIn
# messages to continue the conversation. Inbound DM replies are handled separately
# by engage-dm-replies-linkedin.sh.
# Called by launchd (com.m13v.social-dm-outreach-linkedin) every 6 hours.

set -euo pipefail

# Browser-profile lock first (shared with other linkedin pipelines), then pipeline lock.
source "$(dirname "$0")/lock.sh"
acquire_lock "linkedin-browser" 3600
acquire_lock "dm-outreach-linkedin" 2700

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/dm-outreach-linkedin-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== LinkedIn DM Outreach Run: $(date) ==="

# Scan for new DM candidates first (cheap Python, writes to dms table)
log "Scanning for DM candidates (all platforms)..."
(PYTHONUNBUFFERED=1 python3 "$REPO_DIR/scripts/scan_dm_candidates.py" 2>&1 || true) | tee -a "$LOG_FILE"

DM_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE status='pending' AND platform='linkedin';" 2>/dev/null || echo "0")

if [ "$DM_PENDING" -eq 0 ]; then
    log "No pending LinkedIn DMs"
    python3 "$REPO_DIR/scripts/log_run.py" --script "dm_outreach_linkedin" --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed $(( $(date +%s) - RUN_START ))
    exit 0
fi

log "LinkedIn: $DM_PENDING DMs to send"

DM_DATA=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(q) FROM (
        SELECT d.id, d.platform, d.their_author, d.their_content, d.comment_context,
               r.their_comment_url, r.our_reply_content,
               p.thread_title, p.our_content as our_post_content
        FROM dms d
        JOIN replies r ON d.reply_id = r.id
        JOIN posts p ON d.post_id = p.id
        WHERE d.status='pending' AND d.platform='linkedin'
        ORDER BY d.discovered_at ASC
    ) q;")

export CLAUDE_SESSION_ID=$(uuidgen | tr 'A-Z' 'a-z')

PROMPT_FILE=$(mktemp)
cat > "$PROMPT_FILE" <<PROMPT_EOF
You are the Social Autoposter LinkedIn DM outreach bot.

Read $SKILL_FILE for content rules (tone, anti-AI detection, no em dashes).

## Task: Send LinkedIn messages to continue comment conversations

These users engaged with our LinkedIn posts/comments. We already replied publicly. Now send a short, casual message to continue the conversation.

CRITICAL RULES:
1. Messages must feel like a natural continuation of the comment discussion, NOT a cold outreach or sales pitch
2. Reference the specific conversation topic, not generic "hey I saw your comment"
3. Keep it short: 1-2 sentences max, like a text message
4. No links in the first DM; earn the conversation first
5. No em dashes. Write casually, like texting a coworker.

## COMMITMENT GUARDRAILS (violating any of these is a critical failure)
- **NEVER suggest, offer, or agree to calls, meetings, demos, or video chats.** Keep everything in the DM thread.
- **NEVER agree to podcast appearances, X Spaces, interviews, or live events.**
- **NEVER offer to move the conversation to another platform** (Telegram, Discord, email, etc.). Stay in the current DM thread.
- **NEVER promise to share specific links, files, or resources you don't have right now.** If you don't have it in config.json projects, don't promise it.
- **NEVER make time-bound commitments** ("this week", "tomorrow", "Thursday"). Don't commit the human to any schedule.
- **NEVER say "I'm in [city]"** or share location/personal details not in config.json.
- If someone asks for any of the above, respond naturally but deflect: keep the conversation going in the DM without making promises. Example: "honestly easier to hash it out here, what specifically are you trying to set up?"

DM EXAMPLES (good):
- "yo your point about token costs scaling with agent count hit home, we're dealing with the exact same thing. what's your setup look like?"
- "that workaround you mentioned for the accessibility API crash is clever, did it hold up in production?"
- "curious how you ended up going with that approach for the MCP server, we tried something similar"

DM EXAMPLES (bad):
- "Hey! I noticed your comment on LinkedIn. I'm building something you might find interesting..." (cold pitch)
- "Great point! I'd love to connect and share what we're working on." (generic)
- "Hi there, I saw your insightful comment about AI agents..." (too formal)

## Users to DM:
$DM_DATA

## How to send messages on LinkedIn (use mcp__linkedin-agent__* tools):
1. Navigate to https://www.linkedin.com/messaging/
2. Start new message to THEIR_AUTHOR
3. Type and send the message.

## After each DM:

Success (BOTH steps required):
  psql "\$DATABASE_URL" -c "UPDATE dms SET status='sent', our_dm_content='DM_TEXT', sent_at=NOW(), claude_session_id='$CLAUDE_SESSION_ID'::uuid WHERE id=DM_ID;"
  python3 $REPO_DIR/scripts/dm_conversation.py log-outbound --dm-id DM_ID --content "DM_TEXT"

Failed (rate limit, blocked, error):
  psql "\$DATABASE_URL" -c "UPDATE dms SET status='error', skip_reason='REASON', claude_session_id='$CLAUDE_SESSION_ID'::uuid WHERE id=DM_ID;"

DMs disabled:
  psql "\$DATABASE_URL" -c "UPDATE dms SET status='skipped', skip_reason='chat_disabled', claude_session_id='$CLAUDE_SESSION_ID'::uuid WHERE id=DM_ID;"

CRITICAL: ALL browser calls MUST use mcp__linkedin-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools. If a linkedin-agent tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). Do NOT fall back to any other browser tool.
PROMPT_EOF

gtimeout 2700 "$REPO_DIR/scripts/run_claude.sh" "dm-outreach-linkedin" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json" -p "$(cat "$PROMPT_FILE")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: LinkedIn DM outreach claude exited with code $?"
rm -f "$PROMPT_FILE"

SENT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE platform='linkedin' AND status='sent';" 2>/dev/null || echo "0")
STILL_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE platform='linkedin' AND status='pending';" 2>/dev/null || echo "0")
log "LinkedIn DM outreach summary: sent (all-time)=$SENT, still_pending=$STILL_PENDING"

RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
python3 "$REPO_DIR/scripts/log_run.py" --script "dm_outreach_linkedin" --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed "$RUN_ELAPSED"

find "$LOG_DIR" -name "dm-outreach-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== LinkedIn DM outreach complete: $(date) ==="
