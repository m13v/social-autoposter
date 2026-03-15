#!/usr/bin/env bash
# engage.sh — Reply engagement loop
# Phase A: Python script scans for new replies (runs in background)
# Phase B: Claude drafts and posts replies via Playwright/API (batched, 50 at a time)
# Phase C: Cleanup
# Phase D: Edit high-performing posts (>2 upvotes, 6h+ old) with a project link
# Called by launchd every 2 hours.

set -euo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/skill/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
BATCH_SIZE=50

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/engage-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== Engagement Loop Run: $(date) ==="

# ═══════════════════════════════════════════════════════
# PHASE A: Scan for replies (runs in BACKGROUND)
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning for replies (background)..."
PYTHONUNBUFFERED=1 python3 "$REPO_DIR/scripts/scan_replies.py" 2>&1 | tee -a "$LOG_FILE" &
SCAN_PID=$!

# ═══════════════════════════════════════════════════════
# PHASE D: Edit high-performing posts with project link
# Runs FIRST — processes ALL eligible posts (no limit)
# ═══════════════════════════════════════════════════════
EDITABLE=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(q) FROM (
        SELECT id, platform, our_url, our_content, thread_title, upvotes
        FROM posts
        WHERE status='active'
          AND upvotes > 2
          AND posted_at < NOW() - INTERVAL '6 hours'
          AND link_edited_at IS NULL
          AND our_url IS NOT NULL
        ORDER BY upvotes DESC
    ) q;")

if [ "$EDITABLE" != "null" ] && [ -n "$EDITABLE" ]; then
    EDITABLE_COUNT=$(echo "$EDITABLE" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
    log "Phase D: $EDITABLE_COUNT posts eligible for link edit"

    PHASE_D_PROMPT=$(mktemp)
    cat > "$PHASE_D_PROMPT" <<PROMPT_EOF
Read $SKILL_FILE for the full workflow. Execute **Phase D only** (Edit high-performing posts with project link).

Posts eligible for editing:
$EDITABLE

Process ALL of them. For each post:
1. Read ~/social-autoposter/config.json to get the projects list.
2. Pick the project whose topics are the CLOSEST match to thread_title + our_content. Be generous - if the thread is about agents, automation, desktop, memory, or anything related to the project descriptions, it's a match. If truly nothing fits, skip that one.
3. Write 1 casual sentence + project link (use website if available, otherwise github).
   - For Moltbook (agent voice): "my human built X for this kind of thing - URL"
   - For Reddit (first person): "fwiw I built something for this - URL"
4. Append it to our_content with a blank line separator.
5. For Moltbook: extract comment UUID from our_url (after #comment-), PATCH via:
   source ~/social-autoposter/.env
   curl -s -X PATCH -H "Authorization: Bearer \$MOLTBOOK_API_KEY" \\
     -H "Content-Type: application/json" \\
     -d '{"content": "FULL_CONTENT"}' \\
     "https://www.moltbook.com/api/v1/comments/COMMENT_UUID"
6. For Reddit: navigate to old.reddit.com comment permalink via browser, click "edit", append the link text to the existing content, save, verify.
7. After each successful edit, update the DB:
   psql "\$DATABASE_URL" -c "UPDATE posts SET link_edited_at=NOW(), link_edit_content='LINK_TEXT' WHERE id=POST_ID"
PROMPT_EOF

    gtimeout 1800 claude -p "$(cat "$PHASE_D_PROMPT")" --max-turns 200 2>&1 | tee -a "$LOG_FILE"
    rm -f "$PHASE_D_PROMPT"
else
    log "Phase D: No posts eligible for link edit"
fi

# Give the scanner a head start to find new replies
sleep 15

# ═══════════════════════════════════════════════════════
# PHASE B: X/Twitter discovery + all reply engagement
# Process in batches of $BATCH_SIZE to avoid prompt size limits
# ═══════════════════════════════════════════════════════

# Reset any 'processing' items older than 2 hours back to 'pending'
# These are items the agent physically posted but crashed before marking 'replied'.
# The in-browser already-replied check (below) prevents re-posting duplicates.
RESET_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    UPDATE replies SET status='pending'
    WHERE status='processing' AND processing_at < NOW() - INTERVAL '2 hours'
    RETURNING id;" | wc -l | tr -d ' ')
[ "$RESET_COUNT" -gt 0 ] && log "Phase B: Reset $RESET_COUNT stuck 'processing' items back to pending"

BATCH_NUM=0

while true; do
    PENDING_COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='pending';")

    if [ "$PENDING_COUNT" -eq 0 ]; then
        log "Phase B: No pending replies remaining. Done!"
        break
    fi

    BATCH_NUM=$((BATCH_NUM + 1))
    BATCH_ACTUAL=$((PENDING_COUNT < BATCH_SIZE ? PENDING_COUNT : BATCH_SIZE))
    log "Phase B batch $BATCH_NUM: Processing $BATCH_ACTUAL of $PENDING_COUNT pending replies"

    PHASE_B_PROMPT=$(mktemp)
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
            WHERE r.status='pending'
            ORDER BY
                CASE WHEN p.thread_url = p.our_url THEN 0 ELSE 1 END,
                r.discovered_at ASC
            LIMIT $BATCH_SIZE
        ) q;")

    cat > "$PHASE_B_PROMPT" <<PROMPT_EOF
You are the Social Autoposter engagement bot.

Read $SKILL_FILE for the full workflow, content rules, and platform details.

$(if [ "$BATCH_NUM" -eq 1 ]; then
cat <<'TWITTER_EOF'
## X/Twitter replies
1. Navigate to https://x.com/notifications/mentions
2. Extract mentions replying to @m13v_
3. Before logging or replying to any mention: query the DB to check if it's already tracked:
   python3 $REPO_DIR/scripts/reply_db.py status
   Also run: psql "$DATABASE_URL" -t -A -c "SELECT their_comment_id FROM replies WHERE platform='x';"
   Skip any mention whose numeric tweet ID appears in that list.
4. Skip light acknowledgments and your own replies
5. Respond to all substantive new replies
6. Log everything to the replies table

CRITICAL — Twitter comment ID format: always store ONLY the numeric tweet ID as their_comment_id.
Extract it from the tweet URL: x.com/username/status/NUMERIC_ID → store just NUMERIC_ID.
NEVER prefix with username or any other text (e.g. store '2030180353625948573', NOT 'TisDad_2030180353625948573').
TWITTER_EOF
fi)

## Respond to pending replies (batch $BATCH_NUM: $BATCH_ACTUAL of $PENDING_COUNT total)

### Priority order:
1. **Replies on our original posts** (is_our_original_post=1) — highest priority
2. **Direct questions** ("what tool", "how do you", "can you share")
3. **Everything else** — general engagement

### Tiered link strategy:
- **Tier 1 (default):** No link. Genuine engagement, expand topic.
- **Tier 2 (natural mention):** Conversation touches something we build. Mention casually.
- **Tier 3 (direct ask):** They ask for link/tool/source. Give it immediately.

Here are the $BATCH_ACTUAL replies to process:
$PENDING_DATA

CRITICAL: Process EVERY reply in this batch. For each: either post a response and mark as 'replied', OR mark as 'skipped' with a skip_reason (light acknowledgments, trolls, crypto spam, DM requests, not directed at us).

CRITICAL: For ALL database operations, use the reply_db.py helper (NOT raw psql):
  python3 $REPO_DIR/scripts/reply_db.py processing ID          # BEFORE browser action
  python3 $REPO_DIR/scripts/reply_db.py replied ID "reply text" [url]   # AFTER posting
  python3 $REPO_DIR/scripts/reply_db.py skipped ID "reason"
  python3 $REPO_DIR/scripts/reply_db.py skip_batch '{"ids":[1,2,3],"reason":"..."}'
  python3 $REPO_DIR/scripts/reply_db.py status
NEVER use psql directly. reply_db.py is faster (persistent connection, no env sourcing).

MANDATORY reply flow for every item:
  Step 1: python3 reply_db.py processing ID      ← mark BEFORE touching browser
  Step 2: post reply via browser
  Step 3: python3 reply_db.py replied ID "text" [url]   ← mark AFTER success
If Step 3 fails, the item stays 'processing' and will be reset to 'pending' on the next run — safe to retry.

GitHub issues engagement is handled by a separate pipeline (github-engage.sh). Skip any github_issues replies in this batch.

For **reddit** — use this FAST posting method (browser_run_code):
1. First, pre-compose ALL reply texts before opening the browser. Decide skip/reply and draft text for every item.
2. For each reply: run python3 reply_db.py processing ID, then call browser_navigate to their_comment_url.
3. Then use a SINGLE browser_run_code call with this exact Playwright pattern:
\`\`\`javascript
async (page) => {
  const OUR_USERNAME = 'Deep_Ad1959';
  const thing = await page.\$('#thing_t1_COMMENT_ID');
  if (!thing) return 'ERROR: comment not found';

  // Check if we already replied (handles crash-recovery re-runs)
  const existingReplies = await thing.\$\$('.child .comment');
  for (const reply of existingReplies) {
    const author = await reply.\$eval('.author', el => el.textContent).catch(() => '');
    if (author === OUR_USERNAME) return 'already_replied';
  }

  await thing.evaluate(el => {
    const btn = el.querySelector('.flat-list a[onclick*="reply"]');
    if (btn) btn.click();
  });
  await page.waitForSelector('#thing_t1_COMMENT_ID .usertext-edit textarea', { timeout: 3000 });
  const textarea = await thing.\$('.usertext-edit textarea');
  await textarea.fill(REPLY_TEXT_HERE);
  await thing.evaluate(el => {
    const btn = el.querySelector('.usertext-edit button.save, .usertext-edit .save');
    if (btn) btn.click();
  });
  await page.waitForTimeout(2000);
  const newComments = await thing.\$\$('.child .comment .bylink');
  return newComments.length > 0 ? await newComments[newComments.length - 1].getAttribute('href') : null;
}
\`\`\`
Replace COMMENT_ID with the Reddit comment ID (from their_comment_id, without t1_ prefix).
Replace REPLY_TEXT_HERE with a JS string literal of the reply text.
IMPORTANT: Use thing.evaluate() for clicks — do NOT use replyBtn.click() directly as it causes Playwright timeouts.
If the JS returns 'already_replied': call reply_db.py replied ID "" to clean up without posting again.
If the JS returns null (no permalink found): call reply_db.py replied ID "text" with no URL — do NOT store the string 'posted' or their_comment_url as the URL.
4. Update DB using reply_db.py (see CRITICAL section above).
5. Navigate directly to the next reply — no need to close tabs.

Do NOT use browser_snapshot, browser_click, or browser_type for Reddit replies. browser_run_code is 5x faster.
Do NOT extract permalinks from snapshots — use the JS return value or skip it.
Do NOT store 'posted' or their_comment_url as our_reply_url — store null/no URL if the permalink is unavailable.

After every 10 replies, run: python3 $REPO_DIR/scripts/reply_db.py status
PROMPT_EOF

    gtimeout 1800 claude -p "$(cat "$PHASE_B_PROMPT")" --max-turns 500 2>&1 | tee -a "$LOG_FILE"
    rm -f "$PHASE_B_PROMPT"

    # Check if we actually made progress (avoid infinite loop)
    NEW_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='pending';")
    if [ "$NEW_PENDING" -ge "$PENDING_COUNT" ]; then
        log "WARNING: No progress made in batch $BATCH_NUM ($PENDING_COUNT -> $NEW_PENDING). Stopping to avoid infinite loop."
        break
    fi
    log "Batch $BATCH_NUM complete: $PENDING_COUNT -> $NEW_PENDING pending"
done

# Wait for scanner to finish if still running
if kill -0 "$SCAN_PID" 2>/dev/null; then
    log "Waiting for Phase A scanner to finish..."
    wait "$SCAN_PID" || true
fi

# ═══════════════════════════════════════════════════════
# PHASE C: Cleanup
# ═══════════════════════════════════════════════════════
log "Phase C: Cleanup"

TOTAL_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='pending';")
TOTAL_REPLIED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='replied';")
TOTAL_SKIPPED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='skipped';")
TOTAL_ERRORS=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='error';")

log "Replies summary: pending=$TOTAL_PENDING replied=$TOTAL_REPLIED skipped=$TOTAL_SKIPPED errors=$TOTAL_ERRORS"

# Delete old logs
find "$LOG_DIR" -name "engage-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== Engagement loop complete: $(date) ==="
