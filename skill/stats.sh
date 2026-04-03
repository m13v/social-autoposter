#!/usr/bin/env bash
# stats.sh — Full stats pipeline:
#   Step 1: API stats (upvotes, comments, deleted/removed) via Python
#   Step 2: Reddit view counts via Claude + Playwright (browser required)
#   Step 3: X/Twitter stats via Claude + Playwright (browser required)
#   Step 4: LinkedIn stats via Claude + Playwright (browser required)
# Called by launchd every 6 hours.

set -uo pipefail

# Stats lock: wait up to 60min for previous stats run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "stats" 3600

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
QUIET="${1:-}"

# Load secrets (MOLTBOOK_API_KEY, DATABASE_URL, etc.)
# shellcheck source=/dev/null
[ -f "$REPO_DIR/.env" ] && source "$REPO_DIR/.env"

mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/stats-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" >> "$LOGFILE"; echo "[$(date +%H:%M:%S)] $*"; }

log "=== Stats Pipeline Run: $(date) ==="

# ═══════════════════════════════════════════════════════
# STEP 1: API stats (upvotes, comments, deleted/removed)
# ═══════════════════════════════════════════════════════
log "Step 1: API stats (Python)"
if [ "$QUIET" = "--quiet" ]; then
    python3 "$REPO_DIR/scripts/update_stats.py" --quiet >> "$LOGFILE" 2>&1
else
    python3 "$REPO_DIR/scripts/update_stats.py" >> "$LOGFILE" 2>&1
fi
STEP1_EXIT=$?
if [ "$STEP1_EXIT" -ne 0 ]; then
    log "Step 1: FAILED (exit $STEP1_EXIT) — continuing to Step 2"
else
    log "Step 1: Done"
fi

# ═══════════════════════════════════════════════════════
# STEP 2: Reddit view counts (browser required)
# ═══════════════════════════════════════════════════════
log "Step 2: Reddit view counts (Python CDP — no LLM tokens)"

REDDIT_USERNAME=$(python3 -c "import json; print(json.load(open('$REPO_DIR/config.json'))['accounts']['reddit']['username'])" 2>/dev/null || echo "")

if [ -n "$REDDIT_USERNAME" ]; then
    # Scrape views via CDP (no Claude session needed)
    VIEWS_JSON=$(python3 "$REPO_DIR/scripts/reddit_browser.py" scrape-views "$REDDIT_USERNAME" 300 2>/dev/null)
    SCRAPE_OK=$(echo "$VIEWS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ok', False))" 2>/dev/null)

    if [ "$SCRAPE_OK" = "True" ]; then
        # Extract results array and save to temp file for scrape_reddit_views.py
        SCRAPE_SUMMARY=$(echo "$VIEWS_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
with open('/tmp/reddit_views.json', 'w') as f:
    json.dump(d['results'], f)
print(f'Scraped {d[\"total\"]} articles, {d[\"with_views\"]} with views')
" 2>&1)
        log "Step 2: $SCRAPE_SUMMARY"

        python3 "$REPO_DIR/scripts/scrape_reddit_views.py" --from-json /tmp/reddit_views.json >> "$LOGFILE" 2>&1
        STEP2_EXIT=$?
    else
        log "Step 2: CDP scrape failed, output: $(echo "$VIEWS_JSON" | head -c 200)"
        STEP2_EXIT=1
    fi

    if [ "$STEP2_EXIT" -ne 0 ]; then
        log "Step 2: FAILED (exit $STEP2_EXIT)"
    else
        log "Step 2: Done"
    fi
else
    log "Step 2: SKIPPED — no Reddit username in config.json"
fi

# ═══════════════════════════════════════════════════════
# STEP 3: X/Twitter stats (API via fxtwitter — no browser needed)
# ═══════════════════════════════════════════════════════
log "Step 3: X/Twitter stats (API via fxtwitter)"
if [ "$QUIET" = "--quiet" ]; then
    python3 "$REPO_DIR/scripts/update_stats.py" --twitter-only --quiet >> "$LOGFILE" 2>&1
else
    python3 "$REPO_DIR/scripts/update_stats.py" --twitter-only >> "$LOGFILE" 2>&1
fi
STEP3_EXIT=$?
if [ "$STEP3_EXIT" -ne 0 ]; then
    log "Step 3: FAILED (exit $STEP3_EXIT)"
else
    log "Step 3: Done"
fi

# ═══════════════════════════════════════════════════════
# STEP 4: LinkedIn stats (browser required)
# ═══════════════════════════════════════════════════════
log "Step 4: LinkedIn stats (Python CDP — no LLM tokens)"

LINKEDIN_POSTS=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
      AND our_url LIKE '%linkedin.com/feed/update/%'
      AND (engagement_updated_at IS NULL OR engagement_updated_at < NOW() - INTERVAL '7 days');" 2>/dev/null || echo "0")

if [ "$LINKEDIN_POSTS" -gt 0 ]; then
    # Build JSON array of posts for batch processing
    POSTS_JSON=$(psql "$DATABASE_URL" -t -A -c "
        SELECT json_agg(q) FROM (
            SELECT id, our_url as url, LEFT(our_content, 80) as content_prefix
            FROM posts
            WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
              AND our_url LIKE '%linkedin.com/feed/update/%'
              AND (engagement_updated_at IS NULL OR engagement_updated_at < NOW() - INTERVAL '7 days')
            ORDER BY id
            LIMIT 30
        ) q;" 2>/dev/null)

    STATS_TMPFILE=$(mktemp)
    python3 "$REPO_DIR/scripts/linkedin_browser.py" stats-batch "$POSTS_JSON" > "$STATS_TMPFILE" 2>/dev/null
    STEP4_EXIT=$?

    if [ "$STEP4_EXIT" -eq 0 ] && [ -s "$STATS_TMPFILE" ]; then
        # Update DB with results
        DATABASE_URL="$DATABASE_URL" python3 - "$STATS_TMPFILE" <<'PYEOF' 2>&1 | tee -a "$LOGFILE"
import json, os, sys, psycopg2

with open(sys.argv[1]) as f:
    results = json.load(f)
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
found = 0
for r in results:
    if r.get('found'):
        found += 1
        cur.execute('UPDATE posts SET upvotes=%s, engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s',
                    (r.get('reactions', 0), r['id']))
    else:
        cur.execute('UPDATE posts SET status_checked_at=NOW() WHERE id=%s', (r['id'],))
conn.commit()
cur.close()
conn.close()
print(f'LinkedIn stats: {found}/{len(results)} comments found, updated')
PYEOF
        rm -f "$STATS_TMPFILE"
        log "Step 4: Done"
    else
        rm -f "$STATS_TMPFILE"
        log "Step 4: FAILED (exit $STEP4_EXIT)"
    fi
else
    log "Step 4: SKIPPED — no LinkedIn posts need stats update ($LINKEDIN_POSTS found)"
fi

log "=== Stats Pipeline complete: $(date) ==="

# Clean up old logs (keep last 7 days)
find "$LOG_DIR" -name "stats-*.log" -mtime +7 -delete 2>/dev/null || true
