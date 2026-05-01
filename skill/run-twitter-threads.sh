#!/bin/bash
# Social Autoposter - Original Twitter thread poster
#
# Picks one (project, topic_angle) target via pick_twitter_thread_target.py,
# which enforces:
#   1. Hard global cap of 3 original threads per UTC calendar day.
#   2. Per-(project, topic_angle) floor window (default 2 days).
#   3. Per-project inverse-share weighting (don't pile on one project).
#
# Then spawns a Claude session with twitter-agent to research, draft, and post
# ONE original thread (1-6 tweets, chained as a Twitter thread).
#
# Called by launchd. See com.m13v.social-twitter-threads.plist.
# Mirror of skill/run-reddit-threads.sh; deviations are commented.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
CONFIG_FILE="$REPO_DIR/config.json"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-twitter-threads-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Twitter Threads Run: $(date) ===" | tee "$LOG_FILE"

# Diagnostic trap (parallel to reddit version): log line + cmd before set -e exits.
trap 'rc=$?; echo "SCRIPT DIED line=$LINENO cmd=\"$BASH_COMMAND\" exit=$rc" | tee -a "$LOG_FILE" >&2' ERR

# Pipeline lock at top. Browser lock acquired later, just before the Claude/MCP step.
source "$REPO_DIR/skill/lock.sh"
acquire_lock "twitter-threads" 600

# Engagement styles
source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block twitter posting)

# Pick target. The picker enforces the daily cap; exit 3 = cap reached, exit 2 = no eligible angle.
set +e
TARGET_JSON=$(/usr/bin/python3 "$REPO_DIR/scripts/pick_twitter_thread_target.py" --json 2>&1)
PICK_RC=$?
set -e
if [ "$PICK_RC" -eq 3 ]; then
  echo "DAILY_CAP_REACHED: skipping this fire (3 threads per UTC day)." | tee -a "$LOG_FILE"
  exit 0
fi
if [ "$PICK_RC" -eq 2 ]; then
  echo "NO_ELIGIBLE_TARGET: every (project,angle) is inside its floor window. Stopping." | tee -a "$LOG_FILE"
  exit 0
fi
if [ "$PICK_RC" -ne 0 ]; then
  echo "PICKER_FAILED rc=$PICK_RC output=$TARGET_JSON" | tee -a "$LOG_FILE"
  exit 0
fi

PROJECT=$(echo "$TARGET_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin)['project']['name'])")
TOPIC_ANGLE=$(echo "$TARGET_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin)['topic_angle'])")
DAILY_COUNT=$(echo "$TARGET_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin)['daily_count_today'])")
DAILY_CAP=$(echo "$TARGET_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin)['daily_cap'])")

echo "Target: project=$PROJECT" | tee -a "$LOG_FILE"
echo "Angle:  $TOPIC_ANGLE"      | tee -a "$LOG_FILE"
echo "Daily:  $DAILY_COUNT/$DAILY_CAP posts today (UTC)" | tee -a "$LOG_FILE"

# Posting account
POST_ACCOUNT=$(/usr/bin/python3 -c "
import json
c = json.load(open('$CONFIG_FILE'))
print((c.get('accounts',{}).get('twitter',{}).get('handle','@m13v_')).lstrip('@'))
")

# Per-project context block (same JSON-driven shape as reddit version).
# Reads twitter_threads first, falls back to threads if a key is absent there.
export PROJECT_ENV="$PROJECT"
export CONFIG_PATH="$CONFIG_FILE"
CONTEXT_BLOCK=$(/usr/bin/python3 <<'PYEOF'
import json, datetime, os
CONFIG = os.environ['CONFIG_PATH']
name = os.environ['PROJECT_ENV']
c = json.load(open(CONFIG))
proj = next((p for p in c['projects'] if p['name'] == name), None)
if not proj:
    print("(project not found)")
    raise SystemExit(0)

tt = proj.get('twitter_threads') or {}
t  = proj.get('threads') or {}            # fallback for content_sources/dynamic_context
lp = proj.get('landing_pages') or {}

def first(*keys):
    """Return the first non-empty value across (tt, t) for any of the given keys."""
    for src in (tt, t):
        for k in keys:
            v = src.get(k)
            if v:
                return v
    return None

out = []
out.append(f"Project: {proj['name']}")
out.append(f"Description: {proj.get('description','').strip()}")
if proj.get('website'): out.append(f"Website: {proj['website']}")
if lp.get('base_url'):  out.append(f"Base URL: {lp['base_url']}")
if proj.get('content_angle'):
    out.append(f"\nContent angle: {proj['content_angle']}")

voice = proj.get('voice')
if voice:
    out.append(f"\nVoice tone: {voice.get('tone','')}")
    if voice.get('never'):
        out.append("Voice never: " + "; ".join(voice['never']))

# Dynamic day counter
dc = first('dynamic_context') or {}
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

# Source paths
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

# content_sources
cs = first('content_sources') or {}
if cs.get('guide_dir'):
    gd = os.path.expanduser(cs['guide_dir'])
    out.append(f"\nGuide dir (read page.tsx files here for specific detail): {gd}")
if cs.get('link_base'):
    out.append(f"Link base for any URL you include: {cs['link_base']}")
if cs.get('readme_url'):
    out.append(f"README url: {cs['readme_url']}")
if cs.get('read_instructions'):
    out.append(cs['read_instructions'])

print("\n".join(out))
PYEOF
)

echo "--- Context block ---" | tee -a "$LOG_FILE"
echo "$CONTEXT_BLOCK"        | tee -a "$LOG_FILE"
echo "---------------------" | tee -a "$LOG_FILE"

# Recent originals by us in last 14 days for THIS project (avoid repeats; show endings to vary closer)
RECENT_POSTS=$(/opt/homebrew/opt/postgresql@14/bin/psql "$DATABASE_URL" -t -A -c "
  SELECT our_content::text FROM posts
  WHERE platform='twitter' AND thread_url = our_url
    AND project_name='${PROJECT}'
    AND posted_at > NOW() - INTERVAL '14 days'
    AND our_content NOT ILIKE '(mention%'
  ORDER BY posted_at DESC LIMIT 10
" 2>/dev/null || echo "(psql error)")

# Recent engagement styles for this project on Twitter
RECENT_STYLES=$(/opt/homebrew/opt/postgresql@14/bin/psql "$DATABASE_URL" -t -A -c "
  SELECT engagement_style FROM posts
  WHERE platform='twitter' AND project_name='${PROJECT}' AND thread_url = our_url
    AND engagement_style IS NOT NULL AND engagement_style != ''
    AND our_content NOT ILIKE '(mention%'
  ORDER BY posted_at DESC LIMIT 5
" 2>/dev/null || echo "(psql error)")

# Top performers (tone calibration). Twitter: reuse upvotes + comments_count + views as a loose engagement score.
TOP_POSTS=$(/opt/homebrew/opt/postgresql@14/bin/psql "$DATABASE_URL" -t -A -c "
  SELECT our_content::text, upvotes, comments_count, views FROM posts
  WHERE platform='twitter' AND project_name='${PROJECT}' AND thread_url=our_url AND status='active'
    AND our_content NOT ILIKE '(mention%'
    AND (COALESCE(upvotes,0) + COALESCE(comments_count,0)*3 + COALESCE(views,0)/100) > 5
  ORDER BY (COALESCE(upvotes,0) + COALESCE(comments_count,0)*3 + COALESCE(views,0)/100) DESC LIMIT 8
" 2>/dev/null || echo "(psql error)")

# Structured output schema. The model returns a "tweets" array (1-6 items)
# representing a single chained Twitter thread, plus the same compliance fields
# as the reddit version.
RESULT_SCHEMA='{"type":"object","properties":{"research_files_read":{"type":"array","items":{"type":"string"}},"topic_angle":{"type":"string"},"engagement_style":{"type":"string"},"tweets":{"type":"array","minItems":1,"maxItems":6,"items":{"type":"string","maxLength":280},"description":"1-6 chained tweets. First is the hook, each <=280 chars."},"permalink":{"type":["string","null"],"description":"URL of the FIRST tweet in the thread, or null if aborted"},"abort_reason":{"type":["string","null"]},"source_summary":{"type":"string","description":"Rich summary: (a) topic angle and why, (b) source files read, (c) specific details used"}},"required":["research_files_read","topic_angle","engagement_style","tweets","permalink","abort_reason","source_summary"]}'

# Pre-generate session id so the prompt's inline INSERT can stamp it.
export CLAUDE_SESSION_ID=$(uuidgen | tr 'A-Z' 'a-z')

# Acquire browser lock right before MCP step.
acquire_lock "twitter-browser" 3600
ensure_browser_healthy "twitter"

# Capture Claude output to a temp file so non-zero exit doesn't swallow stderr.
CLAUDE_TMP=$(mktemp)
set +e
"$REPO_DIR/scripts/run_claude.sh" "run-twitter-threads" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/twitter-agent-mcp.json" -p --output-format json --json-schema "$RESULT_SCHEMA" "You are posting an ORIGINAL Twitter thread for the ${PROJECT} project as @${POST_ACCOUNT}.

## Config & Rules
Read $SKILL_FILE for content rules and anti-AI-detection checklist.
You may also open $CONFIG_FILE for the full project block if you need anything not summarized below.

## Target
Project: ${PROJECT}
Topic angle (use this, do NOT pick a different one): ${TOPIC_ANGLE}

## Project context (live-assembled)
${CONTEXT_BLOCK}

${STYLES_BLOCK}

## Recent originals by us for ${PROJECT} (last 14d, DO NOT recycle phrasing or closer)
Each entry shows: first 120 chars |ENDING| last 80 chars. Vary your closer.
${RECENT_POSTS}

## Recent engagement styles for ${PROJECT} on Twitter (avoid repeating back-to-back)
${RECENT_STYLES}

## Top performing ${PROJECT} originals (match tone)
${TOP_POSTS}

## Workflow

1. RESEARCH (required): Read the product source paths listed in the context block. Pull 1-2 concrete, specific details from the source code or docs to anchor the thread. Generic threads get ignored.

2. SCAN THE TIMELINE: Navigate to https://x.com/home using mcp__twitter-agent__browser_navigate to get a quick read on what is being said in our space today.
   - Read 5-10 recent tweets from accounts in adjacent topics (other indie devs, AI tooling, macOS automation, whatever fits the project).
   - Note the current vocabulary, hot takes, and any thread that is getting unusually high engagement.
   - Close the tab.

3. DRAFT the thread.
   - 1 to 6 tweets. Each <= 280 characters (hard cap). The first tweet must work as a standalone hook (people may only see that one).
   - Use the assigned topic_angle (above). Pick an engagement_style from the styles list that fits and is NOT one of the last 3 used for this project.
   - No em dashes anywhere. Commas, periods, plain '-' only.
   - No hashtag spam. At most ONE hashtag total in the entire thread, only if it is genuinely the topical tag people search.
   - No emojis at the start of a tweet. At most one per tweet, and only if it adds meaning.
   - Lowercase first character on most tweets feels natural on X. Do not uniformly lowercase every sentence (that is an AI tell). Mix it.
   - At least one imperfection (sentence fragment, aside, run-on) somewhere in the thread.
   - Ground at least one claim in a specific detail from the source you read in step 1.
   - VARY YOUR CLOSER. Banned closers: 'curious if anyone', 'anyone else', 'thoughts?', 'has anyone'. Sometimes end with a statement, sometimes mid-thought, sometimes a specific question.
   - First tweet may include the link from the project content_sources.link_base if relevant. Do NOT include the link in tweets 2+ (X downranks link-heavy threads).

4. POST via mcp__twitter-agent__*:
   - Navigate to https://x.com/compose/post.
   - Fill the first tweet into the textarea selected by [data-testid='tweetTextarea_0']. If the contenteditable does not accept .value=, use mcp__twitter-agent__browser_type to type the text directly into the focused element.
   - For each subsequent tweet (if any): click the button with data-testid='addButton' (the small '+' that appends a new tweet to the chain), then fill its textarea. The new textarea will be data-testid='tweetTextarea_1', then 'tweetTextarea_2', etc.
   - When all tweets are filled, click the button with data-testid='tweetButton' (label varies between 'Post' and 'Post all'). Wait 4 seconds.
   - Capture the URL of the FIRST posted tweet:
       - Navigate to https://x.com/${POST_ACCOUNT} and read the most recent pinned-or-top tweet's permalink (browser_evaluate: document.querySelector('article a[href*=\"/status/\"]').href). Confirm its text matches the first tweet you posted.
   - Close the tab.

5. DO NOT touch the database. The shell wrapper handles the INSERT after you return.
   IMPORTANT: tweets, permalink, engagement_style, source_summary in your JSON output are what get logged. Make source_summary rich.

6. Return the structured JSON output. Every field is required. permalink = URL of the first tweet if posted, null if aborted. tweets array must contain the EXACT text of each tweet posted (no markdown, no additions).

CRITICAL: NEVER use em dashes. Use commas, plain hyphens, or separate sentences.
CRITICAL: Each tweet <=280 chars. The schema enforces this; do not exceed.
CRITICAL: Use ONLY mcp__twitter-agent__* tools.
CRITICAL: Close browser tabs after each navigation (browser_tabs action 'close').
CRITICAL: If a browser call times out, wait 30s and retry up to 3 times." > "$CLAUDE_TMP" 2>&1
CLAUDE_RC=$?
set -e
CLAUDE_OUTPUT=$(cat "$CLAUDE_TMP")
rm -f "$CLAUDE_TMP"

echo "$CLAUDE_OUTPUT" | tee -a "$LOG_FILE"
if [ "$CLAUDE_RC" -ne 0 ]; then
  echo "RUN_CLAUDE_NONZERO_EXIT rc=$CLAUDE_RC (output above is full stderr+stdout)" | tee -a "$LOG_FILE"
fi

# Extract structured_output. claude -p --output-format json wraps results.
PARSED=$(/usr/bin/python3 -c "
import json,sys
try:
    raw = sys.stdin.read()
    d, _ = json.JSONDecoder().raw_decode(raw)
    so = d.get('structured_output') or d
    print(json.dumps(so))
except Exception as e:
    print(json.dumps({'_parse_error': str(e)}))
" <<< "$CLAUDE_OUTPUT" 2>/dev/null)

PERMALINK=$(/usr/bin/python3 -c "import json,sys; r=json.loads(sys.stdin.read()); print(r.get('permalink') or 'null')" <<< "$PARSED" 2>/dev/null)
ABORT_REASON=$(/usr/bin/python3 -c "import json,sys; r=json.loads(sys.stdin.read()); print(r.get('abort_reason') or '')" <<< "$PARSED" 2>/dev/null)

# Step compliance summary
/usr/bin/python3 -c "
import json,sys
r = json.loads(sys.stdin.read())
if '_parse_error' in r:
    print(f'Step compliance: PARSE ERROR ({r[\"_parse_error\"]})')
else:
    files = r.get('research_files_read', [])
    tweets = r.get('tweets', [])
    style = r.get('engagement_style', '?')
    over = [i for i,t in enumerate(tweets) if len(t) > 280]
    print(f'Step compliance: research={len(files)} files, tweets={len(tweets)}, style={style}, over_280={over or \"none\"}')
" <<< "$PARSED" 2>/dev/null | tee -a "$LOG_FILE"

if [ "$PERMALINK" != "null" ] && [ "$PERMALINK" != "" ] && [ "$PERMALINK" != "PARSE_ERROR" ]; then
  echo "POSTED: $PERMALINK" | tee -a "$LOG_FILE"

  # Authoritative DB INSERT. Same pattern as reddit threads runner.
  PARSED="$PARSED" \
  CLAUDE_SESSION_ID="$CLAUDE_SESSION_ID" \
  PROJECT_ENV="$PROJECT" \
  POST_ACCOUNT="$POST_ACCOUNT" \
  REPO_DIR="$REPO_DIR" \
  /usr/bin/python3 <<'PYEOF' 2>&1 | tee -a "$LOG_FILE" || true
import json, os, sys
sys.path.insert(0, os.path.join(os.environ["REPO_DIR"], "scripts"))
import db as dbmod

parsed = json.loads(os.environ.get("PARSED") or "{}")
permalink = parsed.get("permalink") or ""
tweets    = parsed.get("tweets") or []
summary   = parsed.get("source_summary", "")
style     = parsed.get("engagement_style", "") or None
session   = os.environ.get("CLAUDE_SESSION_ID") or None
project   = os.environ.get("PROJECT_ENV", "")
account   = os.environ.get("POST_ACCOUNT", "")

if not permalink or not tweets:
    print("[db-insert] SKIP — empty permalink or tweets in structured_output")
    sys.exit(0)

# Stitch tweets into our_content with double-newline separators so downstream
# stats/refresh queries treat the whole thread as one row.
body = "\n\n".join(t.strip() for t in tweets if t and t.strip())
# Twitter doesn't have a separate title; use the first tweet's first 100 chars
# so dashboard listings have something readable.
title = (tweets[0] or "")[:100]

conn = dbmod.get_conn()
existing = conn.execute(
    "SELECT id FROM posts WHERE platform='twitter' AND our_url=%s LIMIT 1",
    (permalink,),
).fetchone()
if existing:
    print(f"[db-insert] SKIP — post {permalink} already in DB as id={existing[0]}")
    sys.exit(0)

row = conn.execute(
    """
    INSERT INTO posts
      (platform, thread_url, thread_author, thread_author_handle,
       thread_title, thread_content, our_url, our_content, our_account,
       source_summary, project_name, engagement_style,
       feedback_report_used, status, posted_at, claude_session_id)
    VALUES
      ('twitter', %s, %s, %s,
       %s, %s, %s, %s, %s,
       %s, %s, %s,
       TRUE, 'active', NOW(), %s::uuid)
    RETURNING id
    """,
    (permalink, account, account,
     title, body, permalink, body, account,
     summary, project, style,
     session),
).fetchone()
conn.commit()
print(f"[db-insert] OK — inserted posts.id={row[0]} for {permalink}")
PYEOF

elif [ -n "$ABORT_REASON" ] && [ "$ABORT_REASON" != "PARSE_ERROR" ]; then
  echo "ABORTED: $ABORT_REASON" | tee -a "$LOG_FILE"
else
  echo "UNKNOWN OUTCOME (check JSON output above)" | tee -a "$LOG_FILE"
fi

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-twitter-threads-*.log" -mtime +14 -delete 2>/dev/null || true
