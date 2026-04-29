#!/bin/bash
# Social Autoposter - LinkedIn posting (two-phase)
#
# Phase A (discovery, ~$10-15 target): pick a project, browse 1-2 LinkedIn
#   search URLs, pick a candidate post, extract URNs, run --check-self-author
#   + --check-engaged-ids, write JSON to a tmp file, STOP. No drafting, no
#   posting, no engagement style block in context.
#
# Phase B (compose+post, ~$10-15 target): given Phase A's JSON, navigate
#   straight to the chosen URL, defensively re-check engaged-ids, draft using
#   the single chosen project's voice block + engagement styles + top
#   performers report, post via mcp__linkedin-agent, verify (network + DOM),
#   log via log_post.py, STOP.
#
# Phasing cuts cache_read by ~50%: each phase carries far less context per
# turn (Phase A skips voice/styles/top-performers; Phase B skips the search
# loop and gets the chosen project's full config alone instead of all 20+).
# Pattern matches engage-linkedin.sh's existing Phase A / Phase B split.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-linkedin-$(date +%Y-%m-%d_%H%M%S).log"
RUN_START_EPOCH=$(date +%s)

echo "=== LinkedIn Post Run: $(date) ===" | tee "$LOG_FILE"

# Hold the linkedin-browser lock for the entire run. Phase A's claude exits
# (closing its Chrome) before Phase B's claude starts, so the profile dir is
# free for Phase B's fresh MCP session — but we must NOT release the shell
# lock between phases or a sibling pipeline would steal the browser.
source "$REPO_DIR/skill/lock.sh"
acquire_lock "linkedin-browser" 3600

# ===== Phase A: discovery (slim context) =====
PROJECT_DIST=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform linkedin --distribution 2>/dev/null || echo "(distribution unavailable)")

# Slim project list: name + description + qualification only. Drops voice,
# features, links, search topics, etc. that Phase A doesn't need. Cuts the
# inlined JSON from ~177KB to ~25KB per turn.
PROJECTS_SLIM_JSON=$(python3 -c "
import json, os
config = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
slim = {p['name']: {k: p[k] for k in ('name','description','qualification') if k in p} for p in config.get('projects', [])}
print(json.dumps(slim, indent=2))
" 2>/dev/null || echo "{}")

# BSD mktemp on macOS only substitutes XXXXXX at the end of the template
# (extensions break the substitution). Keep XXXXXX terminal so the actual
# path is unique — otherwise the literal X's leak into the prompt and the
# LLM "helpfully" substitutes them itself, writing to a path the wrapper
# never sees (lost a full Phase A run on 2026-04-28 to this).
PHASE_A_OUT=$(mktemp /tmp/sa-run-linkedin-phaseA-XXXXXX)
PHASE_A_PROMPT=$(mktemp /tmp/sa-run-linkedin-phaseA-prompt-XXXXXX)

cat > "$PHASE_A_PROMPT" <<PROMPT_EOF
You are the Social Autoposter LinkedIn discovery scout (Phase A).

Your only job: find ONE good LinkedIn post we should comment on, write its
details to $PHASE_A_OUT as JSON, and STOP. Do NOT draft a comment. Do NOT
post anything. Phase B handles drafting and posting.

## Project candidates (slim list: name + description + qualification)
$PROJECTS_SLIM_JSON

## Today's distribution (prefer underrepresented projects)
$PROJECT_DIST

## Workflow

1. Pick ONE underrepresented project from the distribution that has a
   plausible content fit. Do NOT iterate through many projects.

2. Run:
     python3 $REPO_DIR/scripts/find_threads.py --include-linkedin --project 'PROJECT_NAME'
   The output gives you the LinkedIn search URL for that project.

3. Browse that search URL with mcp__linkedin-agent__browser_navigate. If the
   first project's results are weak, you MAY try ONE alternative project.
   Hard cap: at most 2 search URL navigations across this whole phase.

4. From the rendered DOM, identify the BEST candidate post. Skip:
   - Posts authored by Matthew Diakonov / linkedin.com/in/m13v/
   - Posts we already engaged on (DOM walk for ALL URNs)
   - Vendor/spam/recycled content

5. Extract every URN. LinkedIn search results often hydrate post URNs via
   React state / network responses, NOT static DOM attrs — so use BOTH paths
   and merge. (Phase B uses the same dual-walk; Phase A used to be DOM-only
   and was failing ~80% of the time on static search pages.)

   5a. DOM walk via mcp__linkedin-agent__browser_run_code (attributes AND
       textContent — some URNs land in inline JSON inside <code> blocks):
\`\`\`javascript
async (page) => {
  await page.waitForTimeout(3000);
  return await page.evaluate(() => {
    const all = new Set();
    let activity = null;
    const re = /(activity|share|ugcPost)[:_-](\d{16,19})/gi;
    const seen = (m) => {
      while ((m = re.exec(m.input)) !== null) {
        all.add(m[2]);
        if (m[1].toLowerCase() === 'activity' && !activity) activity = m[2];
      }
    };
    // attribute walk
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    let node, scanned = 0;
    while ((node = walker.nextNode()) && scanned++ < 8000) {
      for (const a of node.attributes || []) {
        const v = a.value || '';
        let m; re.lastIndex = 0;
        while ((m = re.exec(v))) {
          all.add(m[2]);
          if (m[1].toLowerCase() === 'activity' && !activity) activity = m[2];
        }
      }
    }
    // inline JSON / <code> blocks (LinkedIn sometimes ships URNs here)
    document.querySelectorAll('code, script[type="application/json"]').forEach(el => {
      const t = el.textContent || ''; let m; re.lastIndex = 0;
      while ((m = re.exec(t))) {
        all.add(m[2]);
        if (m[1].toLowerCase() === 'activity' && !activity) activity = m[2];
      }
    });
    return { allIds: Array.from(all), activityId: activity };
  });
}
\`\`\`
   5b. Network-response walk via mcp__linkedin-agent__browser_network_requests:
         filter: 'voyager|graphql|search|feed-update|updates'
         requestBody: false
         responseBody: true
         static: false
       Walk every response body for /(activity|share|ugcPost)[:_-](\d{16,19})/g
       matches. Merge with 5a's allIds.
   5c. If after both walks you have no activityId, pick a different candidate
       OR exit cleanly with '## No good post found'. Do NOT fabricate URNs.

6. Run engaged-id check:
     python3 $REPO_DIR/scripts/linkedin_url.py --check-engaged-ids 'ID1,ID2,...'
   Exit code 0 = already engaged, pick another candidate. Exit 1 = clean.

7. Capture the post's author profile URL via DOM walk on the post header
   (\`a[href*="/in/"]\` inside the post element).

8. Run self-author check:
     python3 $REPO_DIR/scripts/linkedin_url.py --check-self-author 'AUTHOR_URL'
   Exit code 0 = self-authored, SKIP. Exit 1 = not self.

9. Once you have a clean candidate, write JSON to $PHASE_A_OUT and STOP:
\`\`\`bash
cat > $PHASE_A_OUT <<JSON_EOF
{
  "project": "PROJECT_NAME",
  "thread_url": "https://www.linkedin.com/feed/update/urn:li:activity:ACTIVITY_ID/",
  "activity_id": "ACTIVITY_ID",
  "all_urns": ["ID1","ID2"],
  "author_name": "First Last",
  "author_profile_url": "https://www.linkedin.com/in/SLUG/",
  "post_excerpt": "first 250 chars of post text, no newlines, no double quotes",
  "post_title_hint": "short label for the post",
  "language": "en"
}
JSON_EOF
\`\`\`
   Then say '## Phase A: candidate ready' and STOP.

If no good candidate is found within the 2-search-URL budget, do NOT write
the file, say '## No good post found', and STOP. Phase B will skip cleanly.

CRITICAL: Use ONLY mcp__linkedin-agent__* tools. NEVER click the comment
textbox. NEVER call createComment. NEVER navigate to a post-compose flow.
Phase B does all of that.
CRITICAL: Hard cap of 2 search-URL navigations. The whole point of phasing
is to converge fast. If you can't find a fit in 2 searches, exit cleanly so
the next launchd cycle gets a fresh shot.
CRITICAL: post_excerpt must be safe to embed in a bash double-quoted string.
Strip backticks, double quotes, and newlines before writing it.
PROMPT_EOF

set +e
"$REPO_DIR/scripts/run_claude.sh" "run-linkedin-phaseA" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json" -p "$(cat "$PHASE_A_PROMPT")" 2>&1 | tee -a "$LOG_FILE"
PA_RC=${PIPESTATUS[0]}
set -e
rm -f "$PHASE_A_PROMPT"

# ===== Validate Phase A output =====
if [ "$PA_RC" -ne 0 ] || [ ! -s "$PHASE_A_OUT" ]; then
  echo "Phase A: no candidate (rc=$PA_RC, $([ -s "$PHASE_A_OUT" ] && echo 'file non-empty' || echo 'file empty')). Skipping Phase B." | tee -a "$LOG_FILE"
  rm -f "$PHASE_A_OUT"
  ELAPSED=$(( $(date +%s) - RUN_START_EPOCH ))
  _COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START_EPOCH" --scripts "run-linkedin-phaseA" "run-linkedin-phaseB" 2>/dev/null || echo "0.0000")
python3 "$REPO_DIR/scripts/log_run.py" --script post_linkedin --posted 0 --skipped 1 --failed 0 --cost "$_COST" --elapsed "$ELAPSED" || true
  echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
  find "$LOG_DIR" -name "run-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true
  exit 0
fi

PA_PROJECT=$(python3 -c "import json; print(json.load(open('$PHASE_A_OUT')).get('project',''))" 2>/dev/null || echo "")
PA_URL=$(python3 -c "import json; print(json.load(open('$PHASE_A_OUT')).get('thread_url',''))" 2>/dev/null || echo "")
PA_ACTIVITY_ID=$(python3 -c "import json; print(json.load(open('$PHASE_A_OUT')).get('activity_id',''))" 2>/dev/null || echo "")
PA_ALL_URNS=$(python3 -c "import json; print(','.join(json.load(open('$PHASE_A_OUT')).get('all_urns',[])))" 2>/dev/null || echo "")
PA_AUTHOR_NAME=$(python3 -c "import json; print(json.load(open('$PHASE_A_OUT')).get('author_name',''))" 2>/dev/null || echo "")
PA_AUTHOR_URL=$(python3 -c "import json; print(json.load(open('$PHASE_A_OUT')).get('author_profile_url',''))" 2>/dev/null || echo "")
PA_EXCERPT=$(python3 -c "import json; print(json.load(open('$PHASE_A_OUT')).get('post_excerpt',''))" 2>/dev/null || echo "")
PA_TITLE_HINT=$(python3 -c "import json; print(json.load(open('$PHASE_A_OUT')).get('post_title_hint',''))" 2>/dev/null || echo "")
PA_LANG=$(python3 -c "import json; print(json.load(open('$PHASE_A_OUT')).get('language','en'))" 2>/dev/null || echo "en")

# Required fields: project + activity_id (numeric URN). thread_url is now
# rebuilt from activity_id below, so we don't trust whatever shape the model
# wrote (was rejecting valid ugcPost candidates on 2026-04-29).
if [ -z "$PA_PROJECT" ] || [ -z "$PA_ACTIVITY_ID" ]; then
  echo "Phase A output missing required fields (project='$PA_PROJECT' activity_id='$PA_ACTIVITY_ID'). Skipping." | tee -a "$LOG_FILE"
  rm -f "$PHASE_A_OUT"
  ELAPSED=$(( $(date +%s) - RUN_START_EPOCH ))
  _COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START_EPOCH" --scripts "run-linkedin-phaseA" "run-linkedin-phaseB" 2>/dev/null || echo "0.0000")
python3 "$REPO_DIR/scripts/log_run.py" --script post_linkedin --posted 0 --skipped 0 --failed 1 --cost "$_COST" --elapsed "$ELAPSED" || true
  echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
  exit 0
fi

# activity_id must be a 16-19 digit numeric URN. Anything else is malformed.
case "$PA_ACTIVITY_ID" in
  ''|*[!0-9]*)
    echo "Phase A returned non-numeric activity_id '$PA_ACTIVITY_ID'. Skipping Phase B." | tee -a "$LOG_FILE"
    rm -f "$PHASE_A_OUT"
    ELAPSED=$(( $(date +%s) - RUN_START_EPOCH ))
    _COST=$(python3 "$REPO_DIR/scripts/get_run_cost.py" --since "$RUN_START_EPOCH" --scripts "run-linkedin-phaseA" "run-linkedin-phaseB" 2>/dev/null || echo "0.0000")
python3 "$REPO_DIR/scripts/log_run.py" --script post_linkedin --posted 0 --skipped 0 --failed 1 --cost "$_COST" --elapsed "$ELAPSED" || true
    echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
    exit 0
    ;;
esac

# Rebuild canonical thread_url from activity_id. LinkedIn redirects
# ugcPost-form URNs to the activity feed view, so this works for both. Was a
# bug 2026-04-29: model wrote ugcPost-form thread_url and the strict regex
# validator rejected the whole candidate.
PA_URL="https://www.linkedin.com/feed/update/urn:li:activity:${PA_ACTIVITY_ID}/"

echo "Phase A: candidate ready — project=$PA_PROJECT activity=$PA_ACTIVITY_ID" | tee -a "$LOG_FILE"

# Look up the chosen project's full config (only this one, not all 20+ projects)
PROJECT_FULL=$(python3 -c "
import json, os
c = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
p = next((p for p in c.get('projects',[]) if p['name']=='$PA_PROJECT'), {})
print(json.dumps(p, indent=2))
")

# Phase B inputs (only Phase B needs styles + top performers)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform linkedin 2>/dev/null || echo "(top performers report unavailable)")
source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block linkedin posting)

# Allow Chrome's profile lockfile to release between phases.
# Phase A's claude exits, its playwright-mcp wrapper exits, its Chrome dies;
# the profile dir's SingletonLock takes a beat to clear before Phase B's
# fresh Chrome can claim it.
sleep 3

# ===== Phase B: compose + post + verify + log =====
PHASE_B_PROMPT=$(mktemp /tmp/sa-run-linkedin-phaseB-prompt-XXXXXX)
cat > "$PHASE_B_PROMPT" <<PROMPT_EOF
You are the Social Autoposter (Phase B). Your job: post ONE comment on a
pre-selected LinkedIn post, verify it landed, log it. STOP. Do NOT search
for other candidates — Phase A already picked one.

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

## Project config (only the chosen project's full block)
$PROJECT_FULL

## Top performers feedback (use to pick a comment angle)
$TOP_REPORT

$STYLES_BLOCK

## Workflow

1. Navigate to $PA_URL via mcp__linkedin-agent__browser_navigate.

2. Defensive engaged-id re-check (Phase A may have missed a URN that only
   surfaces after the post page fully loads). Walk the rendered DOM for ALL
   URNs (activity, share, ugcPost forms — same JS as Phase A), merge with
   '$PA_ALL_URNS', and run:
     python3 $REPO_DIR/scripts/linkedin_url.py --check-engaged-ids 'MERGED_URNS'
   If exit code 0 (already engaged), STOP with '## Already engaged
   (defensive catch in Phase B)' and do NOT log.

3. Pick the engagement style that best fits the post + project's voice
   block above (apply voice.tone, never violate voice.never, mirror
   voice.examples / voice.examples_good if present). Reply in $PA_LANG.
   NEVER use em dashes.

4. Post the comment via mcp__linkedin-agent (find textbox, click, type,
   submit).

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

6. If REJECTED, do NOT call the success log path. Instead:
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

7. If SUCCESS, log to DB:
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

CRITICAL: ONE post only. If anything fails, STOP — do NOT pick another
candidate (Phase A's job, not Phase B's).
CRITICAL: Use ONLY mcp__linkedin-agent__* tools.
CRITICAL: NEVER use em dashes.
PROMPT_EOF

set +e
"$REPO_DIR/scripts/run_claude.sh" "run-linkedin-phaseB" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json" -p "$(cat "$PHASE_B_PROMPT")" 2>&1 | tee -a "$LOG_FILE"
PB_RC=${PIPESTATUS[0]}
set -e
rm -f "$PHASE_B_PROMPT"
rm -f "$PHASE_A_OUT"

# ===== Persist run-level summary (one row per script invocation) =====
# Counts posts inserted during this run via NOW() arithmetic so the dashboard
# 'Post · LinkedIn' row keeps showing the same shape regardless of phasing.
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
