#!/usr/bin/env bash
# stats-linkedin-comments.sh — LinkedIn comment-engagement stats refresh.
#
# Mirrors stats-linkedin.sh's design but for comments (the replies table)
# rather than posts (the posts table). Pulls impressions + reactions +
# replies-on-our-comment for each of OUR comments visible on
# /in/me/recent-activity/comments/.
#
# What we collect:
#   - impressions (LinkedIn shows lifetime per-comment counts inline on
#     the Comments tab; e.g. "156 impressions")
#   - reactions (button[aria-label*="Reaction"] aria-label parses
#     "<N> Reaction"; LinkedIn omits the count when 0, so we fall back
#     to 0 only when both Like and Reply leaves are present)
#   - replies (leaf "<N> reply" / "<N> replies")
#
# What we DO NOT collect:
#   - thread author's post stats (those live on posts table; handled by
#     stats-linkedin.sh, not here)
#   - parent post URN namespaces are not collapsed: ugcPost / activity /
#     share are kept distinct in the feed JSON for downstream forensics.
#
# Bot-detection prevention (the May 5 incident, where the deleted
# scrape_linkedin_stats_browser.py looped page.goto over per-permalink
# /feed/update/<urn>/ URLs and got the account logged out, was caused by
# behavioral fingerprinting of scripted permalink navigation, NOT by
# Python existing in the call stack):
#   1. ONE browser_navigate per fire, to /in/me/recent-activity/comments/.
#      No warmup nav, no permalink hops, no /analytics/ permalinks, no
#      /notifications/ scrape, no Voyager API.
#   2. Slow human-like scroll: randomized 600-1100px increments,
#      randomized 1.8-3.5s pauses between scrolls. No instant
#      scrollTo(0, scrollHeight).
#   3. Up to MAX_SCROLLS scrolls, then ONE final harvest evaluate.
#      Harvest also fires DURING scroll because LinkedIn virtualizes the
#      list and detaches articles that scroll out of view; an end-only
#      evaluate would miss everything but the last few items.
#   4. No clicks. No "Show more" button click. No "View analytics" hop.
#   5. If the page redirects to /login or /checkpoint, the prompt prints
#      SESSION_INVALID and STOPs without typing credentials.
#
# Cadence target: every 4-6h (matches stats-linkedin.sh / unipile cadence
# we just retired). LinkedIn updates comment impressions in near-realtime
# but per-comment fingerprint risk is non-zero, so don't run hotter than
# this.

set -euo pipefail

source "$(dirname "$0")/lock.sh"

# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
MCP_CONFIG="$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json"

# Tunables; single source of truth.
MAX_SCROLLS=15           # in-page scrolls on /in/me/recent-activity/comments/
SCROLL_PAUSE_MIN_MS=1800 # min ms between scroll ticks (randomized)
SCROLL_PAUSE_MAX_MS=3500 # max ms between scroll ticks (randomized)
SCROLL_DY_MIN=600        # min px per scroll tick
SCROLL_DY_MAX=1100       # max px per scroll tick
CLAUDE_TIMEOUT_SEC=900   # whole Claude run cap

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/stats-linkedin-comments-$(date +%Y-%m-%d_%H%M%S).log"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== LinkedIn Comment Stats Run: $(date) ==="

# How many active LinkedIn replies do we have to (eventually) cover?
# This is just a coverage hint for the prompt + log line; the page is
# virtualized so a single fire only ever touches the most recent visible
# slice. Multi-fire cadence is what gives full coverage.
ACTIVE_COUNT=$(/opt/homebrew/bin/python3 -c "
import sys; sys.path.insert(0, '$REPO_DIR/scripts')
import db as dbmod; dbmod.load_env(); db = dbmod.get_conn()
cur = db.execute(\"\"\"SELECT COUNT(*) AS n FROM replies
  WHERE platform='linkedin' AND status IN ('replied', 'posted')
    AND our_reply_url IS NOT NULL AND our_reply_url ~ 'commentUrn'\"\"\")
print(cur.fetchone()['n'])
" 2>/dev/null || echo "0")
log "Active LinkedIn replies in DB (coverage target across fires): $ACTIVE_COUNT"

# Output paths Claude will write to.
FEED_JSON="$LOG_DIR/stats-linkedin-comments-feed-$(date +%Y%m%d_%H%M%S).json"
SUMMARY_JSON=$(mktemp -t fazm-li-comments-summary.XXXXXX).json

# 3. Build the Claude prompt.
PHASE_PROMPT=$(mktemp)
cat > "$PHASE_PROMPT" <<PROMPT_EOF
You are the Social Autoposter LinkedIn comment-stats bot.

Read $SKILL_FILE if you need context on platform conventions.

## Task: Refresh engagement stats for OUR LinkedIn comments

CRITICAL — Browser agent rule: ONLY use mcp__linkedin-agent__* tools
(browser_navigate, browser_snapshot, browser_run_code, browser_evaluate).
NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*,
or mcp__macos-use__* tools. NEVER use Python Playwright / CDP attach.

CRITICAL — LinkedIn flagged patterns (do NOT do any of these):
1. Do NOT navigate to any /feed/update/<urn>/ permalink, /analytics/
   permalink, /notifications/ page, or anywhere other than the comments
   tab below. ONE browser_navigate total.
2. Do NOT call /voyager/api/* or fetch() anything from the linkedin.com
   session.
3. Do NOT click into individual comment threads, expand "Show more
   comments", expand "Replies on Matthew Diakonov's comment", or click
   "View analytics".
4. Do NOT log in. If the page is a login or checkpoint page, print
   SESSION_INVALID and STOP — do not type credentials.

If a browser tool call is blocked or times out, wait 30 seconds and retry
the same agent. Up to 3 retries. If still blocked, STOP.

### Step 1: Navigate to your own Comments activity tab

mcp__linkedin-agent__browser_navigate to:
  https://www.linkedin.com/in/me/recent-activity/comments/

mcp__linkedin-agent__browser_snapshot once. Verify it's the Comments tab
(your own comments visible, "X impressions" labels visible). If the URL
contains /login, /checkpoint, /uas/, or the page shows captcha / 'sign in',
print exactly:
  SESSION_INVALID
and STOP. Do not type, do not click, do not navigate elsewhere.

### Step 2: Slow scroll + harvest in ONE browser_evaluate

LinkedIn virtualizes this list: articles that scroll out of view get
detached from the DOM. So we must harvest DURING scroll, accumulating
into a Map keyed by comment_id. The scroll cadence is randomized to
mimic human reading speed (the May 5 logout was caused by scripted-
looking behavior, not by volume).

mcp__linkedin-agent__browser_evaluate with this function:

  () => {
    return new Promise(resolve => {
      const acc = new Map();
      function harvest() {
        document.querySelectorAll('article').forEach(art => {
          const urnEl = art.querySelector('[data-urn^="urn:li:comment:"], [data-id^="urn:li:comment:"]');
          if (!urnEl) return;
          const urn = urnEl.getAttribute('data-urn') || urnEl.getAttribute('data-id') || '';
          const m = urn.match(/^urn:li:comment:\\((\\w+):(\\d+),(\\d+)\\)\$/);
          if (!m) return;
          const parent_kind = m[1], parent_id = m[2], comment_id = m[3];
          let impressions = null, reactions = null, replies = null;
          let saw_like = false, saw_reply = false;
          art.querySelectorAll('div, span, p, button, a').forEach(leaf => {
            if (leaf.children.length > 0) return;
            const t = (leaf.innerText || '').trim();
            if (!t) return;
            if (impressions === null) { const x = t.match(/^([\\d,]+)\\s+impressions?\$/i); if (x) impressions = parseInt(x[1].replace(/,/g,'')); }
            if (replies     === null) { const x = t.match(/^([\\d,]+)\\s+repl(y|ies)\$/i);  if (x) replies     = parseInt(x[1].replace(/,/g,'')); }
            if (t === 'Like')  saw_like  = true;
            if (t === 'Reply') saw_reply = true;
          });
          for (const b of art.querySelectorAll('button[aria-label*="eaction"]')) {
            const lbl = b.getAttribute('aria-label') || '';
            const x = lbl.match(/^([\\d,]+)\\s+Reaction/i);
            if (x) { reactions = parseInt(x[1].replace(/,/g,'')); break; }
          }
          if (reactions === null && saw_like && saw_reply) reactions = 0;
          if (replies   === null && saw_reply)             replies   = 0;
          const prev = acc.get(comment_id);
          acc.set(comment_id, {
            comment_id, parent_kind, parent_id,
            impressions: (impressions !== null ? impressions : (prev ? prev.impressions : null)),
            reactions:   (reactions   !== null ? reactions   : (prev ? prev.reactions   : null)),
            replies:     (replies     !== null ? replies     : (prev ? prev.replies     : null)),
          });
        });
      }
      let ticks = 0;
      const tick = () => {
        harvest();
        const dy = ${SCROLL_DY_MIN} + Math.random() * (${SCROLL_DY_MAX} - ${SCROLL_DY_MIN});
        window.scrollBy(0, dy);
        ticks++;
        const wait = ${SCROLL_PAUSE_MIN_MS} + Math.random() * (${SCROLL_PAUSE_MAX_MS} - ${SCROLL_PAUSE_MIN_MS});
        if (ticks < ${MAX_SCROLLS}) {
          setTimeout(tick, wait);
        } else {
          setTimeout(() => { harvest(); resolve([...acc.values()]); }, 1500);
        }
      };
      tick();
    });
  }

Save the result of that browser_evaluate to:
  $FEED_JSON

The JSON value must be the JS array as-returned. If you used the
\`filename\` parameter on browser_evaluate, the file is already on disk —
just confirm it's at the path above. If not, parse the result and Write
it to disk.

If the array is empty, still write '[]' to the file; the helper handles
empty input gracefully.

### Step 3: Apply to DB

Run:
  /opt/homebrew/bin/python3 $REPO_DIR/scripts/update_linkedin_comment_stats_from_feed.py \\
      --from-json $FEED_JSON \\
      --summary   $SUMMARY_JSON

Expected output (echo back as your final line so this run's log captures it):
  LinkedInComments: <T> total, <S> skipped, <C> checked, <U> updated, <D> deleted, <E> errors
PROMPT_EOF

# 4. Acquire the lock around the Claude run only.
acquire_lock "linkedin-browser" 1800
ensure_browser_healthy "linkedin"

/opt/homebrew/bin/gtimeout "$CLAUDE_TIMEOUT_SEC" \
    "$REPO_DIR/scripts/run_claude.sh" \
        "stats-linkedin-comments" \
        --strict-mcp-config --mcp-config "$MCP_CONFIG" \
        -p "$(cat "$PHASE_PROMPT")" \
        2>&1 | tee -a "$LOG_FILE" \
    || log "WARNING: stats-linkedin-comments claude exited with code $?"

release_lock "linkedin-browser"
rm -f "$HOME/.claude/linkedin-agent-lock.json"
rm -f "$PHASE_PROMPT"

# 5. Surface counters from the JSON sidecar.
if [ -s "$SUMMARY_JSON" ]; then
    REFRESHED=$(/opt/homebrew/bin/python3 -c "import json; print(json.load(open('$SUMMARY_JSON')).get('refreshed', 0))" 2>/dev/null || echo 0)
    NOT_FOUND=$(/opt/homebrew/bin/python3 -c "import json; print(json.load(open('$SUMMARY_JSON')).get('not_found', 0))" 2>/dev/null || echo 0)
    log "Comment stats refresh: refreshed=$REFRESHED unmatched=$NOT_FOUND"
else
    log "No summary sidecar produced; assuming zeroed run."
    REFRESHED=0
    NOT_FOUND=0
fi

# 6. Log run to persistent monitor.
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
_COST=$(/opt/homebrew/bin/python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START" --scripts "stats-linkedin-comments" 2>/dev/null || echo "0.0000")
/opt/homebrew/bin/python3 "$REPO_DIR/scripts/log_run.py" --script "stats_linkedin_comments" \
    --posted "$REFRESHED" --skipped 0 --failed 0 \
    --cost "$_COST" --elapsed "$RUN_ELAPSED" \
    2>/dev/null || true

# Cleanup temp files.
rm -f "$SUMMARY_JSON"

# Cleanup old logs + old feed JSONs.
find "$LOG_DIR" -name "stats-linkedin-comments-*.log"  -mtime +14 -delete 2>/dev/null || true
find "$LOG_DIR" -name "stats-linkedin-comments-feed-*.json" -mtime +7 -delete 2>/dev/null || true

log "=== LinkedIn comment stats complete: $(date) ==="
