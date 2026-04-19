#!/usr/bin/env bash
# stats.sh — Full stats pipeline:
#   Step 1: API stats (upvotes, comments, deleted/removed) via Python
#   Step 2: Reddit view counts via Claude + Playwright (browser required)
#   Step 3: X/Twitter stats via Claude + Playwright (browser required)
#   Step 4: LinkedIn stats via Claude + Playwright (browser required)
# Called by launchd every 6 hours.
#
# Args (any order):
#   --platform <reddit|twitter|linkedin|moltbook>  Run only the steps for one platform.
#   --quiet                                        Minimal Python output.
# If --platform is omitted, all steps run (backward-compatible default).

set -uo pipefail

# Portable platform helpers (defines gtimeout shim for Linux). This is sourced
# early so the `gtimeout` function is available. Note: platform.sh exports a
# variable also named PLATFORM (darwin/linux), which stats.sh's arg parser
# immediately overwrites with the social-platform name below; that is fine
# because stats.sh never calls stat_mtime/platform_notify after arg parsing.
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/lib/platform.sh"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"

# Parse args (support --platform <name> and --quiet in any order).
QUIET=""
PLATFORM=""
while [ $# -gt 0 ]; do
    case "$1" in
        --platform)
            PLATFORM="${2:-}"
            shift 2
            ;;
        --platform=*)
            PLATFORM="${1#--platform=}"
            shift
            ;;
        --quiet)
            QUIET="--quiet"
            shift
            ;;
        *)
            # Unknown arg: ignore (keeps backward compatibility with callers).
            shift
            ;;
    esac
done

# Validate --platform if provided.
case "$PLATFORM" in
    ""|reddit|twitter|linkedin|moltbook)
        ;;
    *)
        echo "stats.sh: invalid --platform '$PLATFORM' (expected reddit, twitter, linkedin, or moltbook)" >&2
        exit 2
        ;;
esac

# Decide which steps to run.
# No --platform means "all" (legacy behavior, kept for manual invocations).
if [ -z "$PLATFORM" ]; then
    RUN_STEP1=1; RUN_STEP2=1; RUN_STEP3=1; RUN_STEP4=1
else
    # Per-platform mode: Step 1 is narrowed via update_stats.py's per-platform
    # flags, and only the one browser step for this platform runs.
    RUN_STEP2=0; RUN_STEP3=0; RUN_STEP4=0
    case "$PLATFORM" in
        reddit)   RUN_STEP1=1; RUN_STEP2=1 ;;
        twitter)  RUN_STEP1=0; RUN_STEP3=1 ;;  # Step 3 handles Twitter API directly.
        linkedin) RUN_STEP1=0; RUN_STEP4=1 ;;  # LinkedIn has no cheap API leg.
        moltbook) RUN_STEP1=1 ;;               # API-only, covered by Step 1.
    esac
fi

# Load secrets (MOLTBOOK_API_KEY, DATABASE_URL, etc.)
# shellcheck source=/dev/null
[ -f "$REPO_DIR/.env" ] && source "$REPO_DIR/.env"

mkdir -p "$LOG_DIR"
# Include platform in log filename so the dashboard can distinguish per-platform runs.
LOG_TAG="${PLATFORM:-all}"
LOGFILE="$LOG_DIR/stats-${LOG_TAG}-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" >> "$LOGFILE"; echo "[$(date +%H:%M:%S)] $*"; }

log "=== Stats Pipeline Run: $(date) ==="
if [ -n "$PLATFORM" ]; then
    log "Platform filter: $PLATFORM (step1=$RUN_STEP1 step2=$RUN_STEP2 step3=$RUN_STEP3 step4=$RUN_STEP4)"
else
    log "Platform filter: (none, running all steps)"
fi

# ═══════════════════════════════════════════════════════
# STEP 2: Reddit profile scrape (headless Playwright, no Claude session).
# Runs BEFORE Step 1 so thread rows get views + score + comments_count in a
# single no-API pass. Step 1 then skips any thread refreshed within the last
# 4h and spends the API budget only on comment rows.
# ═══════════════════════════════════════════════════════
if [ "$RUN_STEP2" -eq 1 ]; then
log "Step 2: Reddit profile scrape (headless Playwright)"

REDDIT_USERNAME=$(python3 -c "import json; print(json.load(open('$REPO_DIR/config.json'))['accounts']['reddit']['username'])" 2>/dev/null || echo "")

if [ -n "$REDDIT_USERNAME" ]; then
    SCRAPE_OUT=$(mktemp)
    gtimeout 900 python3 "$REPO_DIR/scripts/reddit_browser.py" scrape-views "$REDDIT_USERNAME" 300 > "$SCRAPE_OUT" 2>> "$LOGFILE"
    STEP2_EXIT=$?
    if [ "$STEP2_EXIT" -eq 124 ]; then
        log "Step 2: TIMEOUT (15 min limit reached)"
        rm -f "$SCRAPE_OUT"
    elif [ "$STEP2_EXIT" -ne 0 ]; then
        log "Step 2: FAILED scrape-views (exit $STEP2_EXIT)"
        head -c 500 "$SCRAPE_OUT" >> "$LOGFILE" 2>/dev/null || true
        rm -f "$SCRAPE_OUT"
    else
        # Extract the .results array into the format scrape_reddit_views.py expects.
        python3 -c "
import json, sys
with open('$SCRAPE_OUT') as f:
    data = json.load(f)
if not data.get('ok'):
    print('scrape_views returned ok=false:', data.get('error', 'unknown'), file=sys.stderr)
    sys.exit(2)
with open('/tmp/reddit_views.json', 'w') as f:
    json.dump(data.get('results', []), f)
print(f\"scraped {data.get('total', 0)} urls, {data.get('with_views', 0)} with views, {data.get('with_score', 0)} with score, {data.get('with_comments_count', 0)} with comments_count\")
" >> "$LOGFILE" 2>&1
        EXTRACT_EXIT=$?
        rm -f "$SCRAPE_OUT"
        if [ "$EXTRACT_EXIT" -ne 0 ]; then
            log "Step 2: FAILED extract (exit $EXTRACT_EXIT)"
        else
            python3 "$REPO_DIR/scripts/scrape_reddit_views.py" --from-json /tmp/reddit_views.json $QUIET >> "$LOGFILE" 2>&1
            UPDATE_EXIT=$?
            if [ "$UPDATE_EXIT" -ne 0 ]; then
                log "Step 2: FAILED DB update (exit $UPDATE_EXIT)"
            else
                log "Step 2: Done"
            fi
        fi
    fi
else
    log "Step 2: SKIPPED, no Reddit username in config.json"
fi
else
    log "Step 2: SKIPPED (platform=$PLATFORM)"
fi

# ═══════════════════════════════════════════════════════
# STEP 1: API stats (upvotes, comments, deleted/removed) for anything
# Step 2 couldn't cover (i.e. comment rows; threads are skipped via the
# 4h freshness window set by Step 2).
# ═══════════════════════════════════════════════════════
if [ "$RUN_STEP1" -eq 1 ]; then
    # Narrow the Python call per platform. Without --platform we run the
    # default all-platforms pass (kept for manual invocations only).
    STEP1_ARGS=()
    [ "$QUIET" = "--quiet" ] && STEP1_ARGS+=("--quiet")
    case "$PLATFORM" in
        reddit)   STEP1_ARGS+=("--reddit-only") ;;
        moltbook) STEP1_ARGS+=("--moltbook-only") ;;
        twitter)  STEP1_ARGS+=("--twitter-only") ;;
    esac

    log "Step 1: API stats (Python) ${STEP1_ARGS[*]:-}"
    python3 "$REPO_DIR/scripts/update_stats.py" "${STEP1_ARGS[@]}" >> "$LOGFILE" 2>&1
    STEP1_EXIT=$?
    if [ "$STEP1_EXIT" -ne 0 ]; then
        log "Step 1: FAILED (exit $STEP1_EXIT), continuing to next step"
    else
        log "Step 1: Done"
    fi
else
    log "Step 1: SKIPPED (platform=$PLATFORM)"
fi

# ═══════════════════════════════════════════════════════
# STEP 3: X/Twitter stats (API via fxtwitter, no browser needed)
# ═══════════════════════════════════════════════════════
if [ "$RUN_STEP3" -eq 1 ]; then
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
else
    log "Step 3: SKIPPED (platform=$PLATFORM)"
fi

# ═══════════════════════════════════════════════════════
# STEP 4: LinkedIn stats (browser required)
# ═══════════════════════════════════════════════════════
if [ "$RUN_STEP4" -eq 1 ]; then
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

Step 2: For each post URL, STRIP the ?commentUrn=... query parameter before navigating (it breaks comment rendering).
Navigate with mcp__linkedin-agent__browser_navigate to the clean URL, wait for page load.
Then run mcp__linkedin-agent__browser_run_code with this JavaScript to find OUR comment and its reactions:

SCRAPE_JS:
async (page) => {
  await page.waitForTimeout(4000);

  // CRITICAL: Comments don't render until you interact with the page.
  // Scroll down past the post, then click the "Comment" action button.
  // Do NOT use commentUrn param in the URL — it breaks comment rendering.
  await page.evaluate(() => window.scrollBy(0, 600));
  await page.waitForTimeout(2000);
  const commentActionBtn = await page.$('button[aria-label="Comment"]');
  if (commentActionBtn) {
    try { await commentActionBtn.click(); await page.waitForTimeout(5000); } catch(e) {}
  }

  // Try to expand all comments - click "Load more comments" / "See previous replies"
  const expandBtns = await page.$$('button[aria-label*="Load more comments"], button[aria-label*="load more"], button[aria-label*="See previous replies"], button[aria-label*="Load previous replies"]');
  for (const btn of expandBtns) {
    try { await btn.click(); await page.waitForTimeout(2000); } catch(e) {}
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
    sed -i.bak "s|REPO_DIR_PLACEHOLDER|$REPO_DIR|g" "$STEP4_PROMPT"
    sed -i.bak "s|LINKEDIN_NAME_PLACEHOLDER|$LINKEDIN_NAME|g" "$STEP4_PROMPT"
    sed -i.bak "s|LINKEDIN_NAME_JS_PLACEHOLDER|$LINKEDIN_NAME|g" "$STEP4_PROMPT"
    rm -f "${STEP4_PROMPT}.bak"

    gtimeout 1800 "$REPO_DIR/scripts/run_claude.sh" "stats-step4" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/no-agents-mcp.json" -p "$(cat "$STEP4_PROMPT")" >> "$LOGFILE" 2>&1
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
    log "Step 4: SKIPPED, no LinkedIn posts need stats update ($LINKEDIN_POSTS found)"
fi
else
    log "Step 4: SKIPPED (platform=$PLATFORM)"
fi

log "=== Stats Pipeline complete: $(date) ==="

# Clean up old logs (keep last 7 days). Covers both new `stats-<platform>-*`
# and any legacy `stats-YYYY-*` filenames.
find "$LOG_DIR" -name "stats-*.log" -mtime +7 -delete 2>/dev/null || true
