#!/usr/bin/env bash
# audit.sh — Full post audit pipeline:
#   Step 1: API audit (Reddit + Moltbook) via Python
#   Step 2: X/Twitter audit via Claude + Playwright (browser required)
#   Step 3: LinkedIn audit via Claude + Playwright (browser required)
#   Step 4: Mark deleted/removed posts
#   Step 5: Report summary
# Called by launchd every 24 hours.


set -uo pipefail

# Audit lock: wait up to 60min for previous audit run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "audit" 3600

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/audit-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" >> "$LOG_FILE"; echo "[$(date +%H:%M:%S)] $*"; }

RUN_START=$(date +%s)
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
# STEP 2: X/Twitter audit (API via fxtwitter — no browser needed)
# ═══════════════════════════════════════════════════════
TWITTER_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='twitter' AND status='active' AND our_url IS NOT NULL;" 2>/dev/null || echo "0")

if [ "$TWITTER_COUNT" -gt 0 ]; then
    log "Step 2: X/Twitter audit — $TWITTER_COUNT active tweets (API via fxtwitter)"
    python3 "$REPO_DIR/scripts/update_stats.py" --twitter-audit >> "$LOG_FILE" 2>&1
    STEP2_EXIT=$?
    if [ "$STEP2_EXIT" -ne 0 ]; then
        log "Step 2: FAILED (exit $STEP2_EXIT)"
    else
        log "Step 2: Done"
    fi
else
    log "Step 2: SKIPPED — no active Twitter posts to audit"
fi

# ═══════════════════════════════════════════════════════
# STEP 3: LinkedIn audit (Python CDP — no LLM tokens)
# ═══════════════════════════════════════════════════════
LINKEDIN_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
      AND our_url LIKE '%linkedin.com/feed/update/%';" 2>/dev/null || echo "0")

if [ "$LINKEDIN_COUNT" -gt 0 ]; then
    log "Step 3: LinkedIn audit — $LINKEDIN_COUNT active LinkedIn posts to check (Python CDP)"

    # Process in batches of 30
    OFFSET=0
    TOTAL_CHECKED=0
    TOTAL_DELETED=0

    while true; do
        BATCH_JSON=$(psql "$DATABASE_URL" -t -A -c "
            SELECT json_agg(q) FROM (
                SELECT id, our_url as url
                FROM posts
                WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
                  AND our_url LIKE '%linkedin.com/feed/update/%'
                ORDER BY id
                LIMIT 30 OFFSET $OFFSET
            ) q;" 2>/dev/null)

        [ "$BATCH_JSON" = "" ] || [ "$BATCH_JSON" = "null" ] && break

        AUDIT_TMPFILE=$(mktemp)
        python3 "$REPO_DIR/scripts/linkedin_browser.py" audit-batch "$BATCH_JSON" > "$AUDIT_TMPFILE" 2>/dev/null

        if [ $? -eq 0 ] && [ -s "$AUDIT_TMPFILE" ]; then
            DATABASE_URL="$DATABASE_URL" python3 - "$AUDIT_TMPFILE" <<'PYEOF' 2>&1 | tee -a "$LOG_FILE"
import json, os, sys, psycopg2

with open(sys.argv[1]) as f:
    results = json.load(f)
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
deleted = 0
for r in results:
    if r.get('status') == 'deleted':
        cur.execute('UPDATE posts SET status=%s, status_checked_at=NOW() WHERE id=%s', ('deleted', r['id']))
        deleted += 1
    elif r.get('status') != 'error':
        cur.execute('UPDATE posts SET upvotes=%s, comments_count=%s, views=%s, engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s',
            (r.get('reactions', 0), r.get('comments', 0), r.get('views', 0), r['id']))
conn.commit()
cur.close()
conn.close()
print(f'Batch: {len(results)} checked, {deleted} deleted')
PYEOF
            rm -f "$AUDIT_TMPFILE"
        else
            rm -f "$AUDIT_TMPFILE"
        fi

        BATCH_SIZE=$(echo "$BATCH_JSON" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
        TOTAL_CHECKED=$((TOTAL_CHECKED + BATCH_SIZE))
        [ "$BATCH_SIZE" -lt 30 ] && break
        OFFSET=$((OFFSET + 30))
    done

    log "Step 3: Done — $TOTAL_CHECKED posts audited"
else
    log "Step 3: SKIPPED — no active LinkedIn posts to audit"
fi

# ═══════════════════════════════════════════════════════
# STEP 5: Report summary
# ═══════════════════════════════════════════════════════
log "Step 5: Summary"

ACTIVE=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='active';" 2>/dev/null || echo "?")
DELETED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='deleted';" 2>/dev/null || echo "?")
REMOVED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='removed';" 2>/dev/null || echo "?")

log "Post status: active=$ACTIVE deleted=$DELETED removed=$REMOVED"

# Log run to persistent monitor
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
AUDIT_FAILED=$(( (STEP1_EXIT != 0 ? 1 : 0) + (${STEP2_EXIT:-0} != 0 ? 1 : 0) ))
python3 "$REPO_DIR/scripts/log_run.py" --script "audit" --posted "$ACTIVE" --skipped 0 --failed "$AUDIT_FAILED" --cost 0 --elapsed "$RUN_ELAPSED"

log "=== Audit Pipeline complete: $(date) ==="

# Clean up old logs (keep last 14 days)
find "$LOG_DIR" -name "audit-*.log" -mtime +14 -delete 2>/dev/null || true
