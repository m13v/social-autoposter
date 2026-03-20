#!/usr/bin/env bash
# audit.sh — Full post audit pipeline:
#   Step 1: API audit (Reddit + Moltbook) via Python
#   Step 2: X/Twitter audit via Claude + Playwright (browser required)
#   Step 3: LinkedIn audit via Claude + Playwright (browser required)
#   Step 4: Mark deleted/removed posts
#   Step 5: Report summary
# Called by launchd every 24 hours.

set -uo pipefail

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
# STEP 3: LinkedIn audit (browser required)
# ═══════════════════════════════════════════════════════
LINKEDIN_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
      AND our_url LIKE '%linkedin.com/feed/update/%';" 2>/dev/null || echo "0")

if [ "$LINKEDIN_COUNT" -gt 0 ]; then
    log "Step 3: LinkedIn audit — $LINKEDIN_COUNT active LinkedIn posts to check (Claude + Playwright)"

    gtimeout 1800 claude -p "You are the Social Autoposter audit bot.

Execute a LinkedIn post audit. There are $LINKEDIN_COUNT active LinkedIn posts to check.

CRITICAL: Use the linkedin-agent browser (mcp__linkedin-agent__* tools) for ALL LinkedIn operations. NEVER use generic mcp__playwright-extension__* tools.

Follow these steps exactly:

1. Query the DB for all active LinkedIn posts:
   source ~/social-autoposter/.env
   psql \"\$DATABASE_URL\" -t -A -F '|' -c \"
     SELECT id, our_url FROM posts
     WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
       AND our_url LIKE '%linkedin.com/feed/update/%'
     ORDER BY id;\"

2. For each post, use mcp__linkedin-agent__browser_navigate to visit the URL.
   Process in batches of 10 with 5-second delays between page loads.

3. For each post, run mcp__linkedin-agent__browser_run_code with this JavaScript:
async (page) => {
  await page.waitForTimeout(3000);
  const result = await page.evaluate(() => {
    const res = { reactions: 0, comments: 0, views: 0, reposts: 0, status: 'active' };

    // Check if post is unavailable
    const bodyText = document.body.innerText.toLowerCase();
    if (bodyText.includes('this content isn') || bodyText.includes('page not found') ||
        bodyText.includes('this post was removed') || bodyText.includes('no longer available')) {
      res.status = 'deleted';
      return res;
    }

    // Reactions
    const reactionBtn = document.querySelector('button.social-details-social-counts__reactions-count, span.social-details-social-counts__reactions-count, button[aria-label*=\"reaction\"], span[aria-label*=\"reaction\"]');
    if (reactionBtn) {
      const label = reactionBtn.getAttribute('aria-label') || '';
      const m = label.match(/([\d,]+)\\s*reaction/i);
      if (m) res.reactions = parseInt(m[1].replace(/,/g, ''), 10);
      else { const t = reactionBtn.textContent.trim().replace(/,/g, ''); const n = parseInt(t, 10); if (!isNaN(n)) res.reactions = n; }
    }

    // Comments
    const commentBtn = document.querySelector('button.social-details-social-counts__comments, button[aria-label*=\"comment\"]');
    if (commentBtn) { const m = commentBtn.textContent.trim().match(/([\d,]+)/); if (m) res.comments = parseInt(m[1].replace(/,/g, ''), 10); }

    // Views / impressions
    const viewsEl = document.querySelector('span.social-details-social-counts__impressions, span[aria-label*=\"impression\"], span.analytics-entry-point');
    if (viewsEl) { const m = viewsEl.textContent.trim().match(/([\d,]+)/); if (m) res.views = parseInt(m[1].replace(/,/g, ''), 10); }

    // Fallback impressions
    const viewMatch = document.body.innerText.match(/([\d,]+)\\s*impressions?/i);
    if (viewMatch && res.views === 0) res.views = parseInt(viewMatch[1].replace(/,/g, ''), 10);

    return res;
  });
  return JSON.stringify(result);
}

4. For each post:
   - If status is 'deleted': UPDATE posts SET status='deleted', status_checked_at=NOW() WHERE id=<id>
   - Otherwise: UPDATE posts SET upvotes=<reactions>, comments_count=<comments>, views=<views>,
       thread_engagement='<json>', engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=<id>

5. Close browser tabs after done (mcp__linkedin-agent__browser_tabs action 'close', NOT browser_close).

6. Print a summary: posts checked, updated, deleted, errors.

CRITICAL: Use 5-second delays between page loads to avoid LinkedIn rate limiting." --max-turns 80 >> "$LOG_FILE" 2>&1
    STEP3_EXIT=$?
    if [ "$STEP3_EXIT" -eq 124 ]; then
        log "Step 3: TIMEOUT (30 min limit reached)"
    elif [ "$STEP3_EXIT" -ne 0 ]; then
        log "Step 3: FAILED (exit $STEP3_EXIT)"
    else
        log "Step 3: Done"
    fi
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
log "=== Audit Pipeline complete: $(date) ==="

# Clean up old logs (keep last 14 days)
find "$LOG_DIR" -name "audit-*.log" -mtime +14 -delete 2>/dev/null || true
