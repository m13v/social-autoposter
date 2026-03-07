#!/usr/bin/env bash
# engage.sh — Reply engagement loop
# Phase A: Python script scans for new replies (no Claude needed)
# Phase B: Claude drafts and posts replies via Playwright/API
# Phase C: Cleanup
# Called by launchd every 2 hours.

set -euo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
DB="$REPO_DIR/social_posts.db"
SKILL_FILE="$REPO_DIR/skill/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/engage-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== Engagement Loop Run: $(date) ==="

# ═══════════════════════════════════════════════════════
# PHASE A: Scan for replies (Python, no Claude needed)
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning for replies..."
python3 "$REPO_DIR/scripts/scan_replies.py" --db "$DB" 2>&1 | tee -a "$LOG_FILE" || true

# ═══════════════════════════════════════════════════════
# PHASE B: X/Twitter discovery + all reply engagement
# ═══════════════════════════════════════════════════════
PENDING_COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM replies WHERE status='pending';")
log "Phase B: $PENDING_COUNT pending replies to handle"

# Always run Phase B — it handles both X/Twitter discovery and pending replies
claude -p "You are the Social Autoposter engagement bot.

Read $SKILL_FILE for the full workflow, content rules, and platform details.

Run the **Workflow: Engage** section:

## Phase C from SKILL.md: X/Twitter replies
1. Navigate to https://x.com/notifications/mentions
2. Extract mentions replying to @m13v_
3. Skip already-tracked IDs, light acknowledgments, and your own replies
4. Respond to substantive new replies (max 5)
5. Log everything to the replies table

## Phase B from SKILL.md: Respond to pending replies
There are $PENDING_COUNT pending replies in the database.

$(if [ "$PENDING_COUNT" -gt 0 ]; then
    sqlite3 -json "$DB" "
        SELECT r.id, r.platform, r.their_author, r.their_content, r.their_comment_url,
               r.their_comment_id, r.depth,
               p.thread_title, p.thread_url, p.our_content, p.our_url
        FROM replies r
        JOIN posts p ON r.post_id = p.id
        WHERE r.status='pending'
        ORDER BY r.discovered_at ASC
        LIMIT 10;"
else
    echo "No pending replies."
fi)

For each reply: draft response, post it, update DB. Max 5 replies.

CRITICAL: Close browser tabs after every page visit (browser_tabs action 'close', NOT browser_close)." --max-turns 80 2>&1 | tee -a "$LOG_FILE"

# ═══════════════════════════════════════════════════════
# PHASE C: Cleanup
# ═══════════════════════════════════════════════════════
log "Phase C: Cleanup"

TOTAL_PENDING=$(sqlite3 "$DB" "SELECT COUNT(*) FROM replies WHERE status='pending';")
TOTAL_REPLIED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM replies WHERE status='replied';")
TOTAL_SKIPPED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM replies WHERE status='skipped';")
TOTAL_ERRORS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM replies WHERE status='error';")

log "Replies summary: pending=$TOTAL_PENDING replied=$TOTAL_REPLIED skipped=$TOTAL_SKIPPED errors=$TOTAL_ERRORS"

# Git sync
cd "$REPO_DIR"
git add social_posts.db
git diff --cached --quiet || git commit -m "engage $(date '+%Y-%m-%d %H:%M')" && git push 2>/dev/null || true

# Sync SQLite → Neon Postgres
bash "$REPO_DIR/syncfield.sh" || true

# Delete old logs
find "$LOG_DIR" -name "engage-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== Engagement loop complete: $(date) ==="
