#!/bin/bash
# Social Autoposter - Original Reddit thread poster (generalized)
#
# Picks one (project, subreddit) target via pick_thread_target.py,
# which enforces per-sub floor and banned-subreddit filtering, then spawns a
# Claude session with reddit-agent to research, draft, and post ONE original
# thread.
#
# Called by launchd every 6 hours. See com.m13v.social-reddit-threads.plist.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
CONFIG_FILE="$REPO_DIR/config.json"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-reddit-threads-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Reddit Threads Run: $(date) ===" | tee "$LOG_FILE"

# Serialize with other reddit-agent consumers
source "$REPO_DIR/skill/lock.sh"
acquire_lock "reddit-threads" 600

# Load engagement styles
source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block reddit posting)

# Pick target
TARGET_JSON=$(/usr/bin/python3 "$REPO_DIR/scripts/pick_thread_target.py" --json 2>&1) || {
  echo "NO_ELIGIBLE_TARGET: every eligible subreddit is inside its floor window. Stopping." | tee -a "$LOG_FILE"
  exit 0
}

PROJECT=$(echo "$TARGET_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin)['project']['name'])")
SUBREDDIT=$(echo "$TARGET_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin)['subreddit'])")
IS_OWN=$(echo "$TARGET_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin)['is_own_community'])")

echo "Target: project=$PROJECT subreddit=$SUBREDDIT own_community=$IS_OWN" | tee -a "$LOG_FILE"
SUB_SLUG=$(echo "$SUBREDDIT" | sed 's|^r/||I')

# Posting account (hardcoded for now; the only configured reddit account)
POST_ACCOUNT=$(/usr/bin/python3 -c "
import json
c = json.load(open('$CONFIG_FILE'))
print(c.get('accounts',{}).get('reddit',{}).get('username','Deep_Ad1959'))
")

# Build full per-project context block (JSON-driven so prompt stays compact)
export PROJECT_ENV="$PROJECT"
CONTEXT_BLOCK=$(/usr/bin/python3 <<'PYEOF'
import json, datetime, os
CONFIG = '/Users/matthewdi/social-autoposter/config.json'
name = os.environ['PROJECT_ENV']
c = json.load(open(CONFIG))
proj = next((p for p in c['projects'] if p['name'] == name), None)
if not proj:
    print("(project not found)")
    raise SystemExit(0)

t = proj.get('threads') or {}
lp = proj.get('landing_pages') or {}

out = []
out.append(f"Project: {proj['name']}")
out.append(f"Description: {proj.get('description','').strip()}")
if proj.get('website'): out.append(f"Website: {proj['website']}")
if lp.get('base_url'): out.append(f"Base URL: {lp['base_url']}")
if proj.get('content_angle'):
    out.append(f"\nContent angle: {proj['content_angle']}")

voice = proj.get('voice')
if voice:
    out.append(f"\nVoice tone: {voice.get('tone','')}")
    if voice.get('never'):
        out.append("Voice never: " + "; ".join(voice['never']))

# Dynamic day counter
dc = t.get('dynamic_context') or {}
day = dc.get('day_counter')
if day:
    base = day['base_count']
    ref = datetime.date.fromisoformat(day['ref_date'])
    days = (datetime.date.today() - ref).days
    count = base + days
    label = day.get('label','day count')
    out.append(f"\nLive {label}: {count}+")
for f in dc.get('static_facts') or []:
    out.append(f"- {f}")

# Topic angles
angles = t.get('topic_angles') or []
if angles:
    out.append("\nTopic angles to choose from:")
    for a in angles:
        out.append(f"- {a}")

# Source paths (SEO pipeline pattern)
out.append("\n## Product source (READ for context before drafting)")
repo = lp.get('repo','')
if repo:
    rp = os.path.expanduser(repo)
    status = "" if os.path.isdir(rp) else " [MISSING ON DISK]"
    out.append(f"- Website repo: {rp}{status}")
for s in lp.get('product_source') or []:
    p = os.path.expanduser(s.get('path',''))
    status = "" if os.path.isdir(p) else " [MISSING]"
    desc = s.get('description','').strip()
    out.append(f"- {p}{status}\n  {desc}")

# Threads content_sources
cs = t.get('content_sources') or {}
if cs.get('guide_dir'):
    gd = os.path.expanduser(cs['guide_dir'])
    out.append(f"\nGuide dir (read page.tsx files here for specific detail): {gd}")
if cs.get('link_base'):
    out.append(f"Link base for any URL you include: {cs['link_base']}")
if cs.get('read_instructions'):
    out.append(cs['read_instructions'])

print("\n".join(out))
PYEOF
)

echo "--- Context block ---" | tee -a "$LOG_FILE"
echo "$CONTEXT_BLOCK" | tee -a "$LOG_FILE"
echo "---------------------" | tee -a "$LOG_FILE"

# Recent posts in THIS sub (avoid repeats - include endings for closer variation)
RECENT_POSTS_SUB=$(psql "$DATABASE_URL" -t -A -c "
  SELECT thread_title || ' |ENDING| ' || RIGHT(our_content, 200) FROM posts
  WHERE thread_url ILIKE '%/r/${SUB_SLUG}/%' AND thread_url = our_url
  ORDER BY posted_at DESC LIMIT 10
" 2>/dev/null || echo "(psql error)")

# Recent posts project-wide (cross-sub dedup - include endings)
RECENT_POSTS_PROJECT=$(psql "$DATABASE_URL" -t -A -c "
  SELECT thread_title || ' |ENDING| ' || RIGHT(our_content, 200) FROM posts
  WHERE project_name='${PROJECT}' AND thread_url = our_url
    AND posted_at > NOW() - INTERVAL '14 days'
  ORDER BY posted_at DESC LIMIT 15
" 2>/dev/null || echo "(psql error)")

# Recent engagement styles for this project (avoid repeating)
RECENT_STYLES=$(psql "$DATABASE_URL" -t -A -c "
  SELECT engagement_style FROM posts
  WHERE project_name='${PROJECT}' AND thread_url = our_url
    AND engagement_style IS NOT NULL AND engagement_style != ''
  ORDER BY posted_at DESC LIMIT 5
" 2>/dev/null || echo "(psql error)")

# Top performers (tone calibration)
TOP_POSTS=$(psql "$DATABASE_URL" -t -A -c "
  SELECT thread_title, upvotes, comments_count, views FROM posts
  WHERE project_name='${PROJECT}' AND thread_url=our_url AND status='active'
    AND (COALESCE(upvotes,0) + COALESCE(comments_count,0)*3) > 5
  ORDER BY (COALESCE(upvotes,0) + COALESCE(comments_count,0)*3) DESC LIMIT 10
" 2>/dev/null || echo "(psql error)")

if [ "$IS_OWN" = "True" ]; then
  CADENCE_NOTE="This is our OWNED subreddit. Daily cadence (1-day floor). Be yourself, no product pitches."
else
  CADENCE_NOTE="This is an EXTERNAL subreddit (3-day floor). The thread must pass the sub's self-promo bar. No product links unless genuinely relevant (max 1)."
fi

# JSON schema: forces the model to return structured output with all required fields.
# This is how we enforce step compliance programmatically.
RESULT_SCHEMA='{"type":"object","properties":{"research_files_read":{"type":"array","items":{"type":"string"},"description":"Absolute paths of source files actually read during research step"},"subreddit_browsed":{"type":"boolean","description":"Whether you navigated to the subreddit hot page and read threads"},"hot_threads_seen":{"type":"array","items":{"type":"string"},"description":"Titles of 3-5 hot threads you read on the subreddit"},"topic_angle":{"type":"string","description":"The topic angle chosen from the list"},"engagement_style":{"type":"string","description":"The engagement style chosen"},"title":{"type":"string","description":"The exact post title submitted"},"body":{"type":"string","description":"The exact post body submitted"},"permalink":{"type":["string","null"],"description":"The Reddit permalink after successful submission, or null if aborted"},"rules_checked":{"type":"boolean","description":"Whether you checked subreddit rules"},"flair_applied":{"type":["string","null"],"description":"Flair text applied, or null if none"},"abort_reason":{"type":["string","null"],"description":"Reason for aborting, or null if posted successfully"},"source_summary":{"type":"string","description":"Rich source summary: (a) topic angle and why, (b) source files read, (c) specific details used"}},"required":["research_files_read","subreddit_browsed","hot_threads_seen","topic_angle","engagement_style","title","body","permalink","rules_checked","flair_applied","abort_reason","source_summary"]}'

CLAUDE_OUTPUT=$(claude -p --output-format json --json-schema "$RESULT_SCHEMA" "You are posting an ORIGINAL thread to ${SUBREDDIT} for the ${PROJECT} project as u/${POST_ACCOUNT}.

## Config & Rules
Read $SKILL_FILE for content rules and anti-AI-detection checklist.
You may also open $CONFIG_FILE for the full project block if you need anything not summarized below.

## Target
Project: ${PROJECT}
Subreddit: ${SUBREDDIT}
Own community: ${IS_OWN}
${CADENCE_NOTE}

## Project context (live-assembled)
${CONTEXT_BLOCK}

${STYLES_BLOCK}

## Recent posts by us in ${SUBREDDIT} (DO NOT repeat topics OR closers)
Each entry shows: title |ENDING| last 200 chars of post body. Study the endings to vary your closer.
${RECENT_POSTS_SUB}

## Recent posts by us for ${PROJECT} across all subs (last 14d, don't recycle angle OR closing style)
${RECENT_POSTS_PROJECT}

## Recent engagement styles for ${PROJECT} (avoid repeating the same style back-to-back)
${RECENT_STYLES}

## Top performing ${PROJECT} posts (match tone/style)
${TOP_POSTS}

## Workflow

1. RESEARCH (required): Read the product source paths listed in the context block. Specifically:
   - README.md at the repo root
   - Any files under src/ or docs/ that relate to your chosen topic angle
   - For Vipassana: read relevant page.tsx under the guide dir
   Pull 1-2 concrete, specific details from the source code or docs to anchor the post. Generic posts get ignored.

2. BROWSE THE SUBREDDIT: Navigate to https://old.reddit.com/${SUBREDDIT}/hot using mcp__reddit-agent__browser_navigate.
   - Read 3-5 recent thread titles and their top comments to absorb community tone, vocabulary, and what topics are getting engagement right now.
   - Note any recurring themes or hot-button issues the community cares about today.
   - Close the tab.
   This shapes your post to sound like it belongs in the current conversation, not like a scheduled drop.

3. Pick a topic from the threads.topic_angles list (in the context block above) that:
   - Has NOT been posted recently in this subreddit (see above)
   - Is not a recycled angle from other subs (see project-wide list)
   - Fits this subreddit's community and rules
   - Invites genuine discussion (end with a question or open thread)
   - Pick an engagement_style from the styles list above that:
     (a) fits the topic and subreddit culture
     (b) is NOT one of the last 3 styles used for this project (see recent styles above)

4. Draft the post. RULES:
   - No em dashes anywhere. Commas, periods, or plain '-' only.
   - No markdown formatting (no ##, no **bold**, no bullet lists).
   - 2-4 short paragraphs, casual tone, first person.
   - Include at least one imperfection (sentence fragment, aside, lowercase start).
   - Title: lowercase, no clickbait patterns, no emojis.
   - Ground in a specific detail from the product source you read in step 1.
   - Follow the voice guidance from the project context. Read it out loud; if it sounds like a blog post, rewrite.
   - VARY YOUR CLOSERS: check how recent posts ended (shown after |ENDING| above). Use a DIFFERENT ending pattern. Banned closers: 'curious if anyone', 'anyone else', 'thoughts?', 'has anyone'. Sometimes end with a statement, sometimes mid-thought, sometimes a specific (not generic) question.
   - VARY CAPITALIZATION: do NOT lowercase every sentence start. Mix it naturally: some sentences capitalized, some not. Uniform all-lowercase is a known AI tell.

5. SUBREDDIT RULES CHECK via mcp__reddit-agent__browser_navigate to https://old.reddit.com/${SUBREDDIT}/about/rules
   - If strict no-self-promo and our post would read promotional, ABORT. Set abort_reason and permalink=null.
   - Note whether flair is required.
   - Close the tab.

6. POST via mcp__reddit-agent__*:
   - Navigate to https://old.reddit.com/${SUBREDDIT}/submit?selftext=true
   - Fill title and body. If Playwright locator hits the md-container wrapper div, fall back to:
     browser_evaluate with:
       document.querySelector('textarea[name=\"title\"]').value = TITLE;
       document.querySelector('textarea[name=\"title\"]').dispatchEvent(new Event('input',{bubbles:true}));
       document.querySelector('textarea[name=\"text\"]').value = BODY;
       document.querySelector('textarea[name=\"text\"]').dispatchEvent(new Event('input',{bubbles:true}));
   - FLAIR HELPER (if flair required): click '.flairselector-button' OR the 'add flair' button, then in the flair dialog click the appropriate .flairoption matching the post type, then click the 'Save' button. If no suitable flair, ABORT.
   - Click the submit button. Wait 3 seconds. Capture the permalink (document.location.href after submission).
   - Close the tab.

7. LOG to database. IMPORTANT: The source_summary in your JSON output IS what gets logged. Make it rich:
   INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
     thread_title, thread_content, our_url, our_content, our_account,
     source_summary, project_name, engagement_style, feedback_report_used, status, posted_at)
   VALUES ('reddit', PERMALINK, '${POST_ACCOUNT}', '${POST_ACCOUNT}',
     TITLE, BODY, PERMALINK, BODY, '${POST_ACCOUNT}',
     SOURCE_SUMMARY, '${PROJECT}', 'STYLE_YOU_CHOSE', TRUE, 'active', NOW());

8. Return your structured JSON output. Every field in the schema is required. Fill permalink with the actual URL if posted, or null if aborted.

CRITICAL: NEVER use em dashes.
CRITICAL: Use ONLY mcp__reddit-agent__* tools.
CRITICAL: Close browser tabs after each navigation (browser_tabs action 'close').
CRITICAL: If a browser call times out, wait 30s and retry up to 3 times." 2>&1)

# Parse structured output and log results
echo "$CLAUDE_OUTPUT" | tee -a "$LOG_FILE"

# Extract fields from JSON output for the summary line
PERMALINK=$(/usr/bin/python3 -c "
import json,sys
try:
    d = json.loads(sys.stdin.read())
    r = d.get('result', d)  # handle wrapped or unwrapped
    print(r.get('permalink') or 'null')
except: print('PARSE_ERROR')
" <<< "$CLAUDE_OUTPUT" 2>/dev/null)

TITLE=$(/usr/bin/python3 -c "
import json,sys
try:
    d = json.loads(sys.stdin.read())
    r = d.get('result', d)
    print(r.get('title',''))
except: print('PARSE_ERROR')
" <<< "$CLAUDE_OUTPUT" 2>/dev/null)

ABORT_REASON=$(/usr/bin/python3 -c "
import json,sys
try:
    d = json.loads(sys.stdin.read())
    r = d.get('result', d)
    print(r.get('abort_reason') or '')
except: print('PARSE_ERROR')
" <<< "$CLAUDE_OUTPUT" 2>/dev/null)

# Log step compliance summary
/usr/bin/python3 -c "
import json,sys
try:
    d = json.loads(sys.stdin.read())
    r = d.get('result', d)
    files = r.get('research_files_read', [])
    browsed = r.get('subreddit_browsed', False)
    hot = r.get('hot_threads_seen', [])
    rules = r.get('rules_checked', False)
    style = r.get('engagement_style', '?')
    print(f'Step compliance: research={len(files)} files, browsed={browsed}, hot_threads={len(hot)}, rules_checked={rules}, style={style}')
except Exception as e:
    print(f'Step compliance: PARSE ERROR ({e})')
" <<< "$CLAUDE_OUTPUT" 2>/dev/null | tee -a "$LOG_FILE"

if [ "$PERMALINK" != "null" ] && [ "$PERMALINK" != "PARSE_ERROR" ]; then
  echo "POSTED: $PERMALINK | $TITLE" | tee -a "$LOG_FILE"
elif [ -n "$ABORT_REASON" ] && [ "$ABORT_REASON" != "PARSE_ERROR" ]; then
  echo "ABORTED: $ABORT_REASON" | tee -a "$LOG_FILE"
else
  echo "UNKNOWN OUTCOME (check JSON output above)" | tee -a "$LOG_FILE"
fi

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-reddit-threads-*.log" -mtime +14 -delete 2>/dev/null || true
