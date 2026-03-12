#!/usr/bin/env bash
# engage.sh — Reply engagement loop
# Phase A: Python script scans for new replies (runs in background)
# Phase B: Claude drafts and posts replies via Playwright/API (batched, 50 at a time)
# Phase C: Cleanup
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

# Give the scanner a head start to find new replies
sleep 15

# ═══════════════════════════════════════════════════════
# PHASE B: X/Twitter discovery + all reply engagement
# Process in batches of $BATCH_SIZE to avoid prompt size limits
# ═══════════════════════════════════════════════════════
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
3. Skip already-tracked IDs, light acknowledgments, and your own replies
4. Respond to all substantive new replies
5. Log everything to the replies table
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

For **github_issues**: use gh issue comment NUMBER -R OWNER/REPO.

For **reddit** — use this FAST posting method (browser_run_code):
1. First, pre-compose ALL reply texts before opening the browser. Decide skip/reply and draft text for every item.
2. For each reply, call browser_navigate to their_comment_url.
3. Then use a SINGLE browser_run_code call with this exact Playwright pattern:
\`\`\`javascript
async (page) => {
  const thing = await page.\$('#thing_t1_COMMENT_ID');
  if (!thing) return 'ERROR: comment not found';
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
  return newComments.length > 0 ? await newComments[newComments.length - 1].getAttribute('href') : 'posted';
}
\`\`\`
Replace COMMENT_ID with the Reddit comment ID (from their_comment_id, without t1_ prefix).
Replace REPLY_TEXT_HERE with a JS string literal of the reply text.
IMPORTANT: Use thing.evaluate() for clicks — do NOT use replyBtn.click() directly as it causes Playwright timeouts.
4. Update DB with the helper script (fast, single connection, minimal tokens):
   - Mark replied: python3 $REPO_DIR/scripts/reply_db.py replied ID "reply text" [url]
   - Mark skipped: python3 $REPO_DIR/scripts/reply_db.py skipped ID "reason"
   - Batch skip: python3 $REPO_DIR/scripts/reply_db.py skip_batch '{"ids":[1,2,3],"reason":"..."}'
   - Check status: python3 $REPO_DIR/scripts/reply_db.py status
   Do NOT use raw psql commands — always use reply_db.py.
5. Navigate directly to the next reply — no need to close tabs.

Do NOT use browser_snapshot, browser_click, or browser_type for Reddit replies. browser_run_code is 5x faster.
Do NOT extract permalinks from snapshots — use the JS return value or skip it.

After every 10 replies, run: python3 $REPO_DIR/scripts/reply_db.py status
PROMPT_EOF

    claude -p "$(cat "$PHASE_B_PROMPT")" --max-turns 500 2>&1 | tee -a "$LOG_FILE"
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
