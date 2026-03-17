#!/usr/bin/env bash
# stats.sh — Full stats pipeline:
#   Step 1: API stats (upvotes, comments, deleted/removed) via Python
#   Step 2: Reddit view counts via Claude + Playwright (browser required)
#   Step 3: X/Twitter stats via Claude + Playwright (browser required)
# Called by launchd every 6 hours.

set -uo pipefail

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/skill/SKILL.md"
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

The JavaScript below scrapes view counts from a Reddit profile page. You will run it on 4 different URLs.

SCRAPE_JS:
async (page) => {
  await page.waitForTimeout(3000);
  const allResults = new Map();
  function extractCurrent() {
    return page.evaluate(() => {
      const results = [];
      document.querySelectorAll('article').forEach(article => {
        const links = article.querySelectorAll('a[href*="/comments/"]');
        let url = null;
        for (const link of links) {
          const href = link.getAttribute('href');
          if (href && href.includes('/comments/')) {
            if (!url || href.includes('/comment/')) url = href;
          }
        }
        let views = null;
        for (const el of article.querySelectorAll('*')) {
          const text = el.textContent.trim();
          const match = text.match(/^([\d,.]+)\s*([KkMm])?\s+views?$/);
          if (match) {
            let v = parseFloat(match[1].replace(/,/g, ''));
            if (match[2] && match[2].toLowerCase() === 'k') v *= 1000;
            if (match[2] && match[2].toLowerCase() === 'm') v *= 1000000;
            views = Math.round(v);
            break;
          }
        }
        if (url) {
          results.push({ url: url.startsWith('http') ? url : 'https://www.reddit.com' + url, views });
        }
      });
      return results;
    });
  }
  let items = await extractCurrent();
  for (const item of items) allResults.set(item.url, item.views);
  let previousHeight = 0, sameHeightCount = 0, scrollCount = 0;
  while (sameHeightCount < 4 && scrollCount < 300) {
    const currentHeight = await page.evaluate(() => document.body.scrollHeight);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(2000);
    items = await extractCurrent();
    for (const item of items) allResults.set(item.url, item.views);
    if (currentHeight === previousHeight) sameHeightCount++;
    else sameHeightCount = 0;
    previousHeight = currentHeight;
    scrollCount++;
  }
  const resultsArray = Array.from(allResults.entries()).map(([url, views]) => ({ url, views }));
  return JSON.stringify({ total: resultsArray.length, scrolls: scrollCount, results: resultsArray });
}

CRITICAL: Use the reddit-agent browser (mcp__reddit-agent__* tools) for ALL steps below. NEVER use generic mcp__playwright-extension__* tools.

Step 1: mcp__reddit-agent__browser_navigate to https://www.reddit.com/user/REDDIT_USERNAME_PLACEHOLDER/comments/?sort=top
Then mcp__reddit-agent__browser_run_code with the SCRAPE_JS above. Save the "results" array to /tmp/reddit_views_1.json

Step 2: mcp__reddit-agent__browser_navigate to https://www.reddit.com/user/REDDIT_USERNAME_PLACEHOLDER/comments/?sort=new
Then mcp__reddit-agent__browser_run_code with the SCRAPE_JS above. Save the "results" array to /tmp/reddit_views_2.json

Step 3: mcp__reddit-agent__browser_navigate to https://www.reddit.com/user/REDDIT_USERNAME_PLACEHOLDER/submitted/?sort=top&t=all
Then mcp__reddit-agent__browser_run_code with the SCRAPE_JS above. Save the "results" array to /tmp/reddit_views_3.json

Step 4: mcp__reddit-agent__browser_navigate to https://www.reddit.com/user/REDDIT_USERNAME_PLACEHOLDER/submitted/?sort=new
Then mcp__reddit-agent__browser_run_code with the SCRAPE_JS above. Save the "results" array to /tmp/reddit_views_4.json

Step 5: Merge all 4 JSON files into one, deduplicating by URL (keep the entry with non-null views if both exist). Save to /tmp/reddit_views.json

Step 6: Run: python3 REPO_DIR_PLACEHOLDER/scripts/scrape_reddit_views.py --from-json /tmp/reddit_views.json

Step 7: Close the browser tab (mcp__reddit-agent__browser_tabs action 'close', NOT browser_close).

Done. Report totals. Do NOT read any other files. Do NOT deviate from these steps.
STEP2_EOF
    # Inject actual values
    sed -i '' "s|REDDIT_USERNAME_PLACEHOLDER|$REDDIT_USERNAME|g" "$STEP2_PROMPT"
    sed -i '' "s|REPO_DIR_PLACEHOLDER|$REPO_DIR|g" "$STEP2_PROMPT"

    gtimeout 1200 claude -p "$(cat "$STEP2_PROMPT")" --max-turns 18 >> "$LOGFILE" 2>&1
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
# STEP 3: X/Twitter stats (browser required)
# ═══════════════════════════════════════════════════════
log "Step 3: X/Twitter stats (Claude + Playwright)"

TWITTER_POSTS=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='twitter' AND status='active' AND our_url IS NOT NULL
      AND (engagement_updated_at IS NULL OR engagement_updated_at < NOW() - INTERVAL '7 days');" 2>/dev/null || echo "0")

if [ "$TWITTER_POSTS" -gt 0 ]; then
    gtimeout 1800 claude -p "You are the Social Autoposter stats bot.

Read $SKILL_FILE for the full workflow.

Execute **Workflow: Stats → Step 3: X/Twitter stats** ONLY.

There are $TWITTER_POSTS tweets needing stats updates.

Follow these steps exactly:
1. Query the DB for tweets needing stats (the SQL is in SKILL.md Step 3)
2. Use mcp__twitter-agent__browser_run_code with the exact JavaScript from SKILL.md Step 3 to scrape each tweet page
3. Process in batches of 20 with 8-second delays between pages
4. Update the DB with views, likes, replies for each tweet
5. Report how many tweets were updated

CRITICAL: Use the twitter-agent browser (mcp__twitter-agent__* tools) for ALL Twitter operations. NEVER use generic mcp__playwright-extension__* tools.
CRITICAL: Use 8-second delays between page loads to avoid X rate limiting.
CRITICAL: Target the specific tweet by status ID to avoid reading parent tweet stats.
CRITICAL: Close browser tabs after you're done (mcp__twitter-agent__browser_tabs action 'close', NOT browser_close)." --max-turns 50 >> "$LOGFILE" 2>&1
    STEP3_EXIT=$?
    if [ "$STEP3_EXIT" -eq 124 ]; then
        log "Step 3: TIMEOUT (30 min limit reached)"
    elif [ "$STEP3_EXIT" -ne 0 ]; then
        log "Step 3: FAILED (exit $STEP3_EXIT)"
    else
        log "Step 3: Done"
    fi
else
    log "Step 3: SKIPPED — no Twitter posts need stats update ($TWITTER_POSTS found)"
fi

log "=== Stats Pipeline complete: $(date) ==="

# Clean up old logs (keep last 7 days)
find "$LOG_DIR" -name "stats-*.log" -mtime +7 -delete 2>/dev/null || true
