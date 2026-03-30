#!/bin/bash
# recover_linkedin_urls.sh — Batch-recover correct feed/update URLs for LinkedIn posts
# that were logged with profile/search/empty URLs instead of activity URLs.
# Runs in batches of 10, verifies each batch, and continues until done.

set -uo pipefail

cd ~/social-autoposter
source .env

LOG_DIR="$HOME/social-autoposter/skill/logs"
mkdir -p "$LOG_DIR"
MASTER_LOG="$LOG_DIR/recover-linkedin-master-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$MASTER_LOG"; }

BATCH_SIZE=10
BATCH_NUM=0
TOTAL_RECOVERED=0
TOTAL_FAILED=0
MAX_BATCHES=50  # safety limit

log "=== LinkedIn URL Recovery Started ==="
log "Batch size: $BATCH_SIZE, Max batches: $MAX_BATCHES"

while [ "$BATCH_NUM" -lt "$MAX_BATCHES" ]; do
    BATCH_NUM=$((BATCH_NUM + 1))

    # Count remaining
    REMAINING=$(psql "$DATABASE_URL" -t -A -c "
        SELECT COUNT(*) FROM posts
        WHERE platform='linkedin' AND status='active'
          AND our_url IS NOT NULL AND our_url != ''
          AND our_url NOT LIKE '%/feed/update/%';" 2>/dev/null)

    if [ "$REMAINING" -eq 0 ]; then
        log "No more posts to recover. Done!"
        break
    fi

    log "--- Batch $BATCH_NUM ($REMAINING remaining) ---"

    # Get next batch of posts
    BATCH_DATA=$(psql "$DATABASE_URL" -t -A -F '|' -c "
        SELECT id, our_url, LEFT(our_content, 120)
        FROM posts
        WHERE platform='linkedin' AND status='active'
          AND our_url IS NOT NULL AND our_url != ''
          AND our_url NOT LIKE '%/feed/update/%'
        ORDER BY id DESC
        LIMIT $BATCH_SIZE;")

    if [ -z "$BATCH_DATA" ]; then
        log "No posts returned. Done!"
        break
    fi

    # Count before
    BEFORE_COUNT=$(psql "$DATABASE_URL" -t -A -c "
        SELECT COUNT(*) FROM posts
        WHERE platform='linkedin' AND status='active'
          AND our_url LIKE '%/feed/update/%';")

    # Build the prompt
    PROMPT_FILE=$(mktemp)
    cat > "$PROMPT_FILE" << 'PROMPT_HEADER'
Recover the correct feed/update URLs for LinkedIn posts. For each post below:

1. Navigate to the URL (append /recent-activity/all/ to profile URLs if needed)
2. Scroll down and search for our comment by "Matthew Diakonov" matching the content text
3. Extract the parent post's activity URL (feed/update/urn:li:activity:...)
4. Update both our_url AND thread_url in the database

CRITICAL: Use ONLY mcp__linkedin-agent__* tools. NEVER use other browser tools.
If you can't find a comment after scrolling, skip it and move to the next.

For each found post, run:
```bash
source ~/social-autoposter/.env
psql "$DATABASE_URL" -c "UPDATE posts SET our_url='https://www.linkedin.com/feed/update/urn:li:activity:ACTIVITY_ID/', thread_url='https://www.linkedin.com/feed/update/urn:li:activity:ACTIVITY_ID/' WHERE id=POST_ID;"
```

Here are the posts to recover:

PROMPT_HEADER

    # Append each post
    while IFS='|' read -r id url content; do
        echo "POST $id: URL=$url" >> "$PROMPT_FILE"
        echo "Content starts with: \"$content\"" >> "$PROMPT_FILE"
        echo "" >> "$PROMPT_FILE"
    done <<< "$BATCH_DATA"

    echo "Report: list which posts were recovered (with activity ID) and which were skipped." >> "$PROMPT_FILE"

    log "Running Claude for batch $BATCH_NUM..."
    BATCH_LOG="$LOG_DIR/recover-linkedin-batch${BATCH_NUM}-$(date +%Y-%m-%d_%H%M%S).log"

    # Run with timeout
    gtimeout 900 claude -p "$(cat "$PROMPT_FILE")" > "$BATCH_LOG" 2>&1
    EXIT_CODE=$?
    rm -f "$PROMPT_FILE"

    if [ "$EXIT_CODE" -eq 124 ]; then
        log "Batch $BATCH_NUM: TIMEOUT (15 min limit)"
    elif [ "$EXIT_CODE" -ne 0 ]; then
        log "Batch $BATCH_NUM: FAILED (exit $EXIT_CODE)"
    fi

    # Count after and compare
    AFTER_COUNT=$(psql "$DATABASE_URL" -t -A -c "
        SELECT COUNT(*) FROM posts
        WHERE platform='linkedin' AND status='active'
          AND our_url LIKE '%/feed/update/%';")

    BATCH_RECOVERED=$((AFTER_COUNT - BEFORE_COUNT))
    TOTAL_RECOVERED=$((TOTAL_RECOVERED + BATCH_RECOVERED))

    # Count how many from this batch still have bad URLs
    BATCH_IDS=$(echo "$BATCH_DATA" | cut -d'|' -f1 | tr '\n' ',' | sed 's/,$//')
    STILL_BAD=$(psql "$DATABASE_URL" -t -A -c "
        SELECT COUNT(*) FROM posts
        WHERE id IN ($BATCH_IDS)
          AND our_url NOT LIKE '%/feed/update/%';")
    TOTAL_FAILED=$((TOTAL_FAILED + STILL_BAD))

    log "Batch $BATCH_NUM result: $BATCH_RECOVERED recovered, $STILL_BAD failed/skipped"
    log "Running totals: $TOTAL_RECOVERED recovered, $TOTAL_FAILED failed"

    # Brief pause between batches
    sleep 5
done

log "=== Recovery Complete ==="
log "Total recovered: $TOTAL_RECOVERED"
log "Total failed/skipped: $TOTAL_FAILED"
log "Batches run: $BATCH_NUM"

# Final count
FINAL_GOOD=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='linkedin' AND status='active'
      AND our_url LIKE '%/feed/update/%';")
FINAL_BAD=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='linkedin' AND status='active'
      AND our_url IS NOT NULL AND our_url != ''
      AND our_url NOT LIKE '%/feed/update/%';")
log "Final state: $FINAL_GOOD trackable, $FINAL_BAD still untrackable"
