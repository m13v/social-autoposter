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
#   - mark remaining batch rows as expired
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
RUN_START=$(date +%s)

[ -f "$REPO_DIR/.env" ] && source "$REPO_DIR/.env"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== Twitter Cycle (batch=$BATCH_ID): $(date) ==="

# Serialize with other twitter-agent consumers (engage-twitter,
# dm-outreach-twitter, link-edit-twitter, engage-dm-replies --platform twitter,
# stats.sh Step 3). Without this, concurrent pipelines collide on the shared
# twitter-agent browser profile and scraping/posting aborts mid-run.
source "$REPO_DIR/skill/lock.sh"
acquire_lock "twitter-browser" 3600

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

# --- Phase 1: Claude drafts queries, scrapes tweets -------------------------
# JSON schema forces structured output. Eliminates the prose-drift failure mode
# where the scanner summarized instead of dumping the JSON array.
SCAN_SCHEMA='{"type":"object","properties":{"tweets":{"type":"array","items":{"type":"object","properties":{"handle":{"type":"string"},"text":{"type":"string"},"tweetUrl":{"type":"string"},"datetime":{"type":"string"},"replies":{"type":"integer"},"retweets":{"type":"integer"},"likes":{"type":"integer"},"views":{"type":"integer"},"bookmarks":{"type":"integer"},"search_topic":{"type":"string"},"matched_project":{"type":"string"}},"required":["handle","text","tweetUrl","datetime","replies","retweets","likes","views","bookmarks","search_topic","matched_project"]}}},"required":["tweets"]}'

log "Phase 1: drafting queries and scraping tweets..."

SCAN_OUTPUT=$("$REPO_DIR/scripts/run_claude.sh" "run-twitter-cycle-scan" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/twitter-agent-mcp.json" -p --output-format json --json-schema "$SCAN_SCHEMA" "You are a Twitter hot-tweet scanner. Your ONLY job is to find high-engagement tweets happening RIGHT NOW that are relevant to one of our projects. Do NOT post anything.

## Step 1: Draft one search query per project

You have $(echo "$PROJECTS_JSON" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))') projects. Draft exactly ONE Twitter search query for each, tailored to that project's topic space.

Projects:
$PROJECTS_JSON

Past top-performing query STYLES (use these only as inspiration for phrasing, operators, and specificity level; do NOT copy keywords blindly, adapt them to each project):
$TOP_QUERIES_JSON

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
        const text = tweetText ? tweetText.textContent.substring(0, 300) : '';
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

CRITICAL RULES:
- Use ONLY mcp__twitter-agent__* tools for scraping
- Do NOT post, reply, like, or interact with any tweet
- Do NOT generate any reply content
- If a search fails or times out, skip it and continue to the next" 2>&1)

# Dump the captured envelope to the cycle log for offline inspection.
echo "$SCAN_OUTPUT" >> "$LOG_FILE"

# Parse the structured-output envelope and write the tweets array to $RAW_FILE.
# claude -p --output-format json wraps results as {"structured_output": {...}, ...}.
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
tweets = so.get('tweets', []) if isinstance(so, dict) else []
if not tweets:
    print('No tweets in structured_output.tweets', file=sys.stderr); sys.exit(1)
json.dump(tweets, open('$RAW_FILE', 'w'))
print(f'Extracted {len(tweets)} tweets to $RAW_FILE', file=sys.stderr)
" <<< "$SCAN_OUTPUT" 2>&1 | tee -a "$LOG_FILE"

EXTRACT_EXIT=${PIPESTATUS[0]:-1}
if [ "$EXTRACT_EXIT" -ne 0 ] || [ ! -f "$RAW_FILE" ]; then
    log "No tweets extracted in Phase 1. Aborting cycle."
    python3 "$REPO_DIR/scripts/log_run.py" --script "post_twitter" --posted 0 --skipped 0 --failed 1 --cost 0 --elapsed $(( $(date +%s) - RUN_START ))
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
           EXTRACT(EPOCH FROM (NOW() - tweet_posted_at))/3600
    FROM twitter_candidates
    WHERE batch_id='$BATCH_ID' AND status='pending' AND delta_score >= 1
    ORDER BY delta_score DESC
    LIMIT 10;
" 2>/dev/null || echo "")

if [ -z "$CANDIDATES" ]; then
    log "No candidates with delta scores. Marking batch expired."
    psql "$DATABASE_URL" -c "UPDATE twitter_candidates SET status='expired' WHERE batch_id='$BATCH_ID' AND status='pending'" 2>&1 | tee -a "$LOG_FILE"
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
while IFS='|' read -r cid curl cauthor ctext cscore cdelta cproject ctopic clikes crts creplies cviews cfollowers cage; do
    CANDIDATE_BLOCK="${CANDIDATE_BLOCK}
---
Candidate ID: $cid
URL: $curl
Author: @$cauthor (${cfollowers} followers)
Text: $ctext
Score: $cscore | Delta (5min): $cdelta | Likes: $clikes | RTs: $crts | Replies: $creplies | Views: $cviews | Age: ${cage}h
Search query: $ctopic
Project match: $cproject
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
3. Draft a reply using the best engagement style. Keep it 1-2 sentences. NEVER use em dashes. Apply the matched project's \`voice\` block from ALL_PROJECTS_JSON above: follow \`voice.tone\`, never violate any item in \`voice.never\`, and mirror \`voice.examples\` / \`voice.examples_good\` when present.
4. Post via the CDP script:
     python3 $REPO_DIR/scripts/twitter_browser.py reply \"CANDIDATE_URL\" \"YOUR_REPLY_TEXT\"
   It returns JSON. Parse reply_url. If reply_url is missing/invalid/doesn't match x.com/m13v_/status/, treat as FAILED: do NOT log, mark candidate 'failed' not 'posted'. NEVER use the parent URL as our_url.
5. Log the primary reply to the database FIRST, BEFORE attempting the self-reply. This guarantees the row exists even if the self-reply crashes; the link-edit-twitter sweep will pick it up later. Parse post_id from the JSON output:
     python3 $REPO_DIR/scripts/log_post.py --platform twitter --thread-url CANDIDATE_URL --our-url REPLY_URL --our-content 'YOUR_REPLY_TEXT' --project MATCHED_PROJECT --thread-author AUTHOR --thread-title 'TWEET_TEXT' --engagement-style STYLE --language LANG
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
- If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times)" 2>&1 | tee -a "$LOG_FILE"

# --- Cleanup: mark remaining pending batch rows as expired ------------------
log "Marking remaining batch rows as expired..."
psql "$DATABASE_URL" -c "UPDATE twitter_candidates SET status='expired' WHERE batch_id='$BATCH_ID' AND status='pending'" 2>&1 | tee -a "$LOG_FILE"

# --- Summary ---------------------------------------------------------------
SUMMARY=$(psql "$DATABASE_URL" -t -A -F '|' -c "
SELECT status, COUNT(*) FROM twitter_candidates WHERE batch_id='$BATCH_ID' GROUP BY status
" 2>/dev/null)
log "Batch summary: $SUMMARY"

# --- Persist to run_monitor.log so Job History picks up Twitter Post rows ---
POSTED_CT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM twitter_candidates WHERE batch_id='$BATCH_ID' AND status='posted'" 2>/dev/null || echo 0)
SKIPPED_CT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM twitter_candidates WHERE batch_id='$BATCH_ID' AND status IN ('skipped','expired')" 2>/dev/null || echo 0)
python3 "$REPO_DIR/scripts/log_run.py" --script "post_twitter" --posted "${POSTED_CT:-0}" --skipped "${SKIPPED_CT:-0}" --failed 0 --cost 0 --elapsed $(( $(date +%s) - RUN_START ))

log "=== Cycle complete: $(date) ==="
find "$LOG_DIR" -name "twitter-cycle-*.log" -mtime +7 -delete 2>/dev/null || true
