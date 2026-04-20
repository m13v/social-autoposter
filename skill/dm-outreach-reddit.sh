#!/usr/bin/env bash
# dm-outreach-reddit.sh — Outbound Reddit DM outreach.
# Scans for DM candidates (users who engaged on our posts), then sends Reddit DMs
# to continue the conversation. Inbound DM replies are handled separately by
# engage-dm-replies-reddit.sh.
# Called by launchd (com.m13v.social-dm-outreach-reddit) every 6 hours.

set -euo pipefail

# Browser-profile lock first (shared with other reddit pipelines), then pipeline lock.
# Canonical order: platform-browser locks before pipeline-specific locks to avoid deadlock.
source "$(dirname "$0")/lock.sh"
acquire_lock "reddit-browser" 3600
acquire_lock "dm-outreach-reddit" 2700

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
LOG_FILE="$LOG_DIR/dm-outreach-reddit-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== Reddit DM Outreach Run: $(date) ==="

# Scan for new DM candidates first (cheap Python, writes to dms table)
log "Scanning for DM candidates (all platforms)..."
(PYTHONUNBUFFERED=1 python3 "$REPO_DIR/scripts/scan_dm_candidates.py" 2>&1 || true) | tee -a "$LOG_FILE"

DM_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE status='pending' AND platform='reddit';" 2>/dev/null || echo "0")

if [ "$DM_PENDING" -eq 0 ]; then
    log "No pending Reddit DMs"
    python3 "$REPO_DIR/scripts/log_run.py" --script "dm_outreach_reddit" --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed $(( $(date +%s) - RUN_START ))
    exit 0
fi

log "Reddit: $DM_PENDING DMs to send"

DM_DATA=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(q) FROM (
        SELECT d.id, d.platform, d.their_author, d.their_content, d.comment_context,
               d.target_project, d.prospect_id,
               r.their_comment_url, r.our_reply_content,
               p.thread_title, p.our_content as our_post_content
        FROM dms d
        JOIN replies r ON d.reply_id = r.id
        JOIN posts p ON d.post_id = p.id
        WHERE d.status='pending' AND d.platform='reddit'
        ORDER BY d.discovered_at ASC
    ) q;")

# Per-project qualification context for ICP pre-check
PROJECTS_QUALIFICATION=$(python3 -c "
import json
c = json.load(open('$REPO_DIR/config.json'))
for p in c.get('projects', []):
    q = p.get('qualification') or {}
    if not q:
        continue
    print(f\"- {p['name']}:\")
    if q.get('must_have'):
        print(f\"    must_have: {' ; '.join(q['must_have'])}\")
    if q.get('disqualify'):
        print(f\"    disqualify: {' ; '.join(q['disqualify'])}\")
" 2>/dev/null || echo "")

export CLAUDE_SESSION_ID=$(uuidgen | tr 'A-Z' 'a-z')

PROMPT_FILE=$(mktemp)
cat > "$PROMPT_FILE" <<PROMPT_EOF
You are the Social Autoposter Reddit DM outreach bot.

Read $SKILL_FILE for content rules (tone, anti-AI detection, no em dashes).

## Task: Send Reddit DMs to continue comment conversations

These users engaged with our Reddit posts/comments. We already replied publicly. Now send a short, casual DM (Reddit Chat) to continue the conversation.

CRITICAL RULES:
1. DMs must feel like a natural continuation of the comment discussion, NOT a cold outreach or sales pitch
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
- "Hey! I noticed your comment on Reddit. I'm building something you might find interesting..." (cold pitch)
- "Great point! I'd love to connect and share what we're working on." (generic)
- "Hi there - I saw your insightful comment about AI agents..." (too formal)

## Users to DM:
$DM_DATA

## Per-project ICP criteria (used for the pre-check step, NOT to skip sending):
$PROJECTS_QUALIFICATION

## Pre-send profile fetch + ICP pre-check (MANDATORY per DM, no filter)

For each DM row, BEFORE you compose or send, do this in order:

1. Look at the row's \`target_project\`. If it's NULL, skip the ICP evaluation (set icp_precheck=unknown with notes="no_target_project") and move to step 4 — but still fetch the profile if it's cheap.

2. Fetch the prospect's Reddit profile with mcp__reddit-agent__* tools:
   - Navigate to https://www.reddit.com/user/THEIR_AUTHOR/
   - browser_snapshot. Pull:
     - the profile bio/tagline (text under their name)
     - karma numbers (post + comment karma)
     - a 1-2 line summary of their most recent 3-5 posts/comments (titles + subreddits)
   - If the profile page is a login wall, deleted, or suspended user, record notes="profile_inaccessible" and proceed with icp_precheck=unknown.

3. Persist the profile fields via:
   \`\`\`bash
   python3 $REPO_DIR/scripts/fetch_prospect_profile.py upsert \\
       --platform reddit --author "THEIR_AUTHOR" \\
       --profile-url "https://www.reddit.com/user/THEIR_AUTHOR/" \\
       --headline "SHORT_TAGLINE_OR_BIO_FIRST_LINE" \\
       --bio "FULL_BIO_TEXT" \\
       --recent-activity "SHORT_3-5_ITEM_SUMMARY" \\
       --notes "ANY_SIGNAL_WORTH_REMEMBERING" \\
       --link-dm DM_ID
   \`\`\`
   Omit any flag whose value is empty or unknown. \`--link-dm\` also wires dms.prospect_id.

4. Evaluate ICP match: compare the profile + their_content + comment_context against the target_project's must_have (satisfy at least one) and disqualify (trigger ANY = fail). Pick one label: icp_match, icp_miss, disqualified, or unknown.
   \`\`\`bash
   python3 $REPO_DIR/scripts/dm_conversation.py set-icp-precheck \\
       --dm-id DM_ID --label LABEL --notes "SHORT_RATIONALE"
   \`\`\`

5. PROCEED TO SEND THE DM regardless of the ICP label. The label is informational; it does NOT gate outreach at this phase. Do not skip a DM because of \`icp_miss\` or \`disqualified\`.

## How to send DMs on Reddit (use mcp__reddit-agent__* tools):
1. Navigate to https://www.reddit.com/message/compose/?to=THEIR_AUTHOR
2. Reddit uses Chat now. Fill in subject (2-4 casual words) and body.
3. Submit and verify (form clears or chat appears).

## After each DM:

Success (BOTH steps required):
  psql "\$DATABASE_URL" -c "UPDATE dms SET status='sent', our_dm_content='DM_TEXT', sent_at=NOW(), claude_session_id='$CLAUDE_SESSION_ID'::uuid WHERE id=DM_ID;"
  python3 $REPO_DIR/scripts/dm_conversation.py log-outbound --dm-id DM_ID --content "DM_TEXT"

Failed (rate limit, blocked, error):
  psql "\$DATABASE_URL" -c "UPDATE dms SET status='error', skip_reason='REASON', claude_session_id='$CLAUDE_SESSION_ID'::uuid WHERE id=DM_ID;"

DMs/Chat disabled:
  psql "\$DATABASE_URL" -c "UPDATE dms SET status='skipped', skip_reason='chat_disabled', claude_session_id='$CLAUDE_SESSION_ID'::uuid WHERE id=DM_ID;"

CRITICAL: ALL browser calls MUST use mcp__reddit-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools. If a reddit-agent tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). Do NOT fall back to any other browser tool.
PROMPT_EOF

gtimeout 2700 "$REPO_DIR/scripts/run_claude.sh" "dm-outreach-reddit" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/reddit-agent-mcp.json" -p "$(cat "$PROMPT_FILE")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Reddit DM outreach claude exited with code $?"
rm -f "$PROMPT_FILE"

SENT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE platform='reddit' AND status='sent';" 2>/dev/null || echo "0")
STILL_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE platform='reddit' AND status='pending';" 2>/dev/null || echo "0")
log "Reddit DM outreach summary: sent (all-time)=$SENT, still_pending=$STILL_PENDING"

RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
python3 "$REPO_DIR/scripts/log_run.py" --script "dm_outreach_reddit" --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed "$RUN_ELAPSED"

find "$LOG_DIR" -name "dm-outreach-reddit-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== Reddit DM outreach complete: $(date) ==="
