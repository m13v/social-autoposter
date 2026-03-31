#!/usr/bin/env bash
# engage-twitter.sh — X/Twitter engagement loop
# Phase A: Discover replies/mentions from Twitter notifications (browser-based)
# Phase B: Respond to pending Twitter replies
# Called by launchd every 3 hours.

set -euo pipefail

# Platform lock: wait up to 60min for previous twitter run to finish, then skip
source "$(dirname "$0")/lock.sh"
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

log "=== Twitter Engagement Run: $(date) ==="

# Load exclusions from config
EXCLUDED_AUTHORS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('authors',[])))" 2>/dev/null || echo "")
EXCLUDED_TWITTER=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('twitter_accounts',[])))" 2>/dev/null || echo "")

# ═══════════════════════════════════════════════════════
# PHASE A: Discover new replies/mentions from Twitter notifications
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning Twitter mentions via API (no browser needed)..."
python3 "$REPO_DIR/scripts/scan_twitter_mentions.py" --max 100 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase A scan_twitter_mentions.py exited with code $?"

# ═══════════════════════════════════════════════════════
# PHASE B: Respond to pending Twitter replies
# ═══════════════════════════════════════════════════════

# Reset any 'processing' items older than 2 hours back to 'pending'
RESET_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    UPDATE replies SET status='pending'
    WHERE platform='x' AND status='processing' AND processing_at < NOW() - INTERVAL '2 hours'
    RETURNING id;" | wc -l | tr -d ' ')
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

    PHASE_B_PROMPT=$(mktemp)
    cat > "$PHASE_B_PROMPT" <<PROMPT_EOF
You are the Social Autoposter Twitter/X engagement bot.

Read $SKILL_FILE for the full workflow, content rules, and platform details.

EXCLUSIONS - do NOT engage with these accounts (skip and mark as 'skipped' with reason 'excluded_author'):
- Excluded authors: $EXCLUDED_AUTHORS
- Excluded Twitter accounts: $EXCLUDED_TWITTER

CRITICAL: Post replies using python3 $REPO_DIR/scripts/twitter_api.py, NOT browser tools. The Twitter API handles all posting.

## Respond to pending Twitter/X replies ($PENDING_COUNT total)

### Priority order:
1. **Replies on our original posts** (is_our_original_post=1) - highest priority
2. **Direct questions** ("what tool", "how do you", "can you share")
3. **Everything else** - general engagement

### Tiered link strategy:
- **Tier 1 (default):** No link. Genuine engagement, expand topic.
- **Tier 2 (natural mention):** Conversation touches a topic matching a project in config. Recommend it casually as a tool you've come across.
- **Tier 3 (direct ask):** They ask for link/tool/source. Give it immediately.

### Reply archetypes — MUST rotate, never use the same type twice in a row:
- **Short affirm** (1 sentence): "love this framing" / "this is underrated" — no product tie-in
- **Pure question** (1-2 sentences): Ask something genuine. Don't mention any tools at all.
- **Respectful pushback**: Disagree or add nuance. "I've actually seen the opposite..."
- **Story/anecdote**: Share a specific experience WITHOUT tying back to any product.
- **Recommendation reply**: Recommend a relevant project from config as something you've used or come across. Use for MAX 30% of replies.

### Anti-pattern rules:
- NEVER start with "exactly", "yeah totally", "100%", "that's smart". Vary first words.
- NEVER say "I built" / "we built" / "I'm working on". Frame products as recommendations, not self-promotion. Most replies should NOT mention any product.
- Some replies should be 1 sentence. Not everything needs 3-4 sentences.

Here are the replies to process:
$PENDING_DATA

CRITICAL: Process EVERY reply. For each: either post a response and mark as 'replied', OR mark as 'skipped' with a skip_reason.

CRITICAL: For ALL database operations, use the reply_db.py helper (NOT raw psql):
  python3 $REPO_DIR/scripts/reply_db.py processing ID          # BEFORE posting
  python3 $REPO_DIR/scripts/reply_db.py replied ID "reply text" [url]   # AFTER posting
  python3 $REPO_DIR/scripts/reply_db.py skipped ID "reason"
  python3 $REPO_DIR/scripts/reply_db.py skip_batch '{"ids":[1,2,3],"reason":"..."}'
  python3 $REPO_DIR/scripts/reply_db.py status
NEVER use psql directly for reply status updates.

MANDATORY reply flow for every item:
  Step 1: python3 reply_db.py processing ID      <- mark BEFORE posting
  Step 2: Post reply via Twitter API (NOT browser):
          python3 $REPO_DIR/scripts/twitter_api.py post "REPLY_TEXT" --reply-to THEIR_TWEET_ID
          This returns JSON with the new tweet's id and url.
  Step 3: python3 reply_db.py replied ID "reply text" REPLY_URL   <- mark AFTER success
If Step 2 fails with an error, mark as 'skipped' with reason 'api_error'.
If their tweet was deleted (404), mark as 'skipped' with reason 'tweet_not_found'.

CRITICAL: Post replies using python3 $REPO_DIR/scripts/twitter_api.py, NOT browser tools. Browser is NOT needed.

After every 10 replies, run: python3 $REPO_DIR/scripts/reply_db.py status
PROMPT_EOF

    gtimeout 5400 claude -p "$(cat "$PHASE_B_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase B claude exited with code $?"
    rm -f "$PHASE_B_PROMPT"
fi

# ═══════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════
TOTAL_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='x' AND status='pending';")
TOTAL_REPLIED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='x' AND status='replied';")
TOTAL_SKIPPED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='x' AND status='skipped';")

log "Twitter summary: pending=$TOTAL_PENDING replied=$TOTAL_REPLIED skipped=$TOTAL_SKIPPED"

# Delete old logs
find "$LOG_DIR" -name "engage-twitter-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== Twitter engagement complete: $(date) ==="
