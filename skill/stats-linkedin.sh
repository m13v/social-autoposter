#!/usr/bin/env bash
# stats-linkedin.sh — LinkedIn engagement stats refresh (Claude-driven, MCP-only).
#
# IMPORTANT: This pipeline does ONE browser navigation per fire to
# /in/me/recent-activity/all/, scrolls in-page (native LinkedIn lazy-load),
# and extracts engagement counts for every visible post in a single DOM
# evaluate. NO per-permalink hops. NO Voyager API. NO multi-page navigation.
#
# Replaces the old scrape_linkedin_stats_browser.py path that looped
# page.goto over 30 /feed/update/<urn>/ URLs per fire. That pattern
# triggered LinkedIn's anti-bot fingerprinting on 2026-04-17 and again on
# 2026-05-05 (incident #2). See CLAUDE.md "LinkedIn: flagged patterns to
# avoid" for the rule list this pipeline is engineered to respect.
#
# Architecture mirrors engage-linkedin.sh:
#   1. Acquire linkedin-browser lock around the Claude run only
#   2. Pull active+non-frozen LinkedIn posts from DB (for the prompt's
#      coverage hint; Claude scrolls until the active set is covered or
#      MAX_SCROLLS hit)
#   3. Run Claude with mcp__linkedin-agent__* tools
#   4. Claude navigates ONCE to /in/me/recent-activity/all/, scrolls,
#      runs ONE browser_run_code DOM extract, writes JSON to a temp file
#   5. update_linkedin_stats_from_feed.py applies scan_no_change_count
#      freeze convention to the DB
#   6. Release lock; emit the standard summary line + JSON sidecar
#
# Cadence target (when launchd is configured): every 4-6h. The freeze
# rule (3+ unchanged + 5d old) keeps the active working set ~90-150 posts
# at steady state, all of which fit on /in/me/recent-activity/all/ with a
# moderate scroll.
#
# This file is currently NOT wired into launchd. It is parked.

set -euo pipefail

source "$(dirname "$0")/lock.sh"

# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
MCP_CONFIG="$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json"

# Tunables; single source of truth.
MAX_SCROLLS=15           # in-page scrolls on /in/me/recent-activity/all/
SCROLL_PAUSE_SEC=2       # seconds between scroll batches (human-pacing)
CLAUDE_TIMEOUT_SEC=900   # whole Claude run cap; one-nav scrape is fast
FREEZE_NO_CHANGE=3       # match update_linkedin_stats_from_feed.py constant
FREEZE_AGE_DAYS=5        # match update_linkedin_stats_from_feed.py constant

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/stats-linkedin-$(date +%Y-%m-%d_%H%M%S).log"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== LinkedIn Stats Run: $(date) ==="

# 1. How many active+non-frozen posts are we trying to cover this fire?
#    This is just a coverage hint for the prompt; the actual freeze rule
#    is enforced inside update_linkedin_stats_from_feed.py.
ACTIVE_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='linkedin' AND status='active'
      AND our_url IS NOT NULL
      AND our_url ~ 'urn:li:activity:'
      AND NOT (
          COALESCE(scan_no_change_count, 0) >= $FREEZE_NO_CHANGE
          AND posted_at < NOW() - INTERVAL '$FREEZE_AGE_DAYS days'
      );
" | tr -d ' \n' || echo "0")
log "Active LinkedIn posts to cover: $ACTIVE_COUNT (freeze: ${FREEZE_NO_CHANGE}+ unchanged + ${FREEZE_AGE_DAYS}d old)"

if [ "$ACTIVE_COUNT" -eq 0 ]; then
    log "No active LinkedIn posts; skipping run."
    exit 0
fi

# 2. Active activity_ids (so the prompt can ask Claude to keep scrolling
#    until coverage is reached). Newline-separated, no quoting.
ACTIVE_AIDS=$(psql "$DATABASE_URL" -t -A -c "
    SELECT DISTINCT regexp_replace(our_url, '.*urn:li:activity:([0-9]+).*', '\1')
    FROM posts
    WHERE platform='linkedin' AND status='active'
      AND our_url IS NOT NULL
      AND our_url ~ 'urn:li:activity:'
      AND NOT (
          COALESCE(scan_no_change_count, 0) >= $FREEZE_NO_CHANGE
          AND posted_at < NOW() - INTERVAL '$FREEZE_AGE_DAYS days'
      )
    ORDER BY 1;
")

# Output paths Claude will write to.
FEED_JSON=$(mktemp -t fazm-li-feed.XXXXXX).json
SUMMARY_JSON=$(mktemp -t fazm-li-stats-summary.XXXXXX).json

# 3. Build the Claude prompt.
PHASE_PROMPT=$(mktemp)
cat > "$PHASE_PROMPT" <<PROMPT_EOF
You are the Social Autoposter LinkedIn stats bot.

Read $SKILL_FILE if you need context on platform conventions.

## Task: Refresh engagement stats for LinkedIn posts via the activity feed

CRITICAL — Browser agent rule: ONLY use mcp__linkedin-agent__* tools
(browser_navigate, browser_snapshot, browser_run_code). NEVER use generic
mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*
tools. NEVER use Python Playwright / CDP attach.

CRITICAL — LinkedIn flagged patterns (do NOT do any of these):
1. Do NOT navigate to any /feed/update/<urn>/ permalink. The point of this
   job is to avoid permalink loops. ONE browser_navigate total, to the
   activity feed URL below. Read everything from that single page's DOM.
2. Do NOT call /voyager/api/* or fetch() anything from the linkedin.com
   session.
3. Do NOT click into individual post threads, "Show comments", "See more
   reactions", or any "View profile" link.
4. Do NOT log in. If the page is a login or checkpoint page, print
   SESSION_INVALID and STOP — do not type credentials.

If a browser tool call is blocked or times out, wait 30 seconds and retry
the same agent. Up to 3 retries. If still blocked, STOP.

### Step 1: Navigate to your own activity feed

mcp__linkedin-agent__browser_navigate to:
  https://www.linkedin.com/in/me/recent-activity/all/

mcp__linkedin-agent__browser_snapshot once. Verify it's the activity feed
(post tiles visible). If the URL contains /login, /checkpoint, /uas/, or
the page shows captcha / 'sign in', print exactly:
  SESSION_INVALID
and STOP. Do not type, do not click, do not navigate elsewhere.

### Step 2: Scroll-load enough posts to cover the active set

The active (non-frozen) LinkedIn posts we need stats for: $ACTIVE_COUNT
posts. Their activity IDs (newline-separated):
$ACTIVE_AIDS

Repeat the following up to $MAX_SCROLLS times:

  mcp__linkedin-agent__browser_run_code with this JS (one evaluate, no
  interactions other than scroll):

    async () => {
      window.scrollBy(0, window.innerHeight * 1.5);
      await new Promise(r => setTimeout(r, ${SCROLL_PAUSE_SEC}000));
      const aids = new Set();
      for (const a of document.querySelectorAll('a[href*="urn:li:activity:"]')) {
        const m = (a.getAttribute('href') || '').match(/urn:li:activity:(\d+)/);
        if (m) aids.add(m[1]);
      }
      return JSON.stringify({ count: aids.size, aids: Array.from(aids) });
    }

  Parse the returned JSON. Stop scrolling when EITHER:
    - 'count' >= $ACTIVE_COUNT, OR
    - every active activity_id from the list above is in 'aids', OR
    - 'count' has not grown for 2 consecutive scrolls.

Do NOT click any 'Show more' button. Do NOT click into any post.

### Step 3: Extract per-post engagement stats in ONE DOM evaluate

mcp__linkedin-agent__browser_run_code with this JS (single evaluate; no
network calls; no interactions; reads only what's already rendered):

  async () => {
    function parseInt0(s) {
      if (!s) return 0;
      const t = String(s).replace(/[, ]/g, '').trim();
      const m = t.match(/(\d+(?:\.\d+)?)\s*([KMB]?)/i);
      if (!m) {
        const n = parseInt(t, 10);
        return isNaN(n) ? 0 : n;
      }
      let n = parseFloat(m[1]);
      const suf = (m[2] || '').toUpperCase();
      if (suf === 'K') n *= 1000;
      else if (suf === 'M') n *= 1_000_000;
      else if (suf === 'B') n *= 1_000_000_000;
      return Math.round(n);
    }

    const out = [];
    const seen = new Set();
    const containers = document.querySelectorAll(
      'div.feed-shared-update-v2, ' +
      'div[data-urn*="urn:li:activity:"], ' +
      'div[data-id*="urn:li:activity:"], ' +
      'article[data-urn*="urn:li:activity:"]'
    );

    for (const c of containers) {
      const urnAttr = c.getAttribute('data-urn') || c.getAttribute('data-id') || '';
      let m = urnAttr.match(/urn:li:activity:(\d+)/);
      if (!m) {
        const a = c.querySelector('a[href*="urn:li:activity:"]');
        if (a) m = (a.getAttribute('href') || '').match(/urn:li:activity:(\d+)/);
      }
      if (!m) continue;
      const aid = m[1];
      if (seen.has(aid)) continue;
      seen.add(aid);

      let reactions = 0;
      const reactBtn = c.querySelector(
        'button[aria-label*="reaction" i], ' +
        'button[data-test-app-aware-link*="reaction" i], ' +
        'span.social-details-social-counts__reactions-count, ' +
        '[class*="social-details-social-counts__reactions"]'
      );
      if (reactBtn) {
        const lbl = reactBtn.getAttribute('aria-label') || reactBtn.textContent || '';
        const lm = lbl.match(/([\d,.KMB ]+)\s*reaction/i);
        if (lm) reactions = parseInt0(lm[1]);
        else reactions = parseInt0((reactBtn.textContent || '').trim());
      }

      let comments = 0;
      const commentLink = c.querySelector(
        'button[aria-label*="comment" i], ' +
        'a[aria-label*="comment" i], ' +
        '[class*="social-details-social-counts__comments"]'
      );
      if (commentLink) {
        const lbl = commentLink.getAttribute('aria-label') || commentLink.textContent || '';
        const cm = lbl.match(/([\d,.KMB ]+)\s*comment/i);
        if (cm) comments = parseInt0(cm[1]);
      }

      let reposts = 0;
      const repostLink = c.querySelector(
        'button[aria-label*="repost" i], ' +
        'a[aria-label*="repost" i], ' +
        '[class*="social-details-social-counts__reposts"]'
      );
      if (repostLink) {
        const lbl = repostLink.getAttribute('aria-label') || repostLink.textContent || '';
        const rm = lbl.match(/([\d,.KMB ]+)\s*repost/i);
        if (rm) reposts = parseInt0(rm[1]);
      }

      out.push({
        activity_id: aid,
        url: 'https://www.linkedin.com/feed/update/urn:li:activity:' + aid + '/',
        reactions,
        comments,
        reposts,
      });
    }
    return JSON.stringify(out);
  }

### Step 4: Write the extracted JSON to disk

The evaluate returns a JSON-stringified array. Write it (parsed and
re-serialized so it pretty-prints) to:
  $FEED_JSON

If the array is empty, still write '[]' to the file; the helper handles
empty input and exits 0 with note=empty_feed.

### Step 5: Apply to DB

Run:
  /usr/bin/python3 $REPO_DIR/scripts/update_linkedin_stats_from_feed.py \\
      --from-json $FEED_JSON \\
      --summary   $SUMMARY_JSON

The helper prints a one-line summary in the same shape as Twitter/Reddit
stats:
  LinkedIn: <T> total, <S> skipped, <C> checked, <U> updated, <D> deleted, <E> errors

Echo that line back as your final output so this run's log captures it.
PROMPT_EOF

# 4. Acquire the lock around the Claude run only — same FIFO-queued lock
#    used by engage-linkedin.sh / run-linkedin.sh, so peer pipelines
#    serialize cleanly. run_claude.sh exports SA_PIPELINE_LOCKED=1 so the
#    PreToolUse hook skips the cross-session block check.
acquire_lock "linkedin-browser" 1800
ensure_browser_healthy "linkedin"

# Run Claude. gtimeout caps the whole phase. run_claude.sh is the same
# wrapper engage-linkedin.sh uses; --strict-mcp-config + --mcp-config
# pins the linkedin-agent MCP and blocks generic browser MCPs.
/opt/homebrew/bin/gtimeout "$CLAUDE_TIMEOUT_SEC" \
    "$REPO_DIR/scripts/run_claude.sh" \
        "stats-linkedin" \
        --strict-mcp-config --mcp-config "$MCP_CONFIG" \
        -p "$(cat "$PHASE_PROMPT")" \
        2>&1 | tee -a "$LOG_FILE" \
    || log "WARNING: stats-linkedin claude exited with code $?"

release_lock "linkedin-browser"
rm -f "$HOME/.claude/linkedin-agent-lock.json"
rm -f "$PHASE_PROMPT"

# 5. Surface counters from the JSON sidecar.
if [ -s "$SUMMARY_JSON" ]; then
    REFRESHED=$(python3 -c "import json; print(json.load(open('$SUMMARY_JSON')).get('refreshed', 0))" 2>/dev/null || echo 0)
    REMOVED=$(python3   -c "import json; print(json.load(open('$SUMMARY_JSON')).get('removed', 0))"   2>/dev/null || echo 0)
    NOT_FOUND=$(python3 -c "import json; print(json.load(open('$SUMMARY_JSON')).get('not_found', 0))" 2>/dev/null || echo 0)
    log "Stats refresh: refreshed=$REFRESHED removed=$REMOVED not_found=$NOT_FOUND"
else
    log "No summary sidecar produced; assuming zeroed run."
    REFRESHED=0
    REMOVED=0
    NOT_FOUND=0
fi

# 6. Log run to persistent monitor.
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
_COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START" --scripts "stats-linkedin" 2>/dev/null || echo "0.0000")
python3 "$REPO_DIR/scripts/log_run.py" --script "stats_linkedin" \
    --posted "$REFRESHED" --skipped 0 --failed 0 \
    --cost "$_COST" --elapsed "$RUN_ELAPSED" \
    2>/dev/null || true

# Cleanup temp files.
rm -f "$FEED_JSON" "$SUMMARY_JSON"

# Cleanup old logs.
find "$LOG_DIR" -name "stats-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== LinkedIn stats complete: $(date) ==="
