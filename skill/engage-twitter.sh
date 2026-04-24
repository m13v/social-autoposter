#!/usr/bin/env bash
# engage-twitter.sh — X/Twitter engagement loop
# Phase A: Discover replies/mentions via Twitter API (no browser needed)
# Phase B: Respond to pending Twitter replies via browser (API can't reply to most tweets)
# Called by launchd every 3 hours.


set -euo pipefail

# Browser-profile lock first (shared with other twitter pipelines), then pipeline lock.
source "$(dirname "$0")/lock.sh"
acquire_lock "twitter-browser" 3600
acquire_lock "twitter" 3600

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
LOG_FILE="$LOG_DIR/engage-twitter-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== Twitter Engagement Run: $(date) ==="

# Load exclusions from config
EXCLUDED_AUTHORS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('authors',[])))" 2>/dev/null || echo "")
EXCLUDED_TWITTER=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('twitter_accounts',[])))" 2>/dev/null || echo "")

# ═══════════════════════════════════════════════════════
# PHASE A: Discover new replies/mentions from Twitter notifications
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning Twitter mentions via browser (no API cost)..."
NOTIFS_JSON=$(mktemp -t twitter_notifs.XXXXXX.json)
python3 "$REPO_DIR/scripts/twitter_browser.py" notifications 8 > "$NOTIFS_JSON" 2>>"$LOG_FILE" \
    || log "WARNING: twitter_browser.py notifications failed"
python3 "$REPO_DIR/scripts/scan_twitter_mentions_browser.py" --json-file "$NOTIFS_JSON" 2>&1 \
    | tee -a "$LOG_FILE" \
    || log "WARNING: Phase A scan_twitter_mentions_browser.py exited with code $?"
rm -f "$NOTIFS_JSON"

# ═══════════════════════════════════════════════════════
# PHASE B: Respond to pending Twitter replies
# ═══════════════════════════════════════════════════════

# Reset any 'processing' items older than 2 hours back to 'pending'
RESET_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    WITH upd AS (
        UPDATE replies SET status='pending'
        WHERE platform='x' AND status='processing' AND processing_at < NOW() - INTERVAL '2 hours'
        RETURNING id
    ) SELECT COUNT(*) FROM upd;")
[ "$RESET_COUNT" -gt 0 ] && log "Phase B: Reset $RESET_COUNT stuck 'processing' Twitter items back to pending"

PENDING_COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='x' AND status='pending';")

if [ "$PENDING_COUNT" -eq 0 ]; then
    log "Phase B: No pending Twitter replies. Done!"
else
    log "Phase B: $PENDING_COUNT pending Twitter replies to process"

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
            WHERE r.platform='x' AND r.status='pending'
            ORDER BY
                CASE WHEN p.thread_url = p.our_url THEN 0 ELSE 1 END,
                r.discovered_at ASC
            LIMIT $BATCH_SIZE
        ) q;")

    # Generate engagement style and content rules from shared module
    source "$REPO_DIR/skill/styles.sh"
    STYLES_BLOCK=$(generate_styles_block twitter replying)

    # Top performers feedback report (platform-wide)
    TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform twitter 2>/dev/null || echo "(top performers report unavailable)")

    PHASE_B_PROMPT=$(mktemp)
    cat > "$PHASE_B_PROMPT" <<PROMPT_EOF
You are the Social Autoposter Twitter/X engagement bot.

Read $SKILL_FILE for the full workflow, content rules, and platform details.

EXCLUSIONS - do NOT engage with these accounts (skip and mark as 'skipped' with reason 'excluded_author'):
- Excluded authors: $EXCLUDED_AUTHORS
- Excluded Twitter accounts: $EXCLUDED_TWITTER

CRITICAL - Reply posting: Use the CDP script to post replies. NEVER use mcp__twitter-agent__*, mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* browser tools for posting.
CRITICAL: If the CDP script fails, retry up to 3 times with 30 seconds between attempts. If still failing, skip that item and move on.

## Respond to pending Twitter/X replies ($PENDING_COUNT total)

### Priority order:
1. **Replies on our original posts** (is_our_original_post=1) - highest priority
2. **Direct questions** ("what tool", "how do you", "can you share")
3. **Everything else** - general engagement

### Tiered link strategy:
- **Tier 1 (default):** No link. Genuine engagement, expand topic.
- **Tier 2 (natural mention):** Conversation touches a topic matching a project in config. Recommend it casually as a tool you've come across.
- **Tier 3 (direct ask):** They ask for link/tool/source. Give it immediately.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better replies):
$TOP_REPORT

$STYLES_BLOCK

Here are the replies to process:
$PENDING_DATA

CRITICAL: Reply in the SAME LANGUAGE as the message you are responding to. Match the language exactly.
CRITICAL: Process EVERY reply. For each: either post a response and mark as 'replied', OR mark as 'skipped' with a skip_reason.

CRITICAL: For ALL database operations, use the reply_db.py helper (NOT raw psql):
  python3 $REPO_DIR/scripts/reply_db.py processing ID          # BEFORE posting
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
  Step 1: python3 reply_db.py processing ID      <- mark BEFORE posting
  Step 2: Post reply via CDP script:
          python3 $REPO_DIR/scripts/twitter_browser.py reply "TWEET_URL" "YOUR_REPLY_TEXT"
          Returns JSON with {ok: true, tweet_url, verified} on success.
          Use their_comment_url as TWEET_URL and your generated reply as YOUR_REPLY_TEXT.
          Extract tweet_url from the JSON response for Step 3.
  Step 3: python3 reply_db.py replied ID "reply text" REPLY_URL ENGAGEMENT_STYLE   <- mark AFTER success (e.g. critic, snarky_oneliner)
If Step 3 fails, the item stays 'processing' and will be reset to 'pending' on the next run.
If the tweet has been deleted or is unavailable, mark as 'skipped' with reason 'tweet_not_found'.

After every 10 replies, run: python3 $REPO_DIR/scripts/reply_db.py status
PROMPT_EOF

    gtimeout 5400 "$REPO_DIR/scripts/run_claude.sh" "engage-twitter-phaseB" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/twitter-agent-mcp.json" -p "$(cat "$PHASE_B_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase B claude exited with code $?"
    rm -f "$PHASE_B_PROMPT"
fi

# Reset any items left in 'processing' after subprocess exit
POST_RESET=$(psql "$DATABASE_URL" -t -A -c "
    WITH upd AS (
        UPDATE replies SET status='pending'
        WHERE platform='x' AND status='processing'
        RETURNING id
    ) SELECT COUNT(*) FROM upd;")
[ "$POST_RESET" -gt 0 ] && log "Post-run: Reset $POST_RESET 'processing' Twitter items back to pending"

# ═══════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════
TOTAL_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='x' AND status='pending';")
TOTAL_REPLIED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='x' AND status='replied';")
TOTAL_SKIPPED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='x' AND status='skipped';")

log "Twitter summary: pending=$TOTAL_PENDING replied=$TOTAL_REPLIED skipped=$TOTAL_SKIPPED"

# Log run to persistent monitor
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
python3 "$REPO_DIR/scripts/log_run.py" --script "engage_twitter" --posted "$TOTAL_REPLIED" --skipped "$TOTAL_SKIPPED" --failed 0 --cost 0 --elapsed "$RUN_ELAPSED"

# Delete old logs
find "$LOG_DIR" -name "engage-twitter-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== Twitter engagement complete: $(date) ==="
