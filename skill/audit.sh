#!/usr/bin/env bash
# audit.sh — Full post audit pipeline:
#   Step 1: API audit (Reddit + Moltbook) via Python
#   Step 2: X/Twitter audit via Claude + Playwright (browser required)
#   Step 3: Mark deleted/removed posts
#   Step 4: Report summary
# Called by launchd every 24 hours.

set -uo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/skill/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/audit-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" >> "$LOG_FILE"; echo "[$(date +%H:%M:%S)] $*"; }

log "=== Audit Pipeline Run: $(date) ==="

# ═══════════════════════════════════════════════════════
# STEP 1: API audit (Reddit + Moltbook)
# ═══════════════════════════════════════════════════════
log "Step 1: API audit (Python — checks deleted/removed + updates stats)"
python3 "$REPO_DIR/scripts/update_stats.py" >> "$LOG_FILE" 2>&1
STEP1_EXIT=$?
if [ "$STEP1_EXIT" -ne 0 ]; then
    log "Step 1: FAILED (exit $STEP1_EXIT) — continuing to Step 2"
else
    log "Step 1: Done"
fi

# ═══════════════════════════════════════════════════════
# STEP 2: X/Twitter audit (browser required)
# ═══════════════════════════════════════════════════════
TWITTER_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='twitter' AND status='active' AND our_url IS NOT NULL;" 2>/dev/null || echo "0")

if [ "$TWITTER_COUNT" -gt 0 ]; then
    log "Step 2: X/Twitter audit — $TWITTER_COUNT active tweets to check (Claude + Playwright)"

    gtimeout 2400 claude -p "You are the Social Autoposter audit bot.

Read $SKILL_FILE for the full workflow.

Execute **Workflow: Audit → Step 2: X/Twitter audit** and **Step 3: Mark deleted/removed posts**.

There are $TWITTER_COUNT active tweets to audit.

Follow these steps exactly:
1. Query the DB for all active Twitter posts:
   SELECT id, our_url FROM posts
   WHERE platform='twitter' AND status='active' AND our_url IS NOT NULL
   ORDER BY id

2. Use browser_run_code with the JavaScript from SKILL.md Stats Step 3 to navigate to each tweet.
   Process in batches of 20 with 8-second delays between pages.

3. For each tweet, check:
   - If the page shows 'This post is from a suspended account', 'This post was deleted', or no [role=\"group\"] element: mark as deleted/removed
   - Otherwise: extract views/likes/replies and update DB

4. Mark deleted/removed posts:
   UPDATE posts SET status='deleted', status_checked_at=NOW() WHERE id=%s
   UPDATE posts SET status='removed', status_checked_at=NOW() WHERE id=%s

5. For healthy tweets, update stats:
   UPDATE posts SET views=%s, upvotes=%s, comments_count=%s,
     engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s

6. Print a summary: tweets checked, updated, deleted, removed, errors.

CRITICAL: Use 8-second delays between page loads to avoid X rate limiting.
CRITICAL: Target the specific tweet by status ID to avoid reading parent tweet stats.
CRITICAL: Close browser tabs after you're done (browser_tabs action 'close', NOT browser_close)." --max-turns 80 >> "$LOG_FILE" 2>&1
    STEP2_EXIT=$?
    if [ "$STEP2_EXIT" -eq 124 ]; then
        log "Step 2: TIMEOUT (40 min limit reached)"
    elif [ "$STEP2_EXIT" -ne 0 ]; then
        log "Step 2: FAILED (exit $STEP2_EXIT)"
    else
        log "Step 2: Done"
    fi
else
    log "Step 2: SKIPPED — no active Twitter posts to audit"
fi

# ═══════════════════════════════════════════════════════
# STEP 4: Report summary
# ═══════════════════════════════════════════════════════
log "Step 4: Summary"

ACTIVE=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='active';" 2>/dev/null || echo "?")
DELETED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='deleted';" 2>/dev/null || echo "?")
REMOVED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='removed';" 2>/dev/null || echo "?")

log "Post status: active=$ACTIVE deleted=$DELETED removed=$REMOVED"
log "=== Audit Pipeline complete: $(date) ==="

# Clean up old logs (keep last 14 days)
find "$LOG_DIR" -name "audit-*.log" -mtime +14 -delete 2>/dev/null || true
