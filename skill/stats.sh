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
log "Step 2: Reddit view counts (Claude + Playwright)"

REDDIT_USERNAME=$(python3 -c "import json; print(json.load(open('$REPO_DIR/config.json'))['accounts']['reddit']['username'])" 2>/dev/null || echo "")

if [ -n "$REDDIT_USERNAME" ]; then
    STEP2_PROMPT=$(mktemp)
    cat > "$STEP2_PROMPT" <<'STEP2_EOF'
Scrape Reddit view counts from 4 profile pages. Do these steps in order, no deviations:

CRITICAL: Use the reddit-agent browser (mcp__reddit-agent__* tools) for ALL steps below. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
If a tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). Do NOT fall back to any other browser tool.

For each of the 4 URLs below, do these 3 sub-steps:
  a) mcp__reddit-agent__browser_run_code code: async (page) => { await page.evaluate(() => { window.__params = {}; }); }
  b) mcp__reddit-agent__browser_navigate to the URL
  c) mcp__reddit-agent__browser_run_code filename: ~/social-autoposter/scripts/scrape_reddit_views.js
  Save the "results" array from the response to the corresponding /tmp/reddit_views_N.json file.

Step 1: URL = https://www.reddit.com/user/REDDIT_USERNAME_PLACEHOLDER/comments/?sort=top
Save results to /tmp/reddit_views_1.json

Step 2: URL = https://www.reddit.com/user/REDDIT_USERNAME_PLACEHOLDER/comments/?sort=new
Save results to /tmp/reddit_views_2.json

Step 3: URL = https://www.reddit.com/user/REDDIT_USERNAME_PLACEHOLDER/submitted/?sort=top&t=all
Save results to /tmp/reddit_views_3.json

Step 4: URL = https://www.reddit.com/user/REDDIT_USERNAME_PLACEHOLDER/submitted/?sort=new
Save results to /tmp/reddit_views_4.json

Step 5: Merge all 4 JSON files into one, deduplicating by URL (keep the entry with non-null views if both exist). Save to /tmp/reddit_views.json

Step 6: Run: python3 REPO_DIR_PLACEHOLDER/scripts/scrape_reddit_views.py --from-json /tmp/reddit_views.json

Step 7: Close the browser tab (mcp__reddit-agent__browser_tabs action 'close', NOT browser_close).

Done. Report totals. Do NOT read any other files. Do NOT deviate from these steps.
STEP2_EOF
    # Inject actual values
    sed -i '' "s|REDDIT_USERNAME_PLACEHOLDER|$REDDIT_USERNAME|g" "$STEP2_PROMPT"
    sed -i '' "s|REPO_DIR_PLACEHOLDER|$REPO_DIR|g" "$STEP2_PROMPT"

    gtimeout 1200 claude -p "$(cat "$STEP2_PROMPT")" >> "$LOGFILE" 2>&1
    STEP2_EXIT=$?
    rm -f "$STEP2_PROMPT"
    if [ "$STEP2_EXIT" -eq 124 ]; then
        log "Step 2: TIMEOUT (20 min limit reached)"
    elif [ "$STEP2_EXIT" -ne 0 ]; then
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
