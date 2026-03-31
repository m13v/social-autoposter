#!/usr/bin/env bash
# engage-linkedin.sh — LinkedIn engagement loop
# Phase A: Discover replies/mentions from LinkedIn notifications (browser-based)
# Phase B: Respond to pending LinkedIn replies
# Called by launchd every 3 hours.

set -euo pipefail

# Platform lock: wait up to 60min for previous linkedin run to finish, then skip
LOCK_FILE="/tmp/social-autoposter-linkedin.lock"
exec 200>"$LOCK_FILE"
flock -w 3600 200 || { echo "Previous linkedin run still active after 60min, skipping"; exit 0; }

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

log "=== LinkedIn Engagement Run: $(date) ==="

# Load exclusions from config
EXCLUDED_AUTHORS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('authors',[])))" 2>/dev/null || echo "")
EXCLUDED_LINKEDIN=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('linkedin_profiles',[])))" 2>/dev/null || echo "")

# ═══════════════════════════════════════════════════════
# PHASE A: Discover new replies/mentions from LinkedIn notifications
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning LinkedIn notifications for replies and mentions..."

PHASE_A_PROMPT=$(mktemp)
cat > "$PHASE_A_PROMPT" <<PROMPT_EOF
You are the Social Autoposter LinkedIn discovery bot.

Read $SKILL_FILE for content rules (tone, anti-AI detection, no em dashes).

## Task: Discover new LinkedIn replies and mentions

CRITICAL - Browser agent rule: ONLY use mcp__linkedin-agent__* tools (e.g. mcp__linkedin-agent__browser_navigate, mcp__linkedin-agent__browser_snapshot, mcp__linkedin-agent__browser_run_code). NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
CRITICAL: If a browser agent tool call is blocked or times out, DO NOT fall back to any other browser tool. Wait 30 seconds and retry the same agent. Repeat up to 3 times. If still blocked, skip that item and move on.

EXCLUSIONS - do NOT engage with these accounts:
- Excluded authors: $EXCLUDED_AUTHORS
- Excluded LinkedIn profiles: $EXCLUDED_LINKEDIN
- Skip comments by "Matthew Diakonov" or "m13v" (our own account).

### Step 1: Load existing LinkedIn reply IDs to avoid duplicates
\`\`\`bash
source ~/social-autoposter/.env
psql "\$DATABASE_URL" -t -A -c "SELECT their_comment_id FROM replies WHERE platform='linkedin';"
\`\`\`
Save this list - skip any notification whose comment URN is already tracked.

### Step 2: Load our LinkedIn posts for matching
\`\`\`bash
psql "\$DATABASE_URL" -t -A -c "SELECT id, our_url FROM posts WHERE platform='linkedin' AND status='active';"
\`\`\`

### Step 3: Navigate to LinkedIn notifications
1. Navigate to https://www.linkedin.com/notifications/ using the linkedin-agent browser
2. Wait for the page to load (3 seconds)
3. Click "Show more results" button repeatedly (up to 10 times) to load more notifications
4. After loading, scroll to load any remaining content

### Step 4: Extract actionable notifications
Use browser_run_code to extract all actionable notifications:

\`\`\`javascript
async (page) => {
  const articles = document.querySelectorAll('article');
  const actionable = [];
  articles.forEach(a => {
    const text = a.innerText || '';
    let type = null;
    if (text.includes('replied to your comment')) type = 'reply';
    else if (text.includes('mentioned you in a comment')) type = 'mention';
    else if (text.includes('commented on your post')) type = 'comment_on_post';
    if (!type) return;

    const strong = a.querySelector('strong');
    const name = strong ? strong.textContent.trim() : 'unknown';
    const link = a.querySelector('a[href*="commentUrn"]') || a.querySelector('a[href*="replyUrn"]') || a.querySelector('a[href*="feed/update"]');
    const url = link ? link.getAttribute('href') : null;
    actionable.push({ type, name, url });
  });
  return JSON.stringify(actionable);
}
\`\`\`

SKIP these notification types (they are not engageable):
- Profile views, search appearances, impressions/analytics
- Job changes, birthdays, work anniversaries
- "X was live", "X posted", suggested posts
- New followers
- Likes on comments (no reply needed)
- Scheduled post confirmations

### Step 5: For each actionable notification, get full context
For each notification URL:
a. Navigate to the post/comment URL via linkedin-agent browser
b. Find the specific comment using JavaScript:
   \`\`\`javascript
   async (page) => {
     const comments = [];
     document.querySelectorAll('article.comments-comment-entity, [data-id*="urn:li:comment"]').forEach(el => {
       const dataId = el.getAttribute('data-id');
       const author = el.querySelector('.comments-post-meta__name-text')?.innerText?.trim();
       const content = el.querySelector('.comments-comment-item__main-content')?.innerText?.trim();
       if (dataId && author && content) comments.push({dataId, author, content: content.substring(0, 500)});
     });
     return JSON.stringify(comments);
   }
   \`\`\`
c. Extract the comment URN from data-id attribute

### Step 6: Insert new replies into the database
For each new reply (not already in the existing list from Step 1):

1. Find the matching post_id from Step 2 by matching the activity URL
2. If no matching post exists, create one (determine PROJECT_NAME by matching the post topic against config.json projects[].topics).
   IMPORTANT: The our_url MUST be a linkedin.com/feed/update/ URL (the activity URL of the post we commented on).
   Extract it from the notification URL or from the post page. If the URL contains an activity ID (e.g. in commentUrn), construct:
   https://www.linkedin.com/feed/update/urn:li:activity:ACTIVITY_ID/
   NEVER use profile URLs (/in/...), search URLs (/search/...), or empty strings for our_url.
   \`\`\`bash
   psql "\$DATABASE_URL" -c "INSERT INTO posts (platform, thread_url, thread_author, thread_title, our_url, our_content, our_account, project_name, status, posted_at) VALUES ('linkedin', 'THREAD_URL', 'THREAD_AUTHOR', 'THREAD_TITLE', 'FEED_UPDATE_URL', 'OUR_POST_CONTENT', 'm13v', 'PROJECT_NAME', 'active', NOW()) RETURNING id;"
   \`\`\`
3. Insert the reply:
   \`\`\`bash
   psql "\$DATABASE_URL" -c "INSERT INTO replies (post_id, platform, their_comment_id, their_author, their_content, their_comment_url, depth, status) VALUES (POST_ID, 'linkedin', 'URN_FROM_DATA_ID', 'AUTHOR_NAME', 'COMMENT_TEXT', 'COMMENT_PERMALINK_URL', 1, 'pending');"
   \`\`\`

CRITICAL - LinkedIn comment ID format: always store the FULL URN from the data-id attribute as their_comment_id.
Format: urn:li:comment:(activity:ACTIVITY_ID,COMMENT_ID)
Example: urn:li:comment:(activity:7438226125077549056,7438815640536170496)

CRITICAL - LinkedIn comment permalink URL format:
https://www.linkedin.com/feed/update/urn:li:activity:ACTIVITY_ID?commentUrn=urn%3Ali%3Acomment%3A%28activity%3AACTIVITY_ID%2CCOMMENT_ID%29

After discovery, print a summary: how many new replies found, how many skipped (already tracked, excluded, etc.).
PROMPT_EOF

gtimeout 3600 claude -p "$(cat "$PHASE_A_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase A claude exited with code $?"
rm -f "$PHASE_A_PROMPT"

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

For LinkedIn replies - use the linkedin-agent browser (mcp__linkedin-agent__* tools):
1. Navigate to their_comment_url (the post with commentUrn query param).
2. Find the specific comment in the page. Take a snapshot to locate it.
3. DEDUP CHECK: Before replying, check if we already have a reply on this comment. Use browser_run_code:
   \`\`\`javascript
   async (page) => {
     // Find all reply authors under the target comment thread
     const replies = document.querySelectorAll('.comments-comment-entity .comments-post-meta__name-text, .comments-reply-item .comments-post-meta__name-text');
     const authors = [...replies].map(el => el.innerText.trim().toLowerCase());
     const alreadyReplied = authors.some(a => a.includes('matthew diakonov') || a.includes('m13v'));
     return JSON.stringify({ alreadyReplied, authors });
   }
   \`\`\`
   If alreadyReplied is true, mark as 'skipped' with reason 'already_replied' and move on.
4. Click "Reply" on that comment, type the reply text, and submit.
5. After posting, construct the permalink URL for our reply and store it.
6. If you can't find the comment or it's been deleted, mark as 'skipped' with reason 'comment_not_found'.

After every 10 replies, run: python3 $REPO_DIR/scripts/reply_db.py status
PROMPT_EOF

    gtimeout 5400 claude -p "$(cat "$PHASE_B_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase B claude exited with code $?"
    rm -f "$PHASE_B_PROMPT"
fi

# ═══════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════
TOTAL_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='linkedin' AND status='pending';")
TOTAL_REPLIED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='linkedin' AND status='replied';")
TOTAL_SKIPPED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='linkedin' AND status='skipped';")

log "LinkedIn summary: pending=$TOTAL_PENDING replied=$TOTAL_REPLIED skipped=$TOTAL_SKIPPED"

# Delete old logs
find "$LOG_DIR" -name "engage-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== LinkedIn engagement complete: $(date) ==="
