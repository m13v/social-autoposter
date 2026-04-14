#!/usr/bin/env bash
# engage-linkedin.sh — LinkedIn engagement loop
# Phase A: Discover replies/mentions from LinkedIn notifications (browser-based)
# Phase B: Respond to pending LinkedIn replies
# Called by launchd every 3 hours.


set -euo pipefail

# Platform lock: wait up to 60min for previous linkedin run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "linkedin" 3600

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
BATCH_SIZE=500

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/engage-linkedin-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== LinkedIn Engagement Run: $(date) ==="

# Auth health check: verify LinkedIn session is valid, re-auth if needed
log "Checking LinkedIn auth..."
AUTH_EXIT=0
python3 "$REPO_DIR/scripts/linkedin_auth_check.py" 2>&1 | tee -a "$LOG_FILE" || AUTH_EXIT=$?
if [ "$AUTH_EXIT" -eq 1 ]; then
    log "ERROR: LinkedIn auth check failed and self-healing could not recover. Skipping run."
    python3 "$REPO_DIR/scripts/log_run.py" --script "engage_linkedin" --posted 0 --skipped 0 --failed 1 --cost 0 --elapsed $(( $(date +%s) - RUN_START ))
    exit 1
elif [ "$AUTH_EXIT" -eq 2 ]; then
    log "LinkedIn session was stale, successfully re-authenticated."
fi

# Load exclusions from config
EXCLUDED_AUTHORS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('authors',[])))" 2>/dev/null || echo "")
EXCLUDED_LINKEDIN=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('linkedin_profiles',[])))" 2>/dev/null || echo "")

# ═══════════════════════════════════════════════════════
# PHASE A: Discover new replies/mentions from LinkedIn notifications
# Uses linkedin-agent browser JS + Python script (no Claude LLM needed)
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning LinkedIn notifications via API (no LLM needed)..."

# Step 1: Extract notifications via Python Playwright (no Claude LLM needed)
# Connects to the running linkedin-agent browser via CDP
NOTIF_JSON=$(mktemp)
python3 "$REPO_DIR/scripts/linkedin_browser.py" notifications > "$NOTIF_JSON" 2>/dev/null

# Wrap array in {notifications: [...]} format for scan_linkedin_notifications.py
python3 -c "
import json, sys
try:
    notifs = json.load(open('$NOTIF_JSON'))
    if isinstance(notifs, list):
        json.dump({'notifications': notifs}, open('$NOTIF_JSON', 'w'))
except Exception as e:
    json.dump({'error': str(e)}, open('$NOTIF_JSON', 'w'))
" 2>/dev/null

# Step 2: Process notifications and insert into DB
python3 "$REPO_DIR/scripts/scan_linkedin_notifications.py" --json-file "$NOTIF_JSON" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase A scan_linkedin_notifications.py exited with code $?"
rm -f "$NOTIF_JSON"

# ═══════════════════════════════════════════════════════
# PHASE B: Respond to pending LinkedIn replies
# ═══════════════════════════════════════════════════════

# Reset any 'processing' items older than 2 hours back to 'pending'
RESET_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    UPDATE replies SET status='pending'
    WHERE platform='linkedin' AND status='processing' AND processing_at < NOW() - INTERVAL '2 hours'
    RETURNING id;" | wc -l | tr -d ' ')
[ "$RESET_COUNT" -gt 0 ] && log "Phase B: Reset $RESET_COUNT stuck 'processing' LinkedIn items back to pending"

PENDING_COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='linkedin' AND status='pending';")

if [ "$PENDING_COUNT" -eq 0 ]; then
    log "Phase B: No pending LinkedIn replies. Done!"
else
    log "Phase B: $PENDING_COUNT pending LinkedIn replies to process"

    PENDING_DATA=$(psql "$DATABASE_URL" -t -A -c "
        SELECT json_agg(q) FROM (
            SELECT r.id, r.platform, r.their_author,
                   LEFT(r.their_content, 300) as their_content,
                   r.their_comment_url, r.their_comment_id, r.depth,
                   LEFT(p.thread_title, 100) as thread_title,
                   p.thread_url, LEFT(p.our_content, 200) as our_content, p.our_url,
                   CASE WHEN p.thread_url = p.our_url THEN 1 ELSE 0 END as is_our_original_post
            FROM replies r
            JOIN posts p ON r.post_id = p.id
            WHERE r.platform='linkedin' AND r.status='pending'
            ORDER BY
                CASE WHEN p.thread_url = p.our_url THEN 0 ELSE 1 END,
                r.discovered_at ASC
            LIMIT $BATCH_SIZE
        ) q;")

    # Generate engagement style and content rules from shared module
    source "$REPO_DIR/skill/styles.sh"
    STYLES_BLOCK=$(generate_styles_block linkedin replying)

    PHASE_B_PROMPT=$(mktemp)
    cat > "$PHASE_B_PROMPT" <<PROMPT_EOF
You are the Social Autoposter LinkedIn engagement bot.

Read $SKILL_FILE for the full workflow, content rules, and platform details.

EXCLUSIONS - do NOT engage with these accounts (skip and mark as 'skipped' with reason 'excluded_author'):
- Excluded authors: $EXCLUDED_AUTHORS
- Excluded LinkedIn profiles: $EXCLUDED_LINKEDIN

CRITICAL - Browser agent rule: ONLY use mcp__linkedin-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
CRITICAL: If a browser agent tool call is blocked or times out, DO NOT fall back to any other browser tool. Wait 30 seconds and retry the same agent. Repeat up to 3 times. If still blocked, skip that item and move on.

## Respond to pending LinkedIn replies ($PENDING_COUNT total)

### Priority order:
1. **Replies on our original posts** (is_our_original_post=1) - highest priority
2. **Direct questions** ("what tool", "how do you", "can you share")
3. **Everything else** - general engagement

### Tiered link strategy:
- **Tier 1 (default):** No link. Genuine engagement, expand topic.
- **Tier 2 (natural mention):** Conversation touches a topic matching a project in config. Recommend it casually as a tool you've come across.
- **Tier 3 (direct ask):** They ask for link/tool/source. Give it immediately.

$STYLES_BLOCK

Here are the replies to process:
$PENDING_DATA

CRITICAL: Reply in the SAME LANGUAGE as the message you are responding to. Match the language exactly.
CRITICAL: Process EVERY reply. For each: either post a response and mark as 'replied', OR mark as 'skipped' with a skip_reason.

CRITICAL: For ALL database operations, use the reply_db.py helper (NOT raw psql):
  python3 $REPO_DIR/scripts/reply_db.py processing ID          # BEFORE browser action
  python3 $REPO_DIR/scripts/reply_db.py replied ID "reply text" [url] [engagement_style]   # AFTER posting (include the style name)
  python3 $REPO_DIR/scripts/reply_db.py skipped ID "reason"
  python3 $REPO_DIR/scripts/reply_db.py skip_batch '{"ids":[1,2,3],"reason":"..."}'
  python3 $REPO_DIR/scripts/reply_db.py status
NEVER use psql directly for reply status updates.

### Project tracking on replies
When you recommend a project in a reply (Tier 2 or Tier 3), set project_name on the reply:
  source ~/social-autoposter/.env
  psql "\$DATABASE_URL" -c "UPDATE replies SET project_name='PROJECT_NAME' WHERE id=REPLY_ID;"
This lets the DM pipeline know which project the conversation is about.

MANDATORY reply flow for every item:
  Step 1: python3 reply_db.py processing ID      <- mark BEFORE touching browser
  Step 2: post reply via browser
  Step 3: python3 reply_db.py replied ID "text" [url] [engagement_style]   <- mark AFTER success (e.g. storyteller, pattern_recognizer)
If Step 3 fails, the item stays 'processing' and will be reset to 'pending' on the next run.

For LinkedIn replies - use the LinkedIn API (NOT browser) to post replies:
1. Extract the activity ID from their_comment_url or their_comment_id.
   - From their_comment_id like \`urn:li:comment:(activity:7438226125077549056,7438815640536170496)\`, the activity ID is \`7438226125077549056\` and the full URN is the parent_comment_urn.
   - From their_comment_url, extract the activity ID from the URL path.
2. Post the reply via API:
   \`\`\`bash
   python3 $REPO_DIR/scripts/linkedin_api.py reply ACTIVITY_ID "PARENT_COMMENT_URN" "YOUR REPLY TEXT"
   \`\`\`
   This returns JSON with {ok, reply_urn, permalink}. Use permalink as the reply URL.
3. If the API call fails (e.g., token expired, comment deleted), fall back to browser:
   - Navigate to their_comment_url via linkedin-agent browser
   - Find the comment, click Reply, type, submit
4. If both API and browser fail, mark as 'skipped' with reason 'comment_not_found'.

After every 10 replies, run: python3 $REPO_DIR/scripts/reply_db.py status
PROMPT_EOF

    gtimeout 5400 claude --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json" -p "$(cat "$PHASE_B_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase B claude exited with code $?"
    rm -f "$PHASE_B_PROMPT"
fi

# ═══════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════
TOTAL_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='linkedin' AND status='pending';")
TOTAL_REPLIED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='linkedin' AND status='replied';")
TOTAL_SKIPPED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='linkedin' AND status='skipped';")

log "LinkedIn summary: pending=$TOTAL_PENDING replied=$TOTAL_REPLIED skipped=$TOTAL_SKIPPED"

# Log run to persistent monitor
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
python3 "$REPO_DIR/scripts/log_run.py" --script "engage_linkedin" --posted "$TOTAL_REPLIED" --skipped "$TOTAL_SKIPPED" --failed 0 --cost 0 --elapsed "$RUN_ELAPSED"

# Delete old logs
find "$LOG_DIR" -name "engage-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== LinkedIn engagement complete: $(date) ==="
