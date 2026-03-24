#!/usr/bin/env bash
# stats.sh — Full stats pipeline:
#   Step 1: API stats (upvotes, comments, deleted/removed) via Python
#   Step 2: Reddit view counts via Claude + Playwright (browser required)
#   Step 3: X/Twitter stats via Claude + Playwright (browser required)
#   Step 4: LinkedIn stats via Claude + Playwright (browser required)
# Called by launchd every 6 hours.

set -uo pipefail

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

CRITICAL: Use the reddit-agent browser (mcp__reddit-agent__* tools) for ALL steps below. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
If a tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). Do NOT fall back to any other browser tool.

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

    gtimeout 1200 claude -p "$(cat "$STEP2_PROMPT")" --max-turns 50 >> "$LOGFILE" 2>&1
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
log "Step 4: LinkedIn stats (Claude + Playwright)"

LINKEDIN_POSTS=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
      AND our_url LIKE '%linkedin.com/feed/update/%'
      AND (engagement_updated_at IS NULL OR engagement_updated_at < NOW() - INTERVAL '7 days');" 2>/dev/null || echo "0")

if [ "$LINKEDIN_POSTS" -gt 0 ]; then
    STEP4_PROMPT=$(mktemp)
    cat > "$STEP4_PROMPT" <<'STEP4_EOF'
Scrape LinkedIn engagement stats for posts. Do these steps in order, no deviations:

CRITICAL: Use the linkedin-agent browser (mcp__linkedin-agent__* tools) for ALL steps below. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
If a tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). Do NOT fall back to any other browser tool.

Step 1: Query the database to get LinkedIn posts needing stats updates:
```bash
source ~/social-autoposter/.env
psql "$DATABASE_URL" -t -A -F '|' -c "
    SELECT id, our_url FROM posts
    WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
      AND our_url LIKE '%linkedin.com/feed/update/%'
      AND (engagement_updated_at IS NULL OR engagement_updated_at < NOW() - INTERVAL '7 days')
    ORDER BY id;"
```

Step 2: For each post URL, navigate with mcp__linkedin-agent__browser_navigate, wait for page load, then run mcp__linkedin-agent__browser_run_code with this JavaScript to extract stats:

SCRAPE_JS:
async (page) => {
  await page.waitForTimeout(3000);
  const result = { reactions: 0, comments: 0, views: 0, reposts: 0 };

  // Try to get the social counts bar
  const socialBar = await page.evaluate(() => {
    const res = { reactions: 0, comments: 0, views: 0, reposts: 0 };

    // Reactions (likes) - look for the social counts section
    const reactionBtn = document.querySelector('button.social-details-social-counts__reactions-count, span.social-details-social-counts__reactions-count, button[aria-label*="reaction"], span[aria-label*="reaction"]');
    if (reactionBtn) {
      const text = reactionBtn.textContent.trim().replace(/,/g, '');
      const num = parseInt(text, 10);
      if (!isNaN(num)) res.reactions = num;
      // Also check aria-label for more accurate count
      const label = reactionBtn.getAttribute('aria-label') || '';
      const m = label.match(/([\d,]+)\s*reaction/i);
      if (m) res.reactions = parseInt(m[1].replace(/,/g, ''), 10);
    }

    // Comments count
    const commentBtn = document.querySelector('button.social-details-social-counts__comments, button[aria-label*="comment"], li.social-details-social-counts__comments');
    if (commentBtn) {
      const text = commentBtn.textContent.trim();
      const m = text.match(/([\d,]+)/);
      if (m) res.comments = parseInt(m[1].replace(/,/g, ''), 10);
    }

    // Views / impressions
    const viewsEl = document.querySelector('span.social-details-social-counts__impressions, span[aria-label*="impression"], span.analytics-entry-point');
    if (viewsEl) {
      const text = viewsEl.textContent.trim().replace(/,/g, '');
      const m = text.match(/([\d,]+)/);
      if (m) res.views = parseInt(m[1].replace(/,/g, ''), 10);
    }

    // Also try generic approach: find all elements with counts in the social bar
    document.querySelectorAll('.social-details-social-counts span, .social-details-social-counts button').forEach(el => {
      const text = el.textContent.trim().toLowerCase();
      const label = (el.getAttribute('aria-label') || '').toLowerCase();
      const numMatch = text.match(/([\d,]+)/);
      if (!numMatch) return;
      const num = parseInt(numMatch[1].replace(/,/g, ''), 10);
      if (isNaN(num)) return;

      if (label.includes('reaction') || label.includes('like')) res.reactions = Math.max(res.reactions, num);
      else if (label.includes('comment')) res.comments = Math.max(res.comments, num);
      else if (label.includes('repost') || label.includes('share')) res.reposts = Math.max(res.reposts, num);
      else if (label.includes('impression') || label.includes('view')) res.views = Math.max(res.views, num);
    });

    // Fallback: look at all text nodes for "N impressions" pattern
    const allText = document.body.innerText;
    const viewMatch = allText.match(/([\d,]+)\s*impressions?/i);
    if (viewMatch && res.views === 0) {
      res.views = parseInt(viewMatch[1].replace(/,/g, ''), 10);
    }

    return res;
  });

  return JSON.stringify(socialBar);
}

Step 3: Collect all results into a JSON array and save to /tmp/linkedin_stats.json. Each entry should be:
  {"url": "<the linkedin post url>", "reactions": N, "comments": N, "views": N, "reposts": N}

Process in batches of 10 with 5-second delays between page loads to avoid LinkedIn rate limiting.

Step 4: Run: python3 REPO_DIR_PLACEHOLDER/scripts/scrape_linkedin_stats.py --from-json /tmp/linkedin_stats.json

Step 5: Close the browser tab (mcp__linkedin-agent__browser_tabs action 'close', NOT browser_close).

Done. Report totals. Do NOT read any other files. Do NOT deviate from these steps.
STEP4_EOF
    sed -i '' "s|REPO_DIR_PLACEHOLDER|$REPO_DIR|g" "$STEP4_PROMPT"

    gtimeout 1800 claude -p "$(cat "$STEP4_PROMPT")" --max-turns 80 >> "$LOGFILE" 2>&1
    STEP4_EXIT=$?
    rm -f "$STEP4_PROMPT"
    if [ "$STEP4_EXIT" -eq 124 ]; then
        log "Step 4: TIMEOUT (30 min limit reached)"
    elif [ "$STEP4_EXIT" -ne 0 ]; then
        log "Step 4: FAILED (exit $STEP4_EXIT)"
    else
        log "Step 4: Done"
    fi
else
    log "Step 4: SKIPPED — no LinkedIn posts need stats update ($LINKEDIN_POSTS found)"
fi

log "=== Stats Pipeline complete: $(date) ==="

# Clean up old logs (keep last 7 days)
find "$LOG_DIR" -name "stats-*.log" -mtime +7 -delete 2>/dev/null || true
