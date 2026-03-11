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
SKILL_FILE="$REPO_DIR/skill/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/engage-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== Engagement Loop Run: $(date) ==="

# ═══════════════════════════════════════════════════════
# PHASE A: Scan for replies (Python, no Claude needed)
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning for replies..."
python3 "$REPO_DIR/scripts/scan_replies.py" 2>&1 | tee -a "$LOG_FILE" || true

# ═══════════════════════════════════════════════════════
# PHASE B: X/Twitter discovery + all reply engagement
# ═══════════════════════════════════════════════════════
PENDING_COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='pending';")
log "Phase B: $PENDING_COUNT pending replies to handle"

# Always run Phase B — it handles both X/Twitter discovery and pending replies
claude -p "You are the Social Autoposter engagement bot.

Read $SKILL_FILE for the full workflow, content rules, and platform details.

Run the **Workflow: Engage** section:

## Phase C from SKILL.md: X/Twitter replies
1. Navigate to https://x.com/notifications/mentions
2. Extract mentions replying to @m13v_
3. Skip already-tracked IDs, light acknowledgments, and your own replies
4. Respond to all substantive new replies
5. Log everything to the replies table

## Phase B from SKILL.md: Respond to pending replies
There are $PENDING_COUNT pending replies in the database.

### Priority order:
1. **Replies on our original posts** (we authored the thread) — these are highest priority since no engagement = bot signal
2. **Direct questions** ("what tool", "how do you", "can you share") — opportunity to naturally mention our projects (Tiered Reply Strategy from SKILL.md)
3. **Everything else** — general engagement

### Tiered link strategy (from SKILL.md):
- **Tier 1 (default):** No link. Genuine engagement, expand topic.
- **Tier 2 (natural mention):** Conversation touches something we build. Mention casually, link only if it adds value.
- **Tier 3 (direct ask):** They ask for link/tool/source. Give it immediately.

$(if [ "$PENDING_COUNT" -gt 0 ]; then
    psql "$DATABASE_URL" -t -A -c "
        SELECT json_agg(q) FROM (
            SELECT r.id, r.platform, r.their_author, r.their_content, r.their_comment_url,
                   r.their_comment_id, r.depth,
                   p.thread_title, p.thread_url, p.our_content, p.our_url,
                   CASE WHEN p.thread_url = p.our_url THEN 1 ELSE 0 END as is_our_original_post
            FROM replies r
            JOIN posts p ON r.post_id = p.id
            WHERE r.status='pending'
            ORDER BY
                CASE WHEN p.thread_url = p.our_url THEN 0 ELSE 1 END,
                r.discovered_at ASC
        ) q;"
else
    echo "No pending replies."
fi)

Process ALL pending replies. For each: draft response (follow Content Rules + anti-AI-detection rules), post it, update DB.
Skip replies that don't warrant a response (light acknowledgments like 'thanks', 'so good', troll comments) — mark those as 'skipped' with a skip_reason.

For **github_issues** platform replies: post via gh issue comment NUMBER -R OWNER/REPO (no browser needed).
Extract OWNER/REPO and issue number from the their_comment_url field.

CRITICAL: Close browser tabs after every page visit (browser_tabs action 'close', NOT browser_close)." --max-turns 500 2>&1 | tee -a "$LOG_FILE"

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
