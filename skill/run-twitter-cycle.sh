#!/bin/bash
# run-twitter-cycle.sh — Combined Twitter scan + post cycle.
#
# Phase 1 (t=0):
#   - weighted-sample 6 projects from config.json
#   - LLM drafts one search query per project (style from past top queries)
#   - scrape tweets via twitter-agent, enrich via fxtwitter -> T0 snapshot
#   - store all candidates with batch_id and search_topic
#
# Sleep 300s.
#
# Phase 2 (t=5m):
#   - re-fetch the same candidates via fxtwitter -> T1 snapshot + delta_score
#   - Claude reads top 5 by delta, drops unsuitable, posts top 3
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

[ -f "$REPO_DIR/.env" ] && source "$REPO_DIR/.env"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== Twitter Cycle (batch=$BATCH_ID): $(date) ==="

# --- Weighted project sample -------------------------------------------------
PROJECTS_JSON=$(python3 - <<'PY'
import json, os, random
c = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
projects = [p for p in c.get('projects', []) if p.get('weight', 0) > 0]
weights = [(p, p.get('weight', 0)) for p in projects]
k = 6
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
                'topics': p.get('topics', []),
                'twitter_topics': p.get('twitter_topics', []),
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
log "Phase 1: drafting queries and scraping tweets..."

claude -p "You are a Twitter hot-tweet scanner. Your ONLY job is to find high-engagement tweets happening RIGHT NOW that are relevant to one of our projects. Do NOT post anything.

## Step 1: Draft one search query per project

You have $(echo "$PROJECTS_JSON" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))') projects. Draft exactly ONE Twitter search query for each, tailored to that project's topic space.

Projects:
$PROJECTS_JSON

Past top-performing query STYLES (use these only as inspiration for phrasing, operators, and specificity level; do NOT copy keywords blindly, adapt them to each project):
$TOP_QUERIES_JSON

Query guidelines:
- Favor high engagement: include 'min_faves:50' for broad terms, 'min_faves:20' for narrower ones
- Favor discussions/opinions (people sharing experience, asking questions), not news/promos/giveaways
- Pick a query likely to surface tweets RELEVANT to that project's actual domain
- Mix it up each run, don't always use the same query for the same project
- Use the projects' topics/twitter_topics/description as grounding

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

3. Combine ALL extracted tweets from ALL queries into a single JSON array at the end.

CRITICAL RULES:
- Use ONLY mcp__twitter-agent__* tools for scraping
- Do NOT post, reply, like, or interact with any tweet
- Do NOT generate any reply content
- Output the final combined JSON array wrapped in a code block tagged \`\`\`json
- Each tweet object MUST include 'search_topic' (the query that found it) and 'matched_project' (the project name whose query found it)
- If a search fails or times out, skip it and continue to the next" 2>&1 | tee -a "$LOG_FILE" | python3 -c "
import sys, json, re
text = sys.stdin.read()
matches = re.findall(r'\`\`\`json\s*(\[.*?\])\s*\`\`\`', text, re.DOTALL)
if matches:
    tweets = json.loads(matches[-1])
    json.dump(tweets, open('$RAW_FILE', 'w'))
    print(f'Extracted {len(tweets)} tweets to $RAW_FILE', file=sys.stderr)
else:
    m = re.search(r'\[[\s\S]*\"tweetUrl\"[\s\S]*\]', text)
    if m:
        try:
            tweets = json.loads(m.group())
            json.dump(tweets, open('$RAW_FILE', 'w'))
            print(f'Extracted {len(tweets)} tweets to $RAW_FILE', file=sys.stderr)
        except:
            print('No valid JSON found', file=sys.stderr); exit(1)
    else:
        print('No tweet data found in output', file=sys.stderr); exit(1)
" 2>&1 | tee -a "$LOG_FILE"

EXTRACT_EXIT=${PIPESTATUS[2]:-1}
if [ "$EXTRACT_EXIT" -ne 0 ] || [ ! -f "$RAW_FILE" ]; then
    log "No tweets extracted in Phase 1. Aborting cycle."
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

# --- Sleep 5 min before T1 measurement --------------------------------------
log "Sleeping 300s before T1 re-measurement..."
sleep 300

# --- Phase 2a: re-fetch T1 engagement ---------------------------------------
log "Phase 2a: re-polling fxtwitter for T1 engagement..."
python3 "$REPO_DIR/scripts/fetch_twitter_t1.py" --batch-id "$BATCH_ID" 2>&1 | tee -a "$LOG_FILE"

# --- Phase 2b: Claude reads top 5 by delta, posts top 3 ---------------------
CANDIDATES=$(psql "$DATABASE_URL" -t -A -F '|' -c "
    SELECT id, tweet_url, author_handle, tweet_text, virality_score,
           COALESCE(delta_score, 0), matched_project, search_topic,
           likes_t1, retweets_t1, replies_t1, views_t1, author_followers,
           EXTRACT(EPOCH FROM (NOW() - tweet_posted_at))/3600
    FROM twitter_candidates
    WHERE batch_id='$BATCH_ID' AND status='pending' AND delta_score IS NOT NULL
    ORDER BY delta_score DESC
    LIMIT 5;
" 2>/dev/null || echo "")

if [ -z "$CANDIDATES" ]; then
    log "No candidates with delta scores. Marking batch expired."
    psql "$DATABASE_URL" -c "UPDATE twitter_candidates SET status='expired' WHERE batch_id='$BATCH_ID' AND status='pending'" 2>&1 | tee -a "$LOG_FILE"
    exit 0
fi

CANDIDATE_COUNT=$(echo "$CANDIDATES" | wc -l | tr -d ' ')
log "Top $CANDIDATE_COUNT candidates by delta selected for post review."

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

log "Phase 2b: Claude reviewing top candidates and posting up to 3..."

claude -p "You are the Social Autoposter.

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
Reply to up to 3 candidates (post limit). Pick the 3 with the strongest combination of high delta + genuinely relevant thread. Skip any candidate whose thread is off-topic, toxic, or low-quality.

For each chosen candidate:
1. Navigate to the candidate URL via mcp__twitter-agent__browser_navigate (read-only, to understand context)
2. Read the full thread
3. Draft a reply using the best engagement style. Keep it 1-2 sentences. NEVER use em dashes.
4. Post via the CDP script:
     python3 $REPO_DIR/scripts/twitter_browser.py reply \"CANDIDATE_URL\" \"YOUR_REPLY_TEXT\"
   It returns JSON. Parse reply_url. If reply_url is missing/invalid/doesn't match x.com/m13v_/status/, treat as FAILED: do NOT log, mark candidate 'failed' not 'posted'. NEVER use the parent URL as our_url.
5. Self-reply with project link:
     python3 $REPO_DIR/scripts/twitter_browser.py self-reply \"YOUR_REPLY_URL\" \"FOLLOW_UP_TEXT\" \"PROJECT_URL\"
   FOLLOW_UP_TEXT: 1 short casual sentence, lowercase, no hard sell, no em dashes. Match parent tweet's language.
   PROJECT_URL: exact URL from the project's config. Skip this step entirely if the matched project has no URL.
6. Log to database:
     python3 $REPO_DIR/scripts/log_post.py --platform twitter --thread-url CANDIDATE_URL --our-url REPLY_URL --our-content 'YOUR_REPLY_TEXT' --project MATCHED_PROJECT --thread-author AUTHOR --thread-title 'TWEET_TEXT' --engagement-style STYLE --language LANG
7. Parse post_id from log_post.py JSON output, then mark candidate:
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

log "=== Cycle complete: $(date) ==="
find "$LOG_DIR" -name "twitter-cycle-*.log" -mtime +7 -delete 2>/dev/null || true
