#!/usr/bin/env bash
# engage.sh — Reply engagement loop: discover replies to our comments, draft responses, post them
# Phase A: Scan Reddit JSON for new replies (Python3, no Claude needed)
# Phase A.5: Scan X/Twitter notifications for new replies (Claude + Playwright)
# Phase B: Claude drafts and posts up to 5 replies (Playwright for Reddit)
# Phase C: Cleanup — git sync, log rotation
# Called by launchd every 2 hours

set -euo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

DB="$HOME/social-autoposter/social_posts.db"
LOG_DIR="$HOME/.claude/skills/social-autoposter/logs"
SKILL_FILE="$HOME/.claude/skills/social-autoposter/SKILL.md"
OUR_REDDIT_ACCOUNT="Deep_Ad1959"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/engage-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== Engagement Loop Run: $(date) ==="

# ─── DB Migration: Create replies table if missing ───
sqlite3 "$DB" "CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER REFERENCES posts(id),
    platform TEXT NOT NULL,
    their_comment_id TEXT NOT NULL,
    their_author TEXT,
    their_content TEXT,
    their_comment_url TEXT,
    our_reply_id TEXT,
    our_reply_content TEXT,
    our_reply_url TEXT,
    parent_reply_id INTEGER REFERENCES replies(id),
    moltbook_post_uuid TEXT,
    moltbook_parent_comment_uuid TEXT,
    depth INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','replied','skipped','error')),
    skip_reason TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    replied_at TIMESTAMP
);"

# ═══════════════════════════════════════════════════════
# PHASE A: Scan for replies (Python3, no Claude needed)
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning for replies..."

python3 - "$DB" "$OUR_REDDIT_ACCOUNT" <<'PYTHON_SCAN' 2>&1 | tee -a "$LOG_FILE"
import sys, json, sqlite3, urllib.request, time, re
from datetime import datetime, timezone, timedelta

DB_PATH = sys.argv[1]
OUR_REDDIT = sys.argv[2]

STALENESS_DAYS = 7
MIN_WORDS = 5
SKIP_AUTHORS = {"AutoModerator", "[deleted]", OUR_REDDIT}
USER_AGENT = "social-engage/1.0 (u/Deep_Ad1959)"

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

discovered = 0
skipped = 0
errors = 0

def word_count(text):
    return len(text.split()) if text else 0

def is_too_old(created_utc):
    if not created_utc:
        return False
    try:
        comment_time = datetime.fromtimestamp(float(created_utc), tz=timezone.utc)
        return (datetime.now(timezone.utc) - comment_time) > timedelta(days=STALENESS_DAYS)
    except (ValueError, TypeError):
        return False

def already_tracked(platform, comment_id):
    row = db.execute(
        "SELECT COUNT(*) FROM replies WHERE platform=? AND their_comment_id=?",
        (platform, str(comment_id))
    ).fetchone()
    return row[0] > 0

def insert_reply(post_id, platform, comment_id, author, content, comment_url,
                 parent_reply_id=None, depth=1, status='pending', skip_reason=None,
                 moltbook_post_uuid=None, moltbook_parent_comment_uuid=None):
    global discovered, skipped
    comment_id = str(comment_id)

    if already_tracked(platform, comment_id):
        return

    db.execute("""INSERT INTO replies
        (post_id, platform, their_comment_id, their_author, their_content, their_comment_url,
         parent_reply_id, depth, status, skip_reason, moltbook_post_uuid, moltbook_parent_comment_uuid)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (post_id, platform, comment_id, author, content, comment_url,
         parent_reply_id, depth, status, skip_reason, moltbook_post_uuid, moltbook_parent_comment_uuid))

    if status == 'pending':
        discovered += 1
    else:
        skipped += 1

def fetch_json(url, headers=None):
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}")
        return None

def process_reddit_replies(children, post_id, parent_reply_id=None, depth=1):
    """Process a list of Reddit comment children, filtering and inserting."""
    for child in children:
        if child.get('kind') != 't1':
            continue
        cdata = child.get('data', {})
        author = cdata.get('author', '')
        body = cdata.get('body', '')
        comment_id = cdata.get('id', '')
        created = cdata.get('created_utc')
        permalink = cdata.get('permalink', '')
        comment_url = f"https://old.reddit.com{permalink}" if permalink else ""

        # Determine status
        if author in SKIP_AUTHORS:
            insert_reply(post_id, 'reddit', comment_id, author, body, comment_url,
                        parent_reply_id=parent_reply_id, depth=depth,
                        status='skipped', skip_reason='filtered_author')
            continue
        if body in ('[deleted]', '[removed]'):
            insert_reply(post_id, 'reddit', comment_id, author, body, comment_url,
                        parent_reply_id=parent_reply_id, depth=depth,
                        status='skipped', skip_reason='deleted')
            continue
        if word_count(body) < MIN_WORDS:
            insert_reply(post_id, 'reddit', comment_id, author, body, comment_url,
                        parent_reply_id=parent_reply_id, depth=depth,
                        status='skipped', skip_reason=f'too_short ({word_count(body)} words)')
            continue
        if is_too_old(created):
            insert_reply(post_id, 'reddit', comment_id, author, body, comment_url,
                        parent_reply_id=parent_reply_id, depth=depth,
                        status='skipped', skip_reason='too_old')
            continue

        insert_reply(post_id, 'reddit', comment_id, author, body, comment_url,
                    parent_reply_id=parent_reply_id, depth=depth)
        print(f"  NEW (depth {depth}): [{post_id}] u/{author}: {body[:80]}...")


# ─── Reddit: Scan replies to our comments (depth 1) ───
print("Scanning Reddit posts for replies...")
reddit_posts = db.execute(
    "SELECT id, our_url, thread_title FROM posts "
    "WHERE platform='reddit' AND status='active' AND our_url IS NOT NULL"
).fetchall()

for post in reddit_posts:
    post_id = post['id']
    our_url = post['our_url']

    # Normalize to old.reddit.com and append .json
    json_url = re.sub(r'www\.reddit\.com', 'old.reddit.com', our_url).rstrip('/') + '.json'

    data = fetch_json(json_url)
    if not data or not isinstance(data, list) or len(data) < 2:
        errors += 1
        continue

    # Our comment is .[1].data.children[0].data
    children = data[1].get('data', {}).get('children', [])
    if not children:
        continue

    our_comment = children[0].get('data', {})
    replies_obj = our_comment.get('replies')

    if not replies_obj or not isinstance(replies_obj, dict):
        continue

    reply_children = replies_obj.get('data', {}).get('children', [])
    process_reddit_replies(reply_children, post_id, parent_reply_id=None, depth=1)

    time.sleep(1)  # Rate limit


# ─── Level N: Scan replies to our previous replies (infinite depth BFS) ───
print("\nLevel N: Scanning replies to our previous replies...")
replied_rows = db.execute(
    "SELECT id, platform, our_reply_url, post_id, depth "
    "FROM replies WHERE status='replied' AND our_reply_url IS NOT NULL"
).fetchall()

for row in replied_rows:
    reply_id = row['id']
    platform = row['platform']
    our_reply_url = row['our_reply_url']
    post_id = row['post_id']
    current_depth = row['depth']

    if platform == 'reddit':
        json_url = re.sub(r'www\.reddit\.com', 'old.reddit.com', our_reply_url).rstrip('/') + '.json'
        data = fetch_json(json_url)
        if not data or not isinstance(data, list) or len(data) < 2:
            continue

        children = data[1].get('data', {}).get('children', [])
        if not children:
            continue

        our_reply_data = children[0].get('data', {})
        replies_obj = our_reply_data.get('replies')

        if not replies_obj or not isinstance(replies_obj, dict):
            continue

        reply_children = replies_obj.get('data', {}).get('children', [])
        process_reddit_replies(reply_children, post_id,
                             parent_reply_id=reply_id, depth=current_depth+1)

        time.sleep(1)

db.commit()
db.close()

print(f"\nPhase A complete: {discovered} new pending, {skipped} skipped, {errors} errors")
PYTHON_SCAN


# ═══════════════════════════════════════════════════════
# PHASE A.5: X/Twitter reply discovery + engagement
# (Requires Playwright + LLM — no public API available)
# ═══════════════════════════════════════════════════════
log "Phase A.5: X/Twitter reply discovery..."

# Get existing X reply IDs so Claude knows what's already tracked
EXISTING_X_REPLIES=$(sqlite3 "$DB" "SELECT their_comment_id FROM replies WHERE platform='x';" | tr '\n' ',' | sed 's/,$//')

# Get our X post URLs for context matching
OUR_X_POSTS=$(sqlite3 -json "$DB" "
    SELECT id, our_url, substr(our_content, 1, 100) as content_preview
    FROM posts
    WHERE platform='x' AND status='active' AND our_url IS NOT NULL
    ORDER BY posted_at DESC LIMIT 30;
")

claude -p "You are the Social Autoposter engagement bot. You have Playwright MCP for browser automation.

Read $SKILL_FILE for tone and content rules. Apply them to your replies.

## Your task

Discover new replies to our X/Twitter posts and engage with them. This is the ONLY way we can find X replies — there is no API.

## Step 1: Scan notifications

1. Navigate to https://x.com/notifications/mentions
2. Wait for the page to load (3 seconds)
3. Save a snapshot to a file
4. Extract all articles that say 'Replying to @m13v_'
5. For each mention, note:
   - The author handle (e.g., @username)
   - Their reply text
   - The timestamp (how long ago)
   - The tweet status ID from any URL in the article

## Step 2: Filter out already-tracked replies

These comment IDs are already in our database — skip them:
$EXISTING_X_REPLIES

Also skip:
- Replies older than 7 days
- Replies that are just 1-2 words ('thanks', 'nice', 'cool', etc.) — log these as skipped with skip_reason='too_short'
- Our own replies (@m13v_)

## Step 3: Engage with new substantive replies

For each NEW reply worth engaging with (max 5 per run):

1. Click into the mention to see full context (our original post + their reply)
2. Draft a reply that:
   - Is 1-3 sentences, casual, first-person
   - Actually responds to what they said — answer questions, acknowledge points
   - Asks a follow-up question when natural
   - Stays under 280 characters
   - Follows the content rules from SKILL.md
   - Include a relevant project link ONLY if the topic directly relates:
     - Social media automation, Reddit marketing → https://s4l.ai
     - Wearables, AI companion, voice capture → https://www.omi.me
     - macOS automation, desktop agents → https://github.com/mediar-ai/mcp-server-macos-use
3. Click the reply textbox, type the reply, click Reply
4. Verify the 'Your post was sent' alert appears
5. Capture our reply URL from the alert
6. Close the tab with browser_tabs action 'close'
7. Log to database:
   sqlite3 ~/social-autoposter/social_posts.db \"INSERT INTO replies (post_id, platform, their_comment_id, their_author, their_content, their_comment_url, our_reply_id, our_reply_content, our_reply_url, depth, status, replied_at) VALUES (POST_ID_OR_NULL, 'x', 'THEIR_TWEET_ID', 'THEIR_NAME', 'THEIR_TEXT', 'THEIR_URL', 'OUR_REPLY_ID', 'OUR_REPLY_TEXT', 'OUR_REPLY_URL', 1, 'replied', datetime('now'));\"

For replies you skip, still log them:
   sqlite3 ~/social-autoposter/social_posts.db \"INSERT INTO replies (post_id, platform, their_comment_id, their_author, their_content, their_comment_url, depth, status, skip_reason) VALUES (NULL, 'x', 'THEIR_TWEET_ID', 'THEIR_NAME', 'THEIR_TEXT', 'THEIR_URL', 1, 'skipped', 'REASON');\"

## Our recent X posts (for context matching)
$OUR_X_POSTS

## CRITICAL: Browser Tab Management
- Use browser_tabs with action 'close' to close tabs. Do NOT use browser_close.
- Close the tab after EVERY page visit.
- At the end, call browser_tabs close one final time.

Report: how many new mentions found, how many replied to, how many skipped." --max-turns 50 2>&1 | tee -a "$LOG_FILE"

log "Phase A.5 complete"


# ═══════════════════════════════════════════════════════
# PHASE B: Claude drafts and posts replies
# ═══════════════════════════════════════════════════════
PENDING_COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM replies WHERE status='pending' AND platform != 'moltbook';")
log "Phase B: $PENDING_COUNT pending replies to handle"

if [ "$PENDING_COUNT" -gt 0 ]; then
    # Collect up to 5 pending replies with full context
    PENDING_DATA=$(sqlite3 -json "$DB" "
        SELECT r.id, r.platform, r.their_author, r.their_content, r.their_comment_url,
               r.their_comment_id, r.depth, r.moltbook_post_uuid, r.moltbook_parent_comment_uuid,
               p.thread_title, p.thread_url, p.our_content, p.our_url
        FROM replies r
        JOIN posts p ON r.post_id = p.id
        WHERE r.status='pending' AND r.platform != 'moltbook'
        ORDER BY r.discovered_at ASC
        LIMIT 10;
    ")

    claude -p "You are the Social Autoposter engagement bot. You have Playwright MCP for browser automation.

Read $SKILL_FILE for tone and content rules. Apply them to your replies.

## Your task

Reply to the following comments that people left on our posts/comments. Keep replies conversational, 2-4 sentences, expand the subject, ask follow-up questions when natural. Include links when relevant.

## Platform accounts
- Reddit: u/Deep_Ad1959 (logged in via Google with matt@mediar.ai). Use old.reddit.com.

## Pending replies to respond to

$PENDING_DATA

## Instructions for each reply

For each pending reply above:

1. **Draft a reply** that:
   - Is 2-4 sentences, casual, first-person
   - Expands on the topic being discussed
   - Asks a follow-up question when natural
   - Follows the content rules from SKILL.md
   - Write as Matthew (use 'I')
   - **IMPORTANT — include a relevant project link when it fits naturally.** Don't force it, but if the conversation touches on any of these topics, drop the link casually at the end:
     - Wearables, voice capture, audio AI, ESP32, personal AI, memory → https://github.com/BasedHardware/omi or https://www.omi.me
     - Multi-agent workflows, macOS automation, Playwright MCP, desktop agents → https://github.com/mediar-ai/mcp-server-macos-use
     - Open source AI agents, Claude Code setups → https://github.com/BasedHardware/omi
   - Format: end with something like 'repo if anyone's curious: [url]' or 'we open sourced it: [url]' — never as a bullet list or sales pitch

2. **Post the reply:**
   - **Reddit**: Use Playwright to navigate to their_comment_url (use old.reddit.com), click reply, type your response, submit. Wait 2-3s and verify. Capture the permalink of our new reply. Close the tab with browser_tabs action 'close' after each post.

3. **Update the database** after each successful reply:
   sqlite3 ~/social-autoposter/social_posts.db \"UPDATE replies SET status='replied', our_reply_content='ESCAPED_CONTENT', our_reply_url='URL', our_reply_id='ID', replied_at=datetime('now') WHERE id=REPLY_ID;\"

4. If posting fails after 3 retries, update status to 'error':
   sqlite3 ~/social-autoposter/social_posts.db \"UPDATE replies SET status='error', skip_reason='posting_failed' WHERE id=REPLY_ID;\"

## CRITICAL: Browser Tab Management
- Use browser_tabs with action 'close' to close tabs. Do NOT use browser_close.
- Close the tab after EVERY page visit.
- At the end, call browser_tabs close one final time.

Process all pending replies, then report what you did." --max-turns 80 2>&1 | tee -a "$LOG_FILE"
else
    log "No pending replies — skipping Phase B"
fi


# ═══════════════════════════════════════════════════════
# PHASE C: Cleanup
# ═══════════════════════════════════════════════════════
log "Phase C: Cleanup"

# Summary
TOTAL_PENDING=$(sqlite3 "$DB" "SELECT COUNT(*) FROM replies WHERE status='pending';")
TOTAL_REPLIED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM replies WHERE status='replied';")
TOTAL_SKIPPED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM replies WHERE status='skipped';")
TOTAL_ERRORS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM replies WHERE status='error';")

log "Replies summary: pending=$TOTAL_PENDING replied=$TOTAL_REPLIED skipped=$TOTAL_SKIPPED errors=$TOTAL_ERRORS"

# Git sync
cd "$HOME/social-autoposter"
git add social_posts.db
git diff --cached --quiet || git commit -m "engage $(date '+%Y-%m-%d %H:%M')" && git push 2>/dev/null || true

# Sync SQLite → Neon Postgres
bash "$HOME/social-autoposter/syncfield.sh" || true

# Delete old logs
find "$LOG_DIR" -name "engage-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== Engagement loop complete: $(date) ==="
