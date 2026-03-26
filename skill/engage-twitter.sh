#!/usr/bin/env bash
# engage-twitter.sh — X/Twitter engagement loop
# Phase A: Discover replies/mentions from Twitter notifications (browser-based)
# Phase B: Respond to pending Twitter replies
# Called by launchd every 3 hours.

set -euo pipefail

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
log "Phase A: Scanning Twitter notifications for replies and mentions..."

PHASE_A_PROMPT=$(mktemp)
cat > "$PHASE_A_PROMPT" <<PROMPT_EOF
You are the Social Autoposter Twitter/X discovery bot.

Read $SKILL_FILE for content rules (tone, anti-AI detection, no em dashes).

## Task: Discover new Twitter/X replies and mentions

CRITICAL - Browser agent rule: ONLY use mcp__twitter-agent__* tools (e.g. mcp__twitter-agent__browser_navigate, mcp__twitter-agent__browser_snapshot, mcp__twitter-agent__browser_run_code). NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
CRITICAL: If a browser agent tool call is blocked or times out, DO NOT fall back to any other browser tool. Wait 30 seconds and retry the same agent. Repeat up to 3 times. If still blocked, skip that item and move on.

EXCLUSIONS - do NOT engage with these accounts:
- Excluded authors: $EXCLUDED_AUTHORS
- Excluded Twitter accounts: $EXCLUDED_TWITTER
- Skip replies from our own account (@m13v_).

### Step 1: Load existing Twitter reply IDs to avoid duplicates
\`\`\`bash
source ~/social-autoposter/.env
psql "\$DATABASE_URL" -t -A -c "SELECT their_comment_id FROM replies WHERE platform='x';"
\`\`\`
Save this list - skip any mention whose numeric tweet ID appears in it.

### Step 2: Load our Twitter posts for matching
\`\`\`bash
psql "\$DATABASE_URL" -t -A -c "SELECT id, our_url FROM posts WHERE platform='twitter' AND status='active';"
\`\`\`

### Step 3: Navigate to Twitter mentions
1. Navigate to https://x.com/notifications/mentions via the twitter-agent browser
2. Wait for page to load (3 seconds)
3. Scroll down several times to load more mentions

### Step 4: Extract mentions
For each mention visible on the page:
a. Identify the author and tweet content
b. Extract the numeric tweet ID from the tweet URL (x.com/username/status/NUMERIC_ID)
c. Check if already tracked (from Step 1 list)
d. Skip light acknowledgments ("thanks", single emoji, etc.) and our own replies

### Step 5: For each new mention, get context
a. Click into the tweet to see the full conversation thread
b. Identify what post/comment of ours they're replying to
c. Extract the reply content

### Step 6: Insert new replies into the database
For each new reply:
1. Find the matching post_id from Step 2 by matching URLs
2. If no matching post exists, create one:
   \`\`\`bash
   psql "\$DATABASE_URL" -c "INSERT INTO posts (platform, thread_url, thread_author, thread_title, our_url, our_content, our_account, status, posted_at) VALUES ('twitter', 'THREAD_URL', 'THREAD_AUTHOR', 'TWEET_TEXT_PREVIEW', 'OUR_TWEET_URL', 'OUR_TWEET_TEXT', 'm13v_', 'active', NOW()) RETURNING id;"
   \`\`\`
3. Insert the reply:
   \`\`\`bash
   psql "\$DATABASE_URL" -c "INSERT INTO replies (post_id, platform, their_comment_id, their_author, their_content, their_comment_url, depth, status) VALUES (POST_ID, 'x', 'NUMERIC_TWEET_ID', 'AUTHOR_HANDLE', 'TWEET_TEXT', 'FULL_TWEET_URL', 1, 'pending');"
   \`\`\`

CRITICAL - Twitter comment ID format: always store ONLY the numeric tweet ID as their_comment_id.
Extract it from the tweet URL: x.com/username/status/NUMERIC_ID -> store just NUMERIC_ID.
NEVER prefix with username or any other text (e.g. store '2030180353625948573', NOT 'TisDad_2030180353625948573').

After discovery, print a summary: how many new replies found, how many skipped (already tracked, excluded, etc.).
PROMPT_EOF

gtimeout 3600 claude -p "$(cat "$PHASE_A_PROMPT")" --max-turns 200 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase A claude exited with code $?"
rm -f "$PHASE_A_PROMPT"

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

CRITICAL - Browser agent rule: ONLY use mcp__twitter-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
CRITICAL: If a browser agent tool call is blocked or times out, DO NOT fall back to any other browser tool. Wait 30 seconds and retry the same agent. Repeat up to 3 times. If still blocked, skip that item and move on.

## Respond to pending Twitter/X replies ($PENDING_COUNT total)

### Priority order:
1. **Replies on our original posts** (is_our_original_post=1) - highest priority
2. **Direct questions** ("what tool", "how do you", "can you share")
3. **Everything else** - general engagement

### Tiered link strategy:
- **Tier 1 (default):** No link. Genuine engagement, expand topic.
- **Tier 2 (natural mention):** Conversation touches something we build. Mention casually.
- **Tier 3 (direct ask):** They ask for link/tool/source. Give it immediately.

### Reply archetypes — MUST rotate, never use the same type twice in a row:
- **Short affirm** (1 sentence): "love this framing" / "this is underrated" — no product tie-in
- **Pure question** (1-2 sentences): Ask something genuine. Don't mention our work at all.
- **Respectful pushback**: Disagree or add nuance. "I've actually seen the opposite..."
- **Story/anecdote**: Share a specific experience WITHOUT tying back to our product.
- **Builder reply**: The current default — relate to our work. Use for MAX 30% of replies.

### Anti-pattern rules:
- NEVER start with "exactly", "yeah totally", "100%", "that's smart". Vary first words.
- NEVER bridge every reply to "we built" / "on the macOS side" / "accessibility APIs". Most replies should NOT mention our product.
- Some replies should be 1 sentence. Not everything needs 3-4 sentences.

Here are the replies to process:
$PENDING_DATA

CRITICAL: Process EVERY reply. For each: either post a response and mark as 'replied', OR mark as 'skipped' with a skip_reason.

CRITICAL: For ALL database operations, use the reply_db.py helper (NOT raw psql):
  python3 $REPO_DIR/scripts/reply_db.py processing ID          # BEFORE browser action
  python3 $REPO_DIR/scripts/reply_db.py replied ID "reply text" [url]   # AFTER posting
  python3 $REPO_DIR/scripts/reply_db.py skipped ID "reason"
  python3 $REPO_DIR/scripts/reply_db.py skip_batch '{"ids":[1,2,3],"reason":"..."}'
  python3 $REPO_DIR/scripts/reply_db.py status
NEVER use psql directly for reply status updates.

MANDATORY reply flow for every item:
  Step 1: python3 reply_db.py processing ID      <- mark BEFORE touching browser
  Step 2: post reply via browser
  Step 3: python3 reply_db.py replied ID "text" [url]   <- mark AFTER success
If Step 3 fails, the item stays 'processing' and will be reset to 'pending' on the next run.

For Twitter/X replies - use the twitter-agent browser (mcp__twitter-agent__* tools):
1. Navigate to the tweet URL (their_comment_url).
2. Take a snapshot to find the reply box.
3. Click the reply box, type the reply text, and submit.
4. After posting, capture the URL of our reply tweet.
5. If the tweet has been deleted or is unavailable, mark as 'skipped' with reason 'tweet_not_found'.

After every 10 replies, run: python3 $REPO_DIR/scripts/reply_db.py status
PROMPT_EOF

    gtimeout 5400 claude -p "$(cat "$PHASE_B_PROMPT")" --max-turns 500 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase B claude exited with code $?"
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
