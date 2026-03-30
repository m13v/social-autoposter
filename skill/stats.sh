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
log "Step 4: LinkedIn stats (Claude + Playwright)"

LINKEDIN_POSTS=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
      AND our_url LIKE '%linkedin.com/feed/update/%'
      AND (engagement_updated_at IS NULL OR engagement_updated_at < NOW() - INTERVAL '7 days');" 2>/dev/null || echo "0")

if [ "$LINKEDIN_POSTS" -gt 0 ]; then
    STEP4_PROMPT=$(mktemp)
    cat > "$STEP4_PROMPT" <<'STEP4_EOF'
Scrape LinkedIn engagement stats for OUR COMMENTS (not the parent post). Do these steps in order, no deviations:

CRITICAL: Use the linkedin-agent browser (mcp__linkedin-agent__* tools) for ALL steps below. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
If a tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). Do NOT fall back to any other browser tool.

IMPORTANT CONTEXT: Our LinkedIn posts are COMMENTS on other people's posts, not original posts.
The our_url field contains the parent post URL. We need to find OUR comment within that post
and scrape the reactions on OUR comment specifically, not the parent post's reactions.
Our LinkedIn account name is: LINKEDIN_NAME_PLACEHOLDER

Step 1: Query the database to get LinkedIn posts needing stats updates:
```bash
source ~/social-autoposter/.env
psql "$DATABASE_URL" -t -A -F '|' -c "
    SELECT id, our_url, LEFT(our_content, 80) as content_prefix FROM posts
    WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
      AND our_url LIKE '%linkedin.com/feed/update/%'
      AND (engagement_updated_at IS NULL OR engagement_updated_at < NOW() - INTERVAL '7 days')
    ORDER BY id
    LIMIT 30;"
```

Step 2: For each post URL, navigate with mcp__linkedin-agent__browser_navigate, wait for page load.
Then click "Load more comments" or "Most relevant" dropdown to show all comments if available.
Then run mcp__linkedin-agent__browser_run_code with this JavaScript to find OUR comment and its reactions:

SCRAPE_JS:
async (page) => {
  await page.waitForTimeout(4000);

  // CRITICAL: Comments don't render until you interact with the page.
  // Click the comment textbox to trigger comment loading.
  const commentBox = await page.$('[contenteditable="true"], [role="textbox"]');
  if (commentBox) {
    try { await commentBox.click(); await page.waitForTimeout(3000); } catch(e) {}
  }

  // Try to expand all comments - click "Load more comments" / "See previous replies"
  const expandBtns = await page.$$('button[aria-label*="Load more comments"], button[aria-label*="load more"], button[aria-label*="See previous replies"], button[aria-label*="Load previous replies"]');
  for (const btn of expandBtns) {
    try { await btn.click(); await page.waitForTimeout(2000); } catch(e) {}
  }

  // Switch to "Most recent" sort to see all comments
  const sortBtn = await page.$('button[class*="comments-sort-order-toggle"]');
  if (sortBtn) {
    try {
      await sortBtn.click();
      await page.waitForTimeout(1000);
      const recentOpt = await page.$('li:has-text("Most recent"), div[role="option"]:has-text("Most recent"), span:has-text("Most recent")');
      if (recentOpt) { await recentOpt.click(); await page.waitForTimeout(3000); }
    } catch(e) {}
  }

  const ourName = "LINKEDIN_NAME_JS_PLACEHOLDER";
  const contentPrefix = "CONTENT_PREFIX_JS_PLACEHOLDER";

  const result = await page.evaluate(({ourName, contentPrefix}) => {
    const res = { reactions: 0, found: false, comment_text_preview: '' };

    // Find all comment containers (current LinkedIn DOM uses article.comments-comment-entity)
    const commentContainers = document.querySelectorAll(
      'article.comments-comment-entity, ' +
      'article.comments-comment-item'
    );

    for (const container of commentContainers) {
      // Author name: current LinkedIn uses .comments-comment-meta__description-title
      const authorEl = container.querySelector(
        '.comments-comment-meta__description-title, ' +
        '.comments-post-meta__name-text'
      );
      const authorText = authorEl ? authorEl.textContent.trim() : '';

      // Comment content: current LinkedIn uses .update-components-text inside the article
      const contentEl = container.querySelector(
        '.update-components-text, ' +
        '.comments-comment-item__main-content, ' +
        '.comments-comment-item-content-body'
      );
      const commentText = contentEl ? contentEl.textContent.trim() : '';

      // Match by author name OR by content prefix (first 60 chars)
      const nameMatch = authorText.toLowerCase().includes(ourName.toLowerCase());
      const prefixClean = contentPrefix.replace(/[^a-z0-9 ]/gi, '').substring(0, 60).toLowerCase();
      const commentClean = commentText.replace(/[^a-z0-9 ]/gi, '').substring(0, 200).toLowerCase();
      const contentMatch = prefixClean.length > 20 && commentClean.includes(prefixClean);

      if (nameMatch || contentMatch) {
        res.found = true;
        res.comment_text_preview = commentText.substring(0, 80);

        // Reaction count: look for button with aria-label "N Reaction(s) on ..."
        // Current class: comments-comment-social-bar__reactions-count--cr
        const reactionEl = container.querySelector(
          'button[class*="comments-comment-social-bar__reactions-count"], ' +
          'button[aria-label*="Reaction"]'
        );
        if (reactionEl) {
          const label = reactionEl.getAttribute('aria-label') || '';
          const labelMatch = label.match(/([\d,]+)\s*[Rr]eaction/);
          if (labelMatch) {
            res.reactions = parseInt(labelMatch[1].replace(/,/g, ''), 10);
          } else {
            const text = reactionEl.textContent.trim().replace(/,/g, '');
            const num = parseInt(text, 10);
            if (!isNaN(num)) res.reactions = num;
          }
        }

        break; // Found our comment, stop searching
      }
    }

    return res;
  }, {ourName, contentPrefix});

  return JSON.stringify(result);
}

IMPORTANT: For each post, replace CONTENT_PREFIX_JS_PLACEHOLDER in the JS with the first 80 chars of content_prefix from the DB query (escaped for JS string). This helps match our comment even if the author name format differs.

Step 3: Collect all results into a JSON array and save to /tmp/linkedin_stats.json. Each entry should be:
  {"url": "<the linkedin post url>", "reactions": N, "found": true/false}
Only include entries where found=true.

Process in batches of 10 with 5-second delays between page loads to avoid LinkedIn rate limiting.

Step 4: Run: python3 REPO_DIR_PLACEHOLDER/scripts/scrape_linkedin_stats.py --from-json /tmp/linkedin_stats.json

Step 5: Close the browser tab (mcp__linkedin-agent__browser_tabs action 'close', NOT browser_close).

Done. Report totals (found vs not-found). Do NOT read any other files. Do NOT deviate from these steps.
STEP4_EOF
    LINKEDIN_NAME=$(python3 -c "import json; print(json.load(open('$REPO_DIR/config.json'))['accounts']['linkedin']['name'])" 2>/dev/null || echo "Matthew Diakonov")
    sed -i '' "s|REPO_DIR_PLACEHOLDER|$REPO_DIR|g" "$STEP4_PROMPT"
    sed -i '' "s|LINKEDIN_NAME_PLACEHOLDER|$LINKEDIN_NAME|g" "$STEP4_PROMPT"
    sed -i '' "s|LINKEDIN_NAME_JS_PLACEHOLDER|$LINKEDIN_NAME|g" "$STEP4_PROMPT"

    gtimeout 1800 claude -p "$(cat "$STEP4_PROMPT")" >> "$LOGFILE" 2>&1
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
