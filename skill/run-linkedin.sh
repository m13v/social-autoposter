#!/bin/bash
# Social Autoposter - LinkedIn posting (Phase A discover+score, Phase B post)
#
# Phase A (discovery + scoring, ~$10-15 target): pick a project, consult
#   top/dud query history, draft 2-3 dynamic search queries, browse the
#   LinkedIn SERPs, extract engagement metrics (reactions/comments/reposts/
#   age/author) for every visible candidate, score serp quality, write a
#   structured JSON envelope to a tmp file, STOP. Bash then pipes the
#   envelope into:
#     - log_linkedin_search_attempts.py (records every query, including
#       zero-result and low-quality, so duds get blocked next cycle)
#     - score_linkedin_candidates.py    (computes velocity + virality, upserts
#       into linkedin_candidates, dedupes against engaged URN history)
#   Bash then SELECTs the top pending candidate by velocity_score.
#
# Phase B (compose + post + verify + log, ~$10-15 target): given Phase A's
#   chosen candidate (already in linkedin_candidates), navigate straight to
#   the URL, defensively re-check engaged-ids, draft using the project's
#   voice block + engagement styles + top performers report, post via
#   mcp__linkedin-agent, verify (network + DOM), log via log_post.py, mark
#   the candidate row 'posted' (or 'skipped' on rejection), STOP.
#
# Differences vs the pre-2026-04-29 shape:
#   - Phase A extracts ENGAGEMENT (not just URN); we don't fly blind anymore
#   - Phase A logs every search query (positive, zero, low-quality SERP) so
#     the LLM learns which phrasings work and which to retire
#   - Phase B reads its candidate from linkedin_candidates (DB-backed),
#     not from a file, so the same candidate isn't picked twice across runs

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-linkedin-$(date +%Y-%m-%d_%H%M%S).log"
RUN_START_EPOCH=$(date +%s)
BATCH_ID="li-$(date +%Y%m%d_%H%M%S)-$$"

echo "=== LinkedIn Post Run: $(date) (batch=$BATCH_ID) ===" | tee "$LOG_FILE"

# Hold the linkedin-browser lock for the entire run. Phase A's claude exits
# (closing its Chrome) before Phase B's claude starts, so the profile dir is
# free for Phase B's fresh MCP session — but we must NOT release the shell
# lock between phases or a sibling pipeline would steal the browser.
source "$REPO_DIR/skill/lock.sh"
acquire_lock "linkedin-browser" 3600
ensure_browser_healthy "linkedin"

# ===== Phase A: discovery + scoring =====
PROJECT_DIST=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform linkedin --distribution 2>/dev/null || echo "(distribution unavailable)")

# Slim project list: name + description + qualification + search_topics.
# Phase A needs search_topics so the LLM can draft per-project queries.
PROJECTS_SLIM_JSON=$(python3 -c "
import json, os
config = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
slim = {}
for p in config.get('projects', []):
    if 'linkedin' in (p.get('platforms_disabled') or []):
        continue
    rec = {k: p[k] for k in ('name','description','qualification') if k in p}
    rec['search_topics'] = p.get('search_topics') or p.get('linkedin_topics') or p.get('topics') or []
    slim[p['name']] = rec
print(json.dumps(slim, indent=2))
" 2>/dev/null || echo "{}")

# Top-performing historical queries (positive signal, last 30d).
TOP_QUERIES=$(python3 "$REPO_DIR/scripts/top_linkedin_queries.py" --limit 15 --window-days 30 2>/dev/null || echo "[]")

# Dud queries to AVOID redrafting (zero-result OR low-SERP, last 7d).
DUD_QUERIES=$(python3 "$REPO_DIR/scripts/top_dud_linkedin_queries.py" --limit 30 --window-days 7 2>/dev/null || echo "[]")

# BSD mktemp on macOS only substitutes XXXXXX at the end of the template.
PHASE_A_OUT=$(mktemp /tmp/sa-run-linkedin-phaseA-XXXXXX)
PHASE_A_PROMPT=$(mktemp /tmp/sa-run-linkedin-phaseA-prompt-XXXXXX)

cat > "$PHASE_A_PROMPT" <<PROMPT_EOF
You are the Social Autoposter LinkedIn discovery + scoring scout (Phase A).

Your job: pick ONE project, draft 2-3 DYNAMIC search queries informed by
historical performance, browse each query's LinkedIn SERP, extract
engagement metrics for every visible candidate post, write a structured
JSON envelope to $PHASE_A_OUT, and STOP. Do NOT draft a comment. Do NOT
post anything. Phase B handles drafting + posting using whatever you write
to the candidates list.

## Project candidates (only LinkedIn-eligible projects shown)
$PROJECTS_SLIM_JSON

## Today's distribution (prefer underrepresented projects)
$PROJECT_DIST

## Top-performing historical queries (last 30 days, sorted by posts produced)
These are STYLE inspiration only — do NOT reuse them verbatim. LinkedIn
SERPs shift daily, so reusing the exact same phrasing is wasteful. Mine
them for the angle/keyword combo that worked, then craft something new.
$TOP_QUERIES

## DUD queries to AVOID (last 7 days, zero-result or low-SERP-quality)
Do NOT redraft any of these phrasings. They have been flat or
audience-wrong recently. Note the 'reason' field — 'zero_results' means
LinkedIn rejected the keywords; 'low_serp_quality' means results came
back but were influencer slop / off-target audience.
$DUD_QUERIES

## Workflow

1. Pick ONE underrepresented project from the distribution that has a
   plausible content fit. Do NOT iterate through many projects.

2. Draft 2-3 search queries for the chosen project. Each query should:
   - Be 2-4 words (LinkedIn search hates long phrases)
   - Target practitioners, not influencers (no "expert tips", "thought
     leadership", or buzzwordy phrasing)
   - Be FRESH — different from the dud list, different angle from the
     top-performers list (steal the recipe, change the dish)
   - Map to the project's search_topics

   Hard cap: at most 3 queries this run. The whole point is to converge
   fast.

3. For EACH query:
   3a. Build the SERP URL:
       https://www.linkedin.com/search/results/content/?keywords=ENCODED_QUERY&sortBy=date_posted
   3b. mcp__linkedin-agent__browser_navigate to that URL.
   3c. mcp__linkedin-agent__browser_run_code to extract every visible
       result with its engagement signals:

\`\`\`javascript
async (page) => {
  await page.waitForTimeout(3500);
  return await page.evaluate(() => {
    // LinkedIn renders search results as 'feed-shared-update-v2' or similar.
    // Walk every distinct post container on the page.
    const out = [];
    const containers = document.querySelectorAll(
      'div.feed-shared-update-v2, div[data-urn*="urn:li:activity"], div[data-urn*="urn:li:share"], div[data-urn*="urn:li:ugcPost"]'
    );
    const seenUrns = new Set();
    const re = /(activity|share|ugcPost)[:_-](\\d{16,19})/gi;

    function parseRelativeAge(txt) {
      // LinkedIn renders "5h", "2d", "3w", "1mo", "Just now"
      if (!txt) return null;
      const m = txt.match(/(\\d+)\\s*(s|m|h|d|w|mo|y)/i);
      if (!m) return null;
      const n = parseInt(m[1], 10);
      const unit = m[2].toLowerCase();
      const map = { s: 1/3600, m: 1/60, h: 1, d: 24, w: 24*7, mo: 24*30, y: 24*365 };
      return n * (map[unit] || 0);
    }

    function parseCount(txt) {
      if (!txt) return 0;
      const t = txt.replace(/,/g, '').trim();
      const m = t.match(/([\\d.]+)\\s*([KkMm]?)/);
      if (!m) return 0;
      const n = parseFloat(m[1]);
      const mult = m[2].toLowerCase() === 'k' ? 1000 : (m[2].toLowerCase() === 'm' ? 1_000_000 : 1);
      return Math.round(n * mult);
    }

    containers.forEach(el => {
      // URN extraction: pull from data-urn attr first, then walk attrs+text
      let activityId = null;
      const urns = new Set();
      const dataUrn = el.getAttribute('data-urn') || '';
      let m;
      re.lastIndex = 0;
      while ((m = re.exec(dataUrn)) !== null) {
        urns.add(m[2]);
        if (m[1].toLowerCase() === 'activity' && !activityId) activityId = m[2];
      }
      // Fallback: walk descendants
      if (!activityId) {
        el.querySelectorAll('[data-urn], a[href*="urn:li"], a[href*="/feed/update/"]').forEach(d => {
          const v = (d.getAttribute('data-urn') || d.getAttribute('href') || '');
          re.lastIndex = 0;
          let mm;
          while ((mm = re.exec(v)) !== null) {
            urns.add(mm[2]);
            if (mm[1].toLowerCase() === 'activity' && !activityId) activityId = mm[2];
          }
        });
      }
      if (!activityId || seenUrns.has(activityId)) return;
      seenUrns.add(activityId);

      // Author
      const authorAnchor = el.querySelector('a[href*="/in/"], a[data-control-name*="actor"]');
      const authorName = (el.querySelector('.update-components-actor__name, span.feed-shared-actor__name')?.textContent || '').trim();
      const authorUrl = authorAnchor ? authorAnchor.href : null;
      // Followers (sometimes shown as "X followers" beneath name)
      let authorFollowers = 0;
      const supplementary = el.querySelector('.update-components-actor__supplementary-actor-info, .feed-shared-actor__sub-description');
      if (supplementary) {
        const fm = (supplementary.textContent || '').match(/([\\d.,]+[KkMm]?)\\s*follower/);
        if (fm) authorFollowers = parseCount(fm[1]);
      }

      // Post text excerpt
      const textEl = el.querySelector('.update-components-text, .feed-shared-update-v2__description, span.break-words');
      const postText = (textEl ? textEl.textContent : '').trim().slice(0, 500);

      // Age
      const timeEl = el.querySelector('time, .update-components-actor__sub-description, span.feed-shared-actor__sub-description');
      const ageText = timeEl ? timeEl.textContent.trim() : '';
      const ageHours = parseRelativeAge(ageText);

      // Engagement: reactions, comments, reposts
      const social = el.querySelector('.social-details-social-counts, .social-action-counts, .update-v2-social-activity');
      let reactions = 0, comments = 0, reposts = 0;
      if (social) {
        // Reactions: look for the social-counts__reactions item
        const reactEl = social.querySelector('[aria-label*="reaction" i], .social-details-social-counts__reactions-count');
        if (reactEl) reactions = parseCount(reactEl.textContent || reactEl.getAttribute('aria-label') || '');
        // Comments
        const commentEl = social.querySelector('[aria-label*="comment" i], li.social-details-social-counts__comments');
        if (commentEl) comments = parseCount(commentEl.textContent || commentEl.getAttribute('aria-label') || '');
        // Reposts
        const repostEl = social.querySelector('[aria-label*="repost" i], li.social-details-social-counts__item--right-aligned');
        if (repostEl) reposts = parseCount(repostEl.textContent || repostEl.getAttribute('aria-label') || '');
      }

      out.push({
        post_url: 'https://www.linkedin.com/feed/update/urn:li:activity:' + activityId + '/',
        activity_id: activityId,
        all_urns: Array.from(urns),
        author_name: authorName || null,
        author_profile_url: authorUrl,
        author_followers: authorFollowers || null,
        post_text: postText,
        age_hours: ageHours,
        reactions: reactions,
        comments: comments,
        reposts: reposts,
        age_text: ageText
      });
    });
    return out;
  });
}
\`\`\`

   3d. RATE THE SERP QUALITY 0-10 for THIS query, based on:
       - Practitioner ratio: % of authors with < 50K followers (higher = better)
       - Topic fit: do the post excerpts actually match the project's domain?
       - Freshness: median age_hours of results (lower = better)
       - 0-3 = useless slop, 4-5 = mixed, 6-8 = mostly relevant, 9-10 = goldmine
       Write the score into the queries_used record (see envelope below).

   3e. SKIP candidates authored by Matthew Diakonov / linkedin.com/in/m13v/.

   3f. SKIP candidates we already engaged on. Run:
         python3 $REPO_DIR/scripts/linkedin_url.py --check-engaged-ids 'comma,sep,urns'
       For each candidate, check its all_urns set; if ANY URN already
       engaged, drop the candidate. (Phase B will defensive-recheck.)

4. After all queries are scraped, write the envelope to $PHASE_A_OUT and STOP:

\`\`\`bash
cat > $PHASE_A_OUT <<JSON_EOF
{
  "project": "PROJECT_NAME",
  "language": "en",
  "queries_used": [
    {"query": "ai agents production",   "candidates_found": 4, "serp_quality_score": 7.5},
    {"query": "macos automation tools", "candidates_found": 0, "serp_quality_score": null},
    {"query": "claude code workflow",   "candidates_found": 6, "serp_quality_score": 5.0}
  ],
  "candidates": [
    {
      "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:NUMERIC/",
      "activity_id": "NUMERIC",
      "all_urns": ["NUMERIC", "..."],
      "author_name": "First Last",
      "author_profile_url": "https://www.linkedin.com/in/SLUG/",
      "author_followers": 12345,
      "post_text": "first 500 chars, no newlines, no double quotes",
      "age_hours": 6.5,
      "reactions": 42,
      "comments": 7,
      "reposts": 3,
      "search_query": "ai agents production",
      "language": "en",
      "serp_quality_score": 7.5
    }
  ]
}
JSON_EOF
\`\`\`

   - queries_used MUST contain ONE row per query you ran (including
     zero-result ones — that is the whole point of the dud-learning).
   - candidates can be empty; bash will skip Phase B cleanly.
   - candidates must NOT include posts you already engaged on or self-authored.
   - post_text and post excerpts must be safe to embed in a bash double-quoted
     string. Strip backticks, double quotes, and newlines before writing.

Then say '## Phase A: envelope written' and STOP.

CRITICAL: Use ONLY mcp__linkedin-agent__* tools. NEVER click the comment
textbox. NEVER call createComment. NEVER navigate to a post-compose flow.
Phase B does all of that.
CRITICAL: Hard cap of 3 search queries. Quality > quantity.
CRITICAL: NEVER use em dashes anywhere.
PROMPT_EOF

set +e
"$REPO_DIR/scripts/run_claude.sh" "run-linkedin-phaseA" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json" -p "$(cat "$PHASE_A_PROMPT")" 2>&1 | tee -a "$LOG_FILE"
PA_RC=${PIPESTATUS[0]}
set -e
rm -f "$PHASE_A_PROMPT"

# ===== Validate Phase A envelope + run Python ingest steps =====
if [ "$PA_RC" -ne 0 ] || [ ! -s "$PHASE_A_OUT" ]; then
  echo "Phase A: no envelope (rc=$PA_RC, $([ -s "$PHASE_A_OUT" ] && echo 'file non-empty' || echo 'file empty')). Skipping Phase B." | tee -a "$LOG_FILE"
  rm -f "$PHASE_A_OUT"
  ELAPSED=$(( $(date +%s) - RUN_START_EPOCH ))
  _COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START_EPOCH" --scripts "run-linkedin-phaseA" "run-linkedin-phaseB" 2>/dev/null || echo "0.0000")
  python3 "$REPO_DIR/scripts/log_run.py" --script post_linkedin --posted 0 --skipped 1 --failed 0 --cost "$_COST" --elapsed "$ELAPSED" || true
  echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
  find "$LOG_DIR" -name "run-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true
  exit 0
fi

# Validate the envelope is well-formed JSON; if it isn't, ledger the run
# as failed and skip Phase B rather than crashing the ingest scripts.
if ! python3 -c "import json,sys; json.load(open('$PHASE_A_OUT'))" 2>/dev/null; then
  echo "Phase A: envelope is malformed JSON; skipping Phase B." | tee -a "$LOG_FILE"
  rm -f "$PHASE_A_OUT"
  ELAPSED=$(( $(date +%s) - RUN_START_EPOCH ))
  _COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START_EPOCH" --scripts "run-linkedin-phaseA" "run-linkedin-phaseB" 2>/dev/null || echo "0.0000")
  python3 "$REPO_DIR/scripts/log_run.py" --script post_linkedin --posted 0 --skipped 0 --failed 1 --cost "$_COST" --elapsed "$ELAPSED" || true
  echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
  exit 0
fi

PA_PROJECT=$(python3 -c "import json; print(json.load(open('$PHASE_A_OUT')).get('project',''))" 2>/dev/null || echo "")

# Ingest queries_used into linkedin_search_attempts (one row per query, dud-aware).
python3 -c "
import json
env = json.load(open('$PHASE_A_OUT'))
project = env.get('project','')
out = []
for q in env.get('queries_used') or []:
    out.append({
        'query': q.get('query',''),
        'project': project,
        'candidates_found': q.get('candidates_found') or 0,
        'serp_quality_score': q.get('serp_quality_score'),
    })
import sys; json.dump(out, sys.stdout)
" | python3 "$REPO_DIR/scripts/log_linkedin_search_attempts.py" --batch-id "$BATCH_ID" 2>&1 | tee -a "$LOG_FILE" || true

# Ingest candidates into linkedin_candidates (scored + deduped).
# Stamp serp_quality_score onto each candidate from its parent query so the
# scoring upsert has the per-row signal even though SERP quality is judged
# per-query.
python3 -c "
import json
env = json.load(open('$PHASE_A_OUT'))
quality_by_query = {q.get('query',''): q.get('serp_quality_score') for q in env.get('queries_used') or []}
project = env.get('project','')
lang = env.get('language','en')
cands = []
for c in env.get('candidates') or []:
    if not isinstance(c, dict):
        continue
    c.setdefault('matched_project', project)
    c.setdefault('language', lang)
    if c.get('serp_quality_score') is None:
        c['serp_quality_score'] = quality_by_query.get(c.get('search_query',''))
    cands.append(c)
import sys; json.dump(cands, sys.stdout)
" | python3 "$REPO_DIR/scripts/score_linkedin_candidates.py" --batch-id "$BATCH_ID" 2>&1 | tee -a "$LOG_FILE" || true

# ===== Pick top pending candidate from this batch (or fallback to global pending) =====
# We try the freshest batch first so a high-velocity post we just discovered
# wins over an older pending row that didn't get posted last cycle. If the
# fresh batch has zero usable rows (everything we saw was already engaged),
# fall back to the broader pending pool — that pool would otherwise just
# expire after MAX_AGE_HOURS without ever being attempted.
PA_PICK=$(python3 -c "
import json, sys
sys.path.insert(0, '$REPO_DIR/scripts')
import db as dbmod
conn = dbmod.get_conn()
row = conn.execute('''
    SELECT post_url, activity_id, all_urns, author_name, author_profile_url,
           post_text, language, matched_project, velocity_score, search_query
    FROM linkedin_candidates
    WHERE status='pending' AND batch_id=%s
    ORDER BY velocity_score DESC NULLS LAST, discovered_at DESC
    LIMIT 1
''', ['$BATCH_ID']).fetchone()
if not row:
    row = conn.execute('''
        SELECT post_url, activity_id, all_urns, author_name, author_profile_url,
               post_text, language, matched_project, velocity_score, search_query
        FROM linkedin_candidates
        WHERE status='pending' AND age_hours <= 96
        ORDER BY velocity_score DESC NULLS LAST, discovered_at DESC
        LIMIT 1
    ''').fetchone()
conn.close()
if not row:
    print(json.dumps({}))
else:
    out = {
        'post_url': row[0],
        'activity_id': row[1],
        'all_urns': row[2] or '',
        'author_name': row[3] or '',
        'author_profile_url': row[4] or '',
        'post_text': (row[5] or '')[:500],
        'language': row[6] or 'en',
        'project': row[7] or '$PA_PROJECT',
        'velocity_score': float(row[8] or 0),
        'search_query': row[9] or '',
    }
    print(json.dumps(out))
" 2>/dev/null || echo "{}")

PA_URL=$(echo "$PA_PICK" | python3 -c "import json,sys; print(json.load(sys.stdin).get('post_url',''))")
PA_ACTIVITY_ID=$(echo "$PA_PICK" | python3 -c "import json,sys; print(json.load(sys.stdin).get('activity_id',''))")
PA_ALL_URNS=$(echo "$PA_PICK" | python3 -c "import json,sys; print(json.load(sys.stdin).get('all_urns',''))")
PA_AUTHOR_NAME=$(echo "$PA_PICK" | python3 -c "import json,sys; print(json.load(sys.stdin).get('author_name',''))")
PA_AUTHOR_URL=$(echo "$PA_PICK" | python3 -c "import json,sys; print(json.load(sys.stdin).get('author_profile_url',''))")
PA_EXCERPT=$(echo "$PA_PICK" | python3 -c "import json,sys; print(json.load(sys.stdin).get('post_text',''))")
PA_LANG=$(echo "$PA_PICK" | python3 -c "import json,sys; print(json.load(sys.stdin).get('language','en'))")
PA_TITLE_HINT=$(echo "$PA_PICK" | python3 -c "import json,sys; v=json.load(sys.stdin).get('post_text',''); print((v or '').split('\\n')[0][:80])")
PA_VELOCITY=$(echo "$PA_PICK" | python3 -c "import json,sys; print(json.load(sys.stdin).get('velocity_score',0))")
PA_QUERY=$(echo "$PA_PICK" | python3 -c "import json,sys; print(json.load(sys.stdin).get('search_query',''))")
[ -z "${PA_PROJECT:-}" ] && PA_PROJECT=$(echo "$PA_PICK" | python3 -c "import json,sys; print(json.load(sys.stdin).get('project',''))")

# ===== If no candidate, exit cleanly =====
if [ -z "$PA_ACTIVITY_ID" ] || [ -z "$PA_URL" ]; then
  echo "Phase A: no postable candidate after scoring (project='$PA_PROJECT'). Skipping Phase B." | tee -a "$LOG_FILE"
  rm -f "$PHASE_A_OUT"
  ELAPSED=$(( $(date +%s) - RUN_START_EPOCH ))
  _COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START_EPOCH" --scripts "run-linkedin-phaseA" "run-linkedin-phaseB" 2>/dev/null || echo "0.0000")
  python3 "$REPO_DIR/scripts/log_run.py" --script post_linkedin --posted 0 --skipped 1 --failed 0 --cost "$_COST" --elapsed "$ELAPSED" || true
  echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
  exit 0
fi

# activity_id must be 16-19 digit numeric.
case "$PA_ACTIVITY_ID" in
  ''|*[!0-9]*)
    echo "Phase A picked non-numeric activity_id '$PA_ACTIVITY_ID'. Skipping Phase B." | tee -a "$LOG_FILE"
    rm -f "$PHASE_A_OUT"
    ELAPSED=$(( $(date +%s) - RUN_START_EPOCH ))
    _COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START_EPOCH" --scripts "run-linkedin-phaseA" "run-linkedin-phaseB" 2>/dev/null || echo "0.0000")
    python3 "$REPO_DIR/scripts/log_run.py" --script post_linkedin --posted 0 --skipped 0 --failed 1 --cost "$_COST" --elapsed "$ELAPSED" || true
    echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
    exit 0
    ;;
esac

# Canonicalize URL from activity_id.
PA_URL="https://www.linkedin.com/feed/update/urn:li:activity:${PA_ACTIVITY_ID}/"

echo "Phase A: chose project=$PA_PROJECT activity=$PA_ACTIVITY_ID velocity=$PA_VELOCITY query='$PA_QUERY'" | tee -a "$LOG_FILE"

# Look up the chosen project's full config (only this one).
PROJECT_FULL=$(python3 -c "
import json, os
c = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
p = next((p for p in c.get('projects',[]) if p['name']=='$PA_PROJECT'), {})
print(json.dumps(p, indent=2))
")

# Phase B inputs (only Phase B needs styles + top performers).
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform linkedin 2>/dev/null || echo "(top performers report unavailable)")
source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block linkedin posting)

# Allow Chrome's profile lockfile to release between phases.
sleep 3

# ===== Phase B: compose + post + verify + log =====
PHASE_B_PROMPT=$(mktemp /tmp/sa-run-linkedin-phaseB-prompt-XXXXXX)
cat > "$PHASE_B_PROMPT" <<PROMPT_EOF
You are the Social Autoposter (Phase B). Your job: post ONE comment on a
pre-selected LinkedIn post (already chosen + scored by Phase A), verify it
landed, log it. STOP. Do NOT search for other candidates.

Read $SKILL_FILE for tone and content rules.

## Pre-selected candidate (from Phase A — DO NOT rediscover)
- Project: **$PA_PROJECT**
- Thread URL: $PA_URL
- Activity URN: $PA_ACTIVITY_ID
- All URNs already seen: $PA_ALL_URNS
- Author: $PA_AUTHOR_NAME ($PA_AUTHOR_URL)
- Post excerpt: $PA_EXCERPT
- Post title hint: $PA_TITLE_HINT
- Language: $PA_LANG
- Velocity score: $PA_VELOCITY (Phase A picked this as the top candidate)
- Search query that surfaced it: '$PA_QUERY'

## Project config
$PROJECT_FULL

## Top performers feedback (use to pick a comment angle)
$TOP_REPORT

$STYLES_BLOCK

## Workflow

1. Navigate to $PA_URL via mcp__linkedin-agent__browser_navigate.

2. Defensive engaged-id re-check (Phase A may have missed a URN that only
   surfaces after the post page fully loads). Walk the rendered DOM for ALL
   URNs (activity, share, ugcPost forms), merge with '$PA_ALL_URNS', and run:
     python3 $REPO_DIR/scripts/linkedin_url.py --check-engaged-ids 'MERGED_URNS'
   If exit code 0 (already engaged), mark the candidate skipped:
     python3 -c "import sys; sys.path.insert(0,'$REPO_DIR/scripts'); import db; c=db.get_conn(); c.execute(\"UPDATE linkedin_candidates SET status='skipped' WHERE activity_id=%s\", ['$PA_ACTIVITY_ID']); c.commit(); c.close()"
   then STOP with '## Already engaged (defensive catch in Phase B)'.

3. Pick the engagement style that best fits the post + project's voice
   block (apply voice.tone, never violate voice.never, mirror voice.examples
   if present). Reply in $PA_LANG.
   NEVER use em dashes.

4. Post the comment via mcp__linkedin-agent (find textbox, click, type, submit).

5. POST-SUBMIT VERIFICATION (mandatory).
   5a. mcp__linkedin-agent__browser_network_requests with:
         filter: 'normCommentsCreate|normComments|contentcreation|socialActions'
         requestBody: true
         static: false
       Save response verbatim as NETWORK_RESPONSE.
   5b. Walk NETWORK_RESPONSE for every 16-19 digit URN, dedupe with the
       seed URN list above into ALL_POST_URNS (comma-separated).
   5c. mcp__linkedin-agent__browser_take_screenshot for the toast.
   5d. mcp__linkedin-agent__browser_snapshot (depth 12). Check:
         (a) comment count went up by at least 1
         (b) a fresh comment by 'Matthew Diakonov' / 'You' is rendered
         (c) NO 'could not be created' toast
         (d) editor textbox cleared
   5e. SUCCESS = all four pass. REJECTED = toast present OR count unchanged.

6. If REJECTED, do NOT call the success log path. Mark candidate skipped:
     python3 -c "import sys; sys.path.insert(0,'$REPO_DIR/scripts'); import db; c=db.get_conn(); c.execute(\"UPDATE linkedin_candidates SET status='skipped' WHERE activity_id=%s\", ['$PA_ACTIVITY_ID']); c.commit(); c.close()"
   Then ledger the soft-block:
     python3 $REPO_DIR/scripts/log_post.py --rejected \\
       --platform linkedin \\
       --thread-url '$PA_URL' \\
       --our-content 'YOUR_COMMENT_TEXT' \\
       --project '$PA_PROJECT' \\
       --thread-author '$PA_AUTHOR_NAME' \\
       --thread-title '$PA_TITLE_HINT' \\
       --engagement-style STYLE_YOU_CHOSE \\
       --language '$PA_LANG' \\
       --rejection-reason 'TOAST: <verbatim toast text or quiet-fail>' \\
       --network-response 'NETWORK_RESPONSE'
   Then STOP with '## Comment soft-blocked, ledgered'.

7. If SUCCESS, log the post and mark candidate posted:
     python3 $REPO_DIR/scripts/log_post.py \\
       --platform linkedin \\
       --thread-url '$PA_URL' \\
       --our-url '$PA_URL' \\
       --our-content 'YOUR_COMMENT_TEXT' \\
       --project '$PA_PROJECT' \\
       --thread-author '$PA_AUTHOR_NAME' \\
       --thread-title '$PA_TITLE_HINT' \\
       --engagement-style STYLE_YOU_CHOSE \\
       --language '$PA_LANG' \\
       --urns 'ALL_POST_URNS'
     python3 -c "import sys; sys.path.insert(0,'$REPO_DIR/scripts'); import db; c=db.get_conn(); c.execute(\"UPDATE linkedin_candidates SET status='posted', posted_at=NOW() WHERE activity_id=%s\", ['$PA_ACTIVITY_ID']); c.commit(); c.close()"

CRITICAL: ONE post only. If anything fails, STOP — do NOT pick another candidate.
CRITICAL: Use ONLY mcp__linkedin-agent__* tools.
CRITICAL: NEVER use em dashes.
PROMPT_EOF

set +e
"$REPO_DIR/scripts/run_claude.sh" "run-linkedin-phaseB" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json" -p "$(cat "$PHASE_B_PROMPT")" 2>&1 | tee -a "$LOG_FILE"
PB_RC=${PIPESTATUS[0]}
set -e
rm -f "$PHASE_B_PROMPT"
rm -f "$PHASE_A_OUT"

# ===== Persist run-level summary =====
ELAPSED=$(( $(date +%s) - RUN_START_EPOCH ))
WINDOW_SEC=$(( ELAPSED + 60 ))
POSTED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE platform='linkedin' AND posted_at >= NOW() - interval '$WINDOW_SEC seconds'" 2>/dev/null | tr -d '[:space:]' || true)
[ -z "$POSTED" ] && POSTED=0
FAILED=0
if [ "$PB_RC" -ne 0 ] && [ "$POSTED" = "0" ]; then FAILED=1; fi
_COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START_EPOCH" --scripts "run-linkedin-phaseA" "run-linkedin-phaseB" 2>/dev/null || echo "0.0000")
python3 "$REPO_DIR/scripts/log_run.py" --script post_linkedin --posted "$POSTED" --skipped 0 --failed "$FAILED" --cost "$_COST" --elapsed "$ELAPSED" || true

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true
