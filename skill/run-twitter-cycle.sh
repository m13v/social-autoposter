#!/bin/bash
# run-twitter-cycle.sh — Combined Twitter scan + post cycle.
#
# Phase 1 (t=0):
#   - weighted-sample 5 projects from config.json
#   - LLM drafts one search query per project (style from past top queries)
#   - scrape tweets via twitter-agent, enrich via fxtwitter -> T0 snapshot
#   - store all candidates with batch_id and search_topic
#
# Sleep 300s.
#
# Phase 2 (t=5m):
#   - re-fetch the same candidates via fxtwitter -> T1 snapshot + delta_score
#   - SQL gate: only candidates with delta_score >= 1 (skip zero-momentum duds)
#   - Claude reads top 10 by delta, drops unsuitable, posts top N where N is
#     adaptive: 3 if ≥3 candidates cleared Δ≥10 (strong momentum), else 1
#   - keep remaining pending rows: salvaged into the next cycle, hard-expired
#     by Phase 0 once tweet age crosses FRESHNESS_HOURS
#
# Launchd cadence: every 20 minutes. One combined job, one browser lock.

set -uo pipefail

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"

BATCH_ID="twcycle-$(date +%Y%m%d-%H%M%S)"
LOG_FILE="$LOG_DIR/twitter-cycle-$(date +%Y-%m-%d_%H%M%S).log"
RAW_FILE="/tmp/twitter_cycle_raw_$(date +%s).json"
QUERIES_FILE="/tmp/twitter_cycle_queries_$(date +%s).json"
RUN_START=$(date +%s)
# Tweets older than this are no longer worth replying to. Pending rows older
# than this are hard-expired by Phase 0; younger pending rows are salvaged
# from prior cycles into this batch.
FRESHNESS_HOURS=6

[ -f "$REPO_DIR/.env" ] && source "$REPO_DIR/.env"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== Twitter Cycle (batch=$BATCH_ID): $(date) ==="

# Serialize with other twitter-agent consumers (engage-twitter,
# dm-outreach-twitter, link-edit-twitter, engage-dm-replies --platform twitter,
# stats.sh Step 3). Without this, concurrent pipelines collide on the shared
# twitter-agent browser profile and scraping/posting aborts mid-run.
source "$REPO_DIR/skill/lock.sh"
acquire_lock "twitter-browser" 3600

# --- Phase 0: hard-expire stale pending + salvage recent orphans -------------
# Pending rows from prior cycles fall into two buckets:
#   - tweet_posted_at older than FRESHNESS_HOURS  -> hard-expire (lost the
#     replying window, no value in retrying)
#   - still-fresh                                 -> re-assign to this batch
#     so Phase 2a re-measures T1 and Phase 2b reconsiders them. This is the
#     recovery path for cycles whose Phase 2b died on Anthropic org quota,
#     X rate limit, browser crash, or any other infra failure.
EXPIRED_STALE=$(psql "$DATABASE_URL" -t -A -c "
    UPDATE twitter_candidates
    SET status='expired'
    WHERE status='pending' AND tweet_posted_at < NOW() - INTERVAL '$FRESHNESS_HOURS hours'
    RETURNING id
" 2>/dev/null | wc -l | tr -d ' ')
[ "${EXPIRED_STALE:-0}" -gt 0 ] && log "Phase 0: hard-expired $EXPIRED_STALE pending rows older than ${FRESHNESS_HOURS}h"

SALVAGED=$(psql "$DATABASE_URL" -t -A -c "
    UPDATE twitter_candidates
    SET batch_id='$BATCH_ID'
    WHERE status='pending' AND batch_id != '$BATCH_ID'
    AND tweet_posted_at >= NOW() - INTERVAL '$FRESHNESS_HOURS hours'
    RETURNING id
" 2>/dev/null | wc -l | tr -d ' ')
[ "${SALVAGED:-0}" -gt 0 ] && log "Phase 0: salvaged $SALVAGED orphaned pending rows from prior cycles into $BATCH_ID"

# --- Weighted project sample -------------------------------------------------
PROJECTS_JSON=$(python3 - <<'PY'
import json, os, random
c = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
projects = [p for p in c.get('projects', []) if p.get('weight', 0) > 0]
weights = [(p, p.get('weight', 0)) for p in projects]
k = 5
chosen = []
remaining = list(weights)
for _ in range(min(k, len(remaining))):
    total = sum(w for _, w in remaining)
    r = random.uniform(0, total)
    acc = 0
    for i, (p, w) in enumerate(remaining):
        acc += w
        if acc >= r:
            chosen.append({
                'name': p.get('name'),
                'description': p.get('description', ''),
                # Unified search_topics (Phase 1 shared-seed migration); fall back
                # to legacy per-platform lists for pre-migration safety.
                'search_topics': p.get('search_topics') or (
                    (p.get('twitter_topics') or []) + (p.get('topics') or [])
                ),
            })
            remaining.pop(i)
            break
print(json.dumps(chosen, indent=2))
PY
)

log "Selected projects: $(echo "$PROJECTS_JSON" | python3 -c 'import json,sys; print(", ".join(p["name"] for p in json.load(sys.stdin)))')"

# --- Top past queries for style inspiration ---------------------------------
TOP_QUERIES_JSON=$(python3 "$REPO_DIR/scripts/top_twitter_queries.py" --limit 20 2>/dev/null || echo "[]")
TOP_COUNT=$(echo "$TOP_QUERIES_JSON" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
log "Top past queries loaded: $TOP_COUNT"

# --- Dud queries: phrasings that returned 0 tweets in the last 48h ----------
# Fed into the prompt as a negative-signal anti-list so the LLM stops
# redrafting the same flat queries every 20-min cycle. Source is
# twitter_search_attempts, populated below from this run's queries_used.
DUD_QUERIES_JSON=$(python3 "$REPO_DIR/scripts/top_dud_twitter_queries.py" --limit 30 --window-hours 48 2>/dev/null || echo "[]")
DUD_COUNT=$(echo "$DUD_QUERIES_JSON" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
log "Dud queries loaded: $DUD_COUNT (last 48h, 0-result)"

# --- Phase 1: Claude drafts queries, scrapes tweets -------------------------
# JSON schema forces structured output. Eliminates the prose-drift failure mode
# where the scanner summarized instead of dumping the JSON array.
SCAN_SCHEMA='{"type":"object","properties":{"tweets":{"type":"array","items":{"type":"object","properties":{"handle":{"type":"string"},"text":{"type":"string"},"tweetUrl":{"type":"string"},"datetime":{"type":"string"},"replies":{"type":"integer"},"retweets":{"type":"integer"},"likes":{"type":"integer"},"views":{"type":"integer"},"bookmarks":{"type":"integer"},"search_topic":{"type":"string"},"matched_project":{"type":"string"}},"required":["handle","text","tweetUrl","datetime","replies","retweets","likes","views","bookmarks","search_topic","matched_project"]}},"queries_used":{"type":"array","items":{"type":"object","properties":{"query":{"type":"string"},"project":{"type":"string"},"tweets_found":{"type":"integer"}},"required":["query","project","tweets_found"]}}},"required":["tweets","queries_used"]}'

log "Phase 1: drafting queries and scraping tweets..."

SCAN_OUTPUT=$("$REPO_DIR/scripts/run_claude.sh" "run-twitter-cycle-scan" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/twitter-agent-mcp.json" -p --output-format json --json-schema "$SCAN_SCHEMA" "You are a Twitter hot-tweet scanner. Your ONLY job is to find high-engagement tweets happening RIGHT NOW that are relevant to one of our projects. Do NOT post anything.

## Step 1: Draft one search query per project

You have $(echo "$PROJECTS_JSON" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))') projects. Draft exactly ONE Twitter search query for each, tailored to that project's topic space.

Projects:
$PROJECTS_JSON

Past top-performing query STYLES (use these only as inspiration for phrasing, operators, and specificity level; do NOT copy keywords blindly, adapt them to each project):
$TOP_QUERIES_JSON

DUD QUERIES — DO NOT REUSE these phrasings or close variants. They returned ZERO tweets in the last 48h, so redrafting them wastes the budget. \`attempts\` is how many cycles already wasted on each one; \`last_ran_h_ago\` is hours since the most recent attempt. Pick a different angle, different operators, or different keyword cluster:
$DUD_QUERIES_JSON

Query guidelines:
- MANDATORY: every query MUST include the operator \`since:$(date -u -v-1d +%Y-%m-%d)\` so X returns only tweets from the last ~24h. Evergreen tweets waste budget — we want momentum, not history.
- Favor high engagement: include 'min_faves:50' for broad terms, 'min_faves:20' for narrower ones
- Favor discussions/opinions (people sharing experience, asking questions), not news/promos/giveaways
- Pick a query likely to surface tweets RELEVANT to that project's actual domain
- Mix it up each run, don't always use the same query for the same project
- Use the projects' search_topics/description as grounding (search_topics is a shared concept seed list across platforms — some phrases are tuned for Reddit or GitHub, so rephrase into natural Twitter search terms with hashtag-adjacent vernacular)

## Step 2: Search and extract

For EACH project's query you drafted:
1. Navigate to: https://x.com/search?q={your_query} -filter:replies&f=live
   Use mcp__twitter-agent__browser_navigate
2. Wait 4 seconds, then run this JavaScript via mcp__twitter-agent__browser_run_code to extract tweets:

async (page) => {
  await page.waitForTimeout(3000);
  const tweets = await page.evaluate(() => {
    const results = [];
    for (const article of [...document.querySelectorAll('article[data-testid=\"tweet\"]')].slice(0, 5)) {
      try {
        let handle = '';
        for (const link of article.querySelectorAll('a[role=\"link\"]')) {
          const href = link.getAttribute('href');
          if (href && href.startsWith('/') && !href.includes('/status/') && !href.includes('/search') && href.length > 1 && href.split('/').length === 2) {
            handle = href.replace('/', ''); break;
          }
        }
        const tweetText = article.querySelector('[data-testid=\"tweetText\"]');
        const text = tweetText ? tweetText.textContent : '';
        const timeEl = article.querySelector('time');
        const timeParent = timeEl ? timeEl.closest('a') : null;
        const tweetUrl = timeParent ? 'https://x.com' + timeParent.getAttribute('href') : '';
        const datetime = timeEl ? timeEl.getAttribute('datetime') : '';
        let replies=0, retweets=0, likes=0, views=0, bookmarks=0;
        for (const btn of article.querySelectorAll('[role=\"group\"] button')) {
          const al = btn.getAttribute('aria-label') || '';
          let m;
          if (m=al.match(/([\d,]+)\s*repl/i)) replies=parseInt(m[1].replace(/,/g,''));
          if (m=al.match(/([\d,]+)\s*repost/i)) retweets=parseInt(m[1].replace(/,/g,''));
          if (m=al.match(/([\d,]+)\s*like/i)) likes=parseInt(m[1].replace(/,/g,''));
          if (m=al.match(/([\d,]+)\s*view/i)) views=parseInt(m[1].replace(/,/g,''));
          if (m=al.match(/([\d,]+)\s*bookmark/i)) bookmarks=parseInt(m[1].replace(/,/g,''));
        }
        results.push({handle, text, tweetUrl, datetime, replies, retweets, likes, views, bookmarks});
      } catch(e) {}
    }
    return results;
  });
  return JSON.stringify(tweets);
}

3. After scanning all projects, return EVERY extracted tweet via the structured 'tweets' field. Each tweet object MUST include 'search_topic' (the query that found it) and 'matched_project' (the project name whose query found it).

4. ALSO return the structured 'queries_used' array with ONE entry per project (length must equal the number of projects), each with:
   - 'query': the exact final query string you searched on x.com (without the leading 'q=' or url-encoding)
   - 'project': the project name
   - 'tweets_found': integer count of tweets you extracted for that query (0 if X showed 'No results' or the page was empty)
   This list is logged to twitter_search_attempts so future cycles can avoid redrafting dead phrasings. Emit it even when tweets_found is 0 — the zero rows are the whole point of this list.

CRITICAL RULES:
- Use ONLY mcp__twitter-agent__* tools for scraping
- Do NOT post, reply, like, or interact with any tweet
- Do NOT generate any reply content
- If a search fails or times out, skip it and continue to the next (still emit a queries_used entry with tweets_found:0 for that project)" 2>&1)

# Dump the captured envelope to the cycle log for offline inspection.
echo "$SCAN_OUTPUT" >> "$LOG_FILE"

# Parse the structured-output envelope and write the tweets array to $RAW_FILE.
# claude -p --output-format json wraps results as {"structured_output": {...}, ...}.
# Also extract queries_used (the LLM's drafted query list with per-query
# tweets_found counts) to $QUERIES_FILE so we can log every attempt to
# twitter_search_attempts — including the ZERO-result ones, which are the
# whole point of this telemetry. We MUST write $QUERIES_FILE even on the
# no-tweets exit path; otherwise duds never get logged and the negative
# anti-list stays empty.
python3 -c "
import json, sys
text = sys.stdin.read().strip()
# raw_decode reads the first complete JSON object and stops, so the trailing
# run_claude.sh cost-log JSON line on stdout/stderr does not cause 'Extra data'.
try:
    env, _ = json.JSONDecoder().raw_decode(text)
except Exception as e:
    print(f'No tweet data found in output (envelope parse error: {e})', file=sys.stderr); sys.exit(1)
so = env.get('structured_output')
if so is None:
    so = env.get('result')
if isinstance(so, str):
    try: so = json.loads(so)
    except Exception: pass

queries_used = so.get('queries_used', []) if isinstance(so, dict) else []
# Always write \$QUERIES_FILE even when empty so the shell's existence check
# is unambiguous; logger no-ops on empty list.
json.dump(queries_used, open('$QUERIES_FILE', 'w'))
print(f'Extracted {len(queries_used)} queries_used entries to $QUERIES_FILE', file=sys.stderr)

tweets = so.get('tweets', []) if isinstance(so, dict) else []
if not tweets:
    print('No tweets in structured_output.tweets', file=sys.stderr); sys.exit(1)
json.dump(tweets, open('$RAW_FILE', 'w'))
print(f'Extracted {len(tweets)} tweets to $RAW_FILE', file=sys.stderr)
" <<< "$SCAN_OUTPUT" 2>&1 | tee -a "$LOG_FILE"

EXTRACT_EXIT=${PIPESTATUS[0]:-1}

# Log every drafted query (incl. zero-result ones) to twitter_search_attempts
# BEFORE any early-exit branches. Runs even when the tweets array is empty
# so dud queries actually accumulate in the negative-signal table.
if [ -f "$QUERIES_FILE" ]; then
    python3 "$REPO_DIR/scripts/log_twitter_search_attempts.py" --batch-id "$BATCH_ID" \
        < "$QUERIES_FILE" 2>&1 | tee -a "$LOG_FILE"
    rm -f "$QUERIES_FILE"
fi
if [ "$EXTRACT_EXIT" -ne 0 ] || [ ! -f "$RAW_FILE" ]; then
    log "No tweets extracted in Phase 1. Aborting cycle."
    _COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START" --scripts "run-twitter-cycle-scan" "run-twitter-cycle-post" 2>/dev/null || echo "0.0000")
    # Detect Anthropic usage-limit hits in the scan envelope so the dashboard
    # surfaces "failed: monthly_limit" instead of a silent failed=1 row. The
    # 429 marker comes from the JSON envelope ("api_error_status":429), the
    # plain-text fallback covers Anthropic's ratelimit prose ("You've hit your
    # limit"). Reason key is consistent with engage_reddit.py for unified
    # rendering.
    PHASE1_REASON="phase1_no_tweets"
    if echo "$SCAN_OUTPUT" | grep -qiE '"api_error_status":429|"hit your limit"|usage limit'; then
        PHASE1_REASON="monthly_limit"
    fi
    python3 "$REPO_DIR/scripts/log_run.py" --script "post_twitter" --posted 0 --skipped 0 --failed 1 \
        --salvaged "${SALVAGED:-0}" \
        --failure-reasons "${PHASE1_REASON}:1" \
        --cost "$_COST" --elapsed $(( $(date +%s) - RUN_START ))
    exit 0
fi

# --- Phase 1 finalize: enrich + score with T0 + batch_id --------------------
log "Enriching via fxtwitter + scoring with T0 snapshot (batch=$BATCH_ID)..."
cat "$RAW_FILE" \
    | python3 "$REPO_DIR/scripts/enrich_twitter_candidates.py" \
    | python3 "$REPO_DIR/scripts/score_twitter_candidates.py" --batch-id "$BATCH_ID" \
    2>&1 | tee -a "$LOG_FILE"
rm -f "$RAW_FILE"

BATCH_COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM twitter_candidates WHERE batch_id='$BATCH_ID'" 2>/dev/null || echo 0)
log "Phase 1 complete. Batch has $BATCH_COUNT candidates with T0 snapshot."

if [ "$BATCH_COUNT" = "0" ]; then
    log "Empty batch. Nothing to re-score. Exiting."
    _COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START" --scripts "run-twitter-cycle-scan" "run-twitter-cycle-post" 2>/dev/null || echo "0.0000")
    # Surface as failed=1 with reason so the dashboard doesn't render this as a
    # silent "—". Distinct reason from phase1_no_tweets so the operator can tell
    # "Claude returned tweets but enrichment dropped them all" from "Claude
    # returned no tweets at all".
    python3 "$REPO_DIR/scripts/log_run.py" --script "post_twitter" --posted 0 --skipped 0 --failed 1 \
        --salvaged "${SALVAGED:-0}" \
        --failure-reasons "empty_batch:1" \
        --cost "$_COST" --elapsed $(( $(date +%s) - RUN_START ))
    exit 0
fi

# Release the twitter-browser lock during the 5-min T1 wait + HTTP-only Phase 2a.
# Other pipelines (engage-twitter, dm-outreach-twitter, link-edit-twitter,
# stats.sh) can run their browser steps in this window instead of waiting for us
# to finish. We re-acquire just before Phase 2b posts, blocking up to the
# acquire_lock timeout if another pipeline is mid-run.
log "Releasing twitter-browser lock for the T1 wait window (5min sleep + HTTP fxtwitter poll)..."
release_lock "twitter-browser"

# --- Sleep 5 min before T1 measurement --------------------------------------
log "Sleeping 300s before T1 re-measurement..."
sleep 300

# --- Phase 2a: re-fetch T1 engagement ---------------------------------------
log "Phase 2a: re-polling fxtwitter for T1 engagement..."
python3 "$REPO_DIR/scripts/fetch_twitter_t1.py" --batch-id "$BATCH_ID" 2>&1 | tee -a "$LOG_FILE"

# --- Phase 2b: top 10 by delta (Δ≥1 floor), adaptive post cap 1 or 3 --------
CANDIDATES=$(psql "$DATABASE_URL" -t -A -F '|' -c "
    SELECT id, tweet_url, author_handle,
           REPLACE(REPLACE(COALESCE(tweet_text, ''), E'\n', ' '), E'\r', ' '),
           virality_score,
           COALESCE(delta_score, 0), matched_project, search_topic,
           likes_t1, retweets_t1, replies_t1, views_t1, author_followers,
           EXTRACT(EPOCH FROM (NOW() - tweet_posted_at))/3600,
           REPLACE(REPLACE(COALESCE(draft_reply_text, ''), E'\n', ' '), E'\r', ' '),
           COALESCE(draft_engagement_style, ''),
           CASE WHEN drafted_at IS NULL THEN -1
                ELSE EXTRACT(EPOCH FROM (NOW() - drafted_at))/60
           END
    FROM twitter_candidates
    WHERE batch_id='$BATCH_ID' AND status='pending' AND delta_score >= 1
    ORDER BY delta_score DESC
    LIMIT 10;
" 2>/dev/null || echo "")

if [ -z "$CANDIDATES" ]; then
    log "No candidates with delta scores. Marking batch expired."
    psql "$DATABASE_URL" -c "UPDATE twitter_candidates SET status='expired' WHERE batch_id='$BATCH_ID' AND status='pending'" 2>&1 | tee -a "$LOG_FILE"
    _COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START" --scripts "run-twitter-cycle-scan" "run-twitter-cycle-post" 2>/dev/null || echo "0.0000")
    EXPIRED_BATCH=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM twitter_candidates WHERE batch_id='$BATCH_ID' AND status='expired'" 2>/dev/null || echo 0)
    # Not a hard error — batch had candidates but none cleared the Δ≥1 floor.
    # Report as skipped (not failed) so the row reads "skipped: N" rather than
    # the silent "—" we used to render. failure_reasons stays empty.
    python3 "$REPO_DIR/scripts/log_run.py" --script "post_twitter" --posted 0 --skipped "${EXPIRED_BATCH:-0}" --failed 0 \
        --salvaged "${SALVAGED:-0}" \
        --cost "$_COST" --elapsed $(( $(date +%s) - RUN_START ))
    exit 0
fi

CANDIDATE_COUNT=$(printf '%s\n' "$CANDIDATES" | grep -c '^[0-9]')
log "Top $CANDIDATE_COUNT candidates by delta selected for post review."

# Adaptive post cap: if ≥3 candidates cleared Δ≥10 (strong momentum), allow up to 3
# posts; otherwise cap at 1 so we don't burn reply budget on marginal cycles.
HIGH_DELTA_COUNT=$(printf '%s\n' "$CANDIDATES" | awk -F'|' '$1 ~ /^[0-9]+$/ && $6+0 >= 10 {n++} END {print n+0}')
if [ "$HIGH_DELTA_COUNT" -ge 3 ]; then
    POST_LIMIT=3
else
    POST_LIMIT=1
fi
log "Adaptive post cap: $HIGH_DELTA_COUNT candidates with Δ≥10 → POST_LIMIT=$POST_LIMIT"

CANDIDATE_BLOCK=""
while IFS='|' read -r cid curl cauthor ctext cscore cdelta cproject ctopic clikes crts creplies cviews cfollowers cage cdraft cdraftstyle cdraftage; do
    DRAFT_LINE=""
    if [ -n "$cdraft" ] && [ "$cdraftage" != "-1" ]; then
        # Round draft age to whole minutes for the prompt.
        DRAFT_MIN=$(printf '%.0f' "$cdraftage")
        DRAFT_LINE="
EXISTING DRAFT (style=$cdraftstyle, age=${DRAFT_MIN}m): $cdraft"
    fi
    CANDIDATE_BLOCK="${CANDIDATE_BLOCK}
---
Candidate ID: $cid
URL: $curl
Author: @$cauthor (${cfollowers} followers)
Text: $ctext
Score: $cscore | Delta (5min): $cdelta | Likes: $clikes | RTs: $crts | Replies: $creplies | Views: $cviews | Age: ${cage}h
Search query: $ctopic
Project match: $cproject${DRAFT_LINE}
"
done <<< "$CANDIDATES"

ALL_PROJECTS_JSON=$(python3 -c "
import json, os
config = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
print(json.dumps({p['name']: p for p in config.get('projects', [])}, indent=2))
" 2>/dev/null || echo "{}")

TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform twitter 2>/dev/null || echo "(top performers report unavailable)")

source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block twitter posting)

# Re-acquire the browser lock before Phase 2b posting. Blocks (up to the
# acquire_lock timeout) if another twitter-agent consumer is mid-run; that is
# the desired behavior, not a bug — we yield while they work and resume when
# they're done. T1 measurements were already captured above via HTTP, so a
# brief wait here doesn't invalidate the candidate scoring.
log "Re-acquiring twitter-browser lock for Phase 2b posting..."
acquire_lock "twitter-browser" 3600
ensure_browser_healthy "twitter"

log "Phase 2b: Claude reviewing top candidates and posting up to $POST_LIMIT..."

"$REPO_DIR/scripts/run_claude.sh" "run-twitter-cycle-post" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/twitter-agent-mcp.json" -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for account handle.

## PRE-SCORED CANDIDATES (top by 5-min engagement velocity, best first)
These are the top candidates from this cycle's scan, re-ranked by how much their engagement GREW during the last 5 minutes. Higher Delta = trending harder right now.
$CANDIDATE_BLOCK

## PROJECT ROUTING (per-candidate)
Each candidate has a 'Project match' field (the project whose query found it).
Use that project for each reply, unless the thread content clearly better fits another project.
All project configs: $ALL_PROJECTS_JSON

## FEEDBACK FROM PAST PERFORMANCE:
$TOP_REPORT

$STYLES_BLOCK

## WORKFLOW
Reply to AT MOST $POST_LIMIT candidate(s) this cycle (post limit). Pick the ones with the strongest combination of high delta + genuinely relevant thread. Skip any candidate whose thread is off-topic, toxic, or low-quality. If fewer than $POST_LIMIT candidates are truly on-brand, post fewer; do not force posts.

For each chosen candidate:
1. Navigate to the candidate URL via mcp__twitter-agent__browser_navigate (read-only, to understand context)
2. Read the full thread
3. DRAFT HANDLING (existing draft vs fresh):
   - If the candidate block above shows an EXISTING DRAFT line AND the draft age is under 30 minutes, REUSE that draft text as-is. Skip drafting; jump to step 4 with YOUR_REPLY_TEXT = the existing draft. Reason: a prior cycle already paid the LLM cost; don't waste it. The draft was vetted at draft time; thread context rarely shifts meaningfully in 30 min.
   - Otherwise (no draft, or draft age ≥ 30 min): draft a reply using the best engagement style. Keep it 1-2 sentences. NEVER use em dashes. Apply the matched project's \`voice\` block from ALL_PROJECTS_JSON above: follow \`voice.tone\`, never violate any item in \`voice.never\`, and mirror \`voice.examples\` / \`voice.examples_good\` when present.
3a. PERSIST THE DRAFT BEFORE POSTING (only when you drafted fresh in step 3; skip when reusing an existing draft):
     python3 $REPO_DIR/scripts/log_draft.py --candidate-id CANDIDATE_ID --text 'YOUR_REPLY_TEXT' --style STYLE
   This guarantees that if step 4 fails (CDP timeout, browser crash, monthly cap), the next cycle's Phase 2b sees the draft on the salvaged row and can post it without redrafting. Failure here is non-fatal: log a warning and continue to step 4.
4. Post via the CDP script:
     python3 $REPO_DIR/scripts/twitter_browser.py reply \"CANDIDATE_URL\" \"YOUR_REPLY_TEXT\"
   It returns JSON. Parse reply_url. If reply_url is missing/invalid/doesn't match x.com/m13v_/status/, treat as FAILED: do NOT log, mark candidate 'failed' not 'posted'. NEVER use the parent URL as our_url.
   The tool may append an active campaign suffix at sample_rate (tool-level enforcement, the literal text is guaranteed to land). Use the JSON's \`final_text\` (NOT YOUR_REPLY_TEXT) for log_post.py in step 5 so the stored content matches what was posted, and use \`applied_campaigns\` (the array of campaign ids that fired) in step 5b.
5. Log the primary reply to the database FIRST, BEFORE attempting the self-reply. This guarantees the row exists even if the self-reply crashes; the link-edit-twitter sweep will pick it up later. Parse post_id from the JSON output:
     python3 $REPO_DIR/scripts/log_post.py --platform twitter --thread-url CANDIDATE_URL --our-url REPLY_URL --our-content 'FINAL_TEXT_FROM_REPLY_JSON' --project MATCHED_PROJECT --thread-author AUTHOR --thread-title 'TWEET_TEXT' --engagement-style STYLE --language LANG
5b. Attribute the post to any campaigns that fired. For each \`cid\` in \`applied_campaigns\` from step 4's JSON (skip if the array is empty):
     python3 $REPO_DIR/scripts/campaign_bump.py --table posts --id POST_ID --campaign-id cid
   Mandatory when applied_campaigns is non-empty; otherwise the campaign counter does not advance and the campaign will over-post.
6. Self-reply with project link.
   LANDING-PAGE GATE: if the matched project has a landing_pages config in config.json (repo + base_url set), SKIP this step entirely. Do not post a bare-URL self-reply. The link-edit-twitter sweep (runs every 6h) will generate a custom per-thread landing page via seo/generate_page.py and post the self-reply with that URL. A bare homepage link would permanently mark the post as link-edited and lock out the custom page. Proceed directly to step 7.
   If the matched project has NO landing_pages config, post the inline self-reply with the plain project URL:
     python3 $REPO_DIR/scripts/twitter_browser.py self-reply \"YOUR_REPLY_URL\" \"FOLLOW_UP_TEXT\" \"PROJECT_URL\"
   FOLLOW_UP_TEXT: 1 short casual sentence, lowercase, no hard sell, no em dashes. Match parent tweet's language.
   PROJECT_URL: exact website URL from the project's config. If the matched project has no URL, skip this step AND leave link_edited_at NULL (the sweep will also skip it since there's no URL to add).
   On success, immediately record the self-reply on the parent post so the sweep doesn't re-attempt:
     python3 $REPO_DIR/scripts/log_post.py --mark-self-reply --post-id POST_ID --self-reply-url SELF_REPLY_URL --self-reply-content 'FOLLOW_UP_TEXT_WITH_URL'
   On failure: do NOT mark; leave link_edited_at NULL so link-edit-twitter picks it up on the next sweep.
7. Mark candidate:
     UPDATE twitter_candidates SET status='posted', posted_at=NOW(), post_id=POST_ID WHERE id=CANDIDATE_ID

If a thread is unfit: UPDATE twitter_candidates SET status='skipped' WHERE id=CANDIDATE_ID

CRITICAL:
- Reply in the SAME LANGUAGE as the parent tweet
- NEVER use em dashes. Use commas, periods, or regular dashes (-)
- Use twitter_browser.py for posting; mcp__twitter-agent__* ONLY for reading
- our_url must always be our reply permalink (x.com/m13v_/status/...)
- At most 3 replies this run
- If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times)
- EXCEPTION to the retry rule: if twitter_browser.py returns {ok:false} with error in {rate_limited, tweet_not_found, reply_box_not_found}, do NOT retry. Mark the candidate 'skipped' (reason=that error) and move on. Retrying a rate_limited burns more X-side budget; the next cycle handles its own backoff." 2>&1 | tee -a "$LOG_FILE"

# --- No end-of-cycle expire ------------------------------------------------
# Pending rows are intentionally left alone. They are either:
#   - candidates Phase 2b never reached (e.g., org quota, browser crash, or
#     simply ran out of POST_LIMIT before reviewing the long tail), and the
#     next cycle's Phase 0 will salvage them while still fresh
#   - hard-expired by the next cycle's Phase 0 once they cross FRESHNESS_HOURS
# This avoids losing work to transient infra failures.

# --- Summary ---------------------------------------------------------------
SUMMARY=$(psql "$DATABASE_URL" -t -A -F '|' -c "
SELECT status, COUNT(*) FROM twitter_candidates WHERE batch_id='$BATCH_ID' GROUP BY status
" 2>/dev/null)
log "Batch summary: $SUMMARY"

# --- Persist to run_monitor.log so Job History picks up Twitter Post rows ---
POSTED_CT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM twitter_candidates WHERE batch_id='$BATCH_ID' AND status='posted'" 2>/dev/null || echo 0)
SKIPPED_CT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM twitter_candidates WHERE batch_id='$BATCH_ID' AND status IN ('skipped','expired')" 2>/dev/null || echo 0)
_COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START" --scripts "run-twitter-cycle-scan" "run-twitter-cycle-post" 2>/dev/null || echo "0.0000")

# --- Phase 2b failure-reason detection -------------------------------------
# When POSTED_CT=0 but Phase 2b had candidates to work with, scan the cycle
# log for known error markers so the dashboard renders an actual reason
# instead of a silent "—". Reason keys are kept consistent with the unified
# failure_reasons schema (engage_reddit.py, engage_github, etc.) so a single
# rendering pass in bin/server.js works across all jobs.
FAILED_CT=0
FAILURE_REASONS=""
if [ "${POSTED_CT:-0}" = "0" ] && [ "${CANDIDATE_COUNT:-0}" -gt 0 ]; then
    # Anchor on the Phase 2b marker so we don't false-positive on Phase 1
    # prose. macOS bash 3.2 has no associative arrays, so build the
    # comma-separated reasons string directly with positional appends.
    PHASE2B_LOG=$(awk '/Phase 2b: Claude reviewing/,EOF' "$LOG_FILE" 2>/dev/null || echo "")
    add_reason() {
        # $1 = reason key, $2 = count
        FAILURE_REASONS="${FAILURE_REASONS:+$FAILURE_REASONS,}${1}:${2}"
        FAILED_CT=$(( FAILED_CT + $2 ))
    }
    # Anthropic 429 / monthly cap. Reason key matches engage_reddit.py.
    if echo "$PHASE2B_LOG" | grep -qiE '"api_error_status":429|hit your limit|monthly usage limit'; then
        add_reason monthly_limit 1
    fi
    # twitter-agent Playwright profile served auth redirect (the 14:45 case).
    if echo "$PHASE2B_LOG" | grep -qiE 'auth redirect|re-authenticat|browser profile.*auth|profile.*needs.*re-auth'; then
        add_reason auth_redirect 1
    fi
    # X-side hard signals from twitter_browser.py.
    if echo "$PHASE2B_LOG" | grep -qiE '"error":"rate_limited"|RATE_LIMITED_TWITTER'; then
        add_reason rate_limited 1
    fi
    if echo "$PHASE2B_LOG" | grep -qiE 'page.load.timeout|navigation timeout|timed out|Timeout exceeded'; then
        add_reason timeout 1
    fi
    if echo "$PHASE2B_LOG" | grep -qiE 'reply_box_not_found|tweet_not_found'; then
        add_reason posting_blocked 1
    fi
    # Fallback: candidates existed but nothing posted and no specific marker
    # surfaced. Better to render a generic reason than a silent "—".
    if [ -z "$FAILURE_REASONS" ]; then
        add_reason phase2b_silent 1
    fi
fi

LOG_ARGS=(--script "post_twitter" --posted "${POSTED_CT:-0}" --skipped "${SKIPPED_CT:-0}" --failed "$FAILED_CT" --salvaged "${SALVAGED:-0}" --cost "$_COST" --elapsed $(( $(date +%s) - RUN_START )))
[ -n "$FAILURE_REASONS" ] && LOG_ARGS+=(--failure-reasons "$FAILURE_REASONS")
python3 "$REPO_DIR/scripts/log_run.py" "${LOG_ARGS[@]}"

log "=== Cycle complete: $(date) ==="
find "$LOG_DIR" -name "twitter-cycle-*.log" -mtime +7 -delete 2>/dev/null || true
