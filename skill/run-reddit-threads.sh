#!/bin/bash
# Social Autoposter - Original Reddit thread poster (generalized)
#
# Picks one (project, subreddit) target via pick_thread_target.py,
# which enforces a 3-day-per-subreddit floor, then spawns a Claude session
# with the reddit-agent to research, draft, and post ONE original thread.
#
# Called by launchd (daily). See com.m13v.social-reddit-threads.plist.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
CONFIG_FILE="$REPO_DIR/config.json"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-reddit-threads-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Reddit Threads Run: $(date) ===" | tee "$LOG_FILE"

# Pick target: one (project, subreddit) pair, 3-day floor enforced
TARGET_JSON=$(/usr/bin/python3 "$REPO_DIR/scripts/pick_thread_target.py" --json 2>&1) || {
  echo "NO_ELIGIBLE_TARGET: every eligible subreddit was posted to within 3 days. Stopping." | tee -a "$LOG_FILE"
  exit 0
}

PROJECT=$(echo "$TARGET_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin)['project']['name'])")
SUBREDDIT=$(echo "$TARGET_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin)['subreddit'])")
IS_OWN=$(echo "$TARGET_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin)['is_own_community'])")

echo "Target: project=$PROJECT subreddit=$SUBREDDIT own_community=$IS_OWN" | tee -a "$LOG_FILE"

# Normalize subreddit slug (strip r/)
SUB_SLUG=$(echo "$SUBREDDIT" | sed 's|^r/||I')

# Extract full project config and threads section
PROJECT_JSON=$(/usr/bin/python3 -c "
import json
c = json.load(open('$CONFIG_FILE'))
for p in c['projects']:
    if p['name'] == '$PROJECT':
        print(json.dumps(p, indent=2))
        break
")

THREADS_JSON=$(echo "$PROJECT_JSON" | /usr/bin/python3 -c "
import sys, json
p = json.load(sys.stdin)
print(json.dumps(p.get('threads', {}), indent=2))
")

# Compute dynamic context (e.g., day counter for Vipassana)
export THREADS_JSON_ENV="$THREADS_JSON"
DYNAMIC_BLOCK=$(/usr/bin/python3 -c "
import json, datetime, os
t = json.loads(os.environ['THREADS_JSON_ENV'])
lines = []
dc = t.get('dynamic_context') or {}
day = dc.get('day_counter')
if day:
    base = day['base_count']
    ref = datetime.date.fromisoformat(day['ref_date'])
    today = datetime.date.today()
    days = (today - ref).days
    count = base + days
    label = day.get('label', 'day count')
    lines.append(f'{label.title()}: {count}+')
facts = dc.get('static_facts') or []
if facts:
    lines.append('Static facts:')
    for f in facts:
        lines.append(f'- {f}')
print('\n'.join(lines))
")

echo "Dynamic context:" | tee -a "$LOG_FILE"
echo "$DYNAMIC_BLOCK" | tee -a "$LOG_FILE"

# Optional guide dir (Vipassana uses this; other projects may not have it)
GUIDE_DIR=$(echo "$THREADS_JSON" | /usr/bin/python3 -c "
import sys, json, os
t = json.load(sys.stdin)
cs = t.get('content_sources') or {}
gd = cs.get('guide_dir') or ''
if gd:
    print(os.path.expanduser(gd))
")
GUIDES_BLOCK=""
if [ -n "$GUIDE_DIR" ] && [ -d "$GUIDE_DIR" ]; then
  GUIDES=$(ls -d "$GUIDE_DIR"/*/ 2>/dev/null | xargs -I{} basename {} | tr '\n' ', ')
  GUIDES_BLOCK="Available guide slugs: $GUIDES
Guide source files live at: $GUIDE_DIR/[slug]/page.tsx
Before drafting, READ the page.tsx source of any guide that relates to your chosen topic. Use specific details, quotes, or framing from the guide content to make the post richer and more authentic."
  echo "Guides available: $GUIDES" | tee -a "$LOG_FILE"
fi

# Recent posts for this subreddit (avoid repeats)
RECENT_POSTS=$(psql "$DATABASE_URL" -t -A -c "
  SELECT thread_title FROM posts
  WHERE thread_url ILIKE '%/r/${SUB_SLUG}/%'
    AND thread_url = our_url
  ORDER BY posted_at DESC LIMIT 15
" 2>/dev/null || echo "(could not fetch recent posts)")

# Top performing posts for this project (tone calibration)
TOP_POSTS=$(psql "$DATABASE_URL" -t -A -c "
  SELECT thread_title, upvotes, comments_count, views FROM posts
  WHERE project_name='${PROJECT}' AND thread_url=our_url AND status='active'
    AND (COALESCE(upvotes,0) + COALESCE(comments_count,0)*3) > 5
  ORDER BY (COALESCE(upvotes,0) + COALESCE(comments_count,0)*3) DESC
  LIMIT 10
" 2>/dev/null || echo "(could not fetch top posts)")

# Cadence note
if [ "$IS_OWN" = "True" ]; then
  CADENCE_NOTE="This is our OWNED subreddit. Daily cadence. Be yourself, no product pitches."
else
  CADENCE_NOTE="This is an EXTERNAL subreddit. We only post here once every ~week. The thread must pass the subreddit's self-promo / community bar. No product links unless genuinely relevant (max 1)."
fi

claude -p "You are posting an original thread to ${SUBREDDIT} for the ${PROJECT} project.

## Config & Rules
Read $SKILL_FILE for content rules and anti-AI-detection checklist.
Read $CONFIG_FILE and find the project named '${PROJECT}'. Use its content_angle, voice, and threads section.

## Target
Project: ${PROJECT}
Subreddit: ${SUBREDDIT}
Own community: ${IS_OWN}
${CADENCE_NOTE}

## Project threads config
${THREADS_JSON}

## Dynamic context (live-calculated)
${DYNAMIC_BLOCK}

${GUIDES_BLOCK}

## Recent posts in ${SUBREDDIT} by us (DO NOT repeat these topics)
${RECENT_POSTS}

## Top performing ${PROJECT} posts (match this tone/style)
${TOP_POSTS}

## Task: Create exactly 1 original thread

1. Pick a topic from the threads.topic_angles list that:
   - Has NOT been posted recently in this subreddit (check list above)
   - Fits this subreddit's community and rules
   - Invites genuine discussion (ends with a question or open thread)

2. Draft the post following ALL content rules from SKILL.md and the project's voice object:
   - No em dashes. Use commas, periods, or regular dashes (-) instead
   - No markdown formatting (no ##, no **bold**, no lists)
   - 2-4 short paragraphs, casual tone, first person
   - Include at least one imperfection (fragment, aside, lowercase)
   - Title: lowercase, no clickbait patterns
   - Read it out loud. If it sounds like a blog post, rewrite it
   - Follow the threads.voice_notes guidance above

3. Before posting, BRIEFLY check the subreddit rules page and any posting flair requirements:
   - Navigate to https://old.reddit.com/${SUBREDDIT}/about/rules
   - If the subreddit has strict no-self-promo rules and our post would read as promotional, ABORT and log: 'ABORTED: ${SUBREDDIT} rules incompatible with this post'
   - Close the tab after reading

4. Post it using mcp__reddit-agent__* browser tools:
   - Navigate to https://old.reddit.com/${SUBREDDIT}/submit?selftext=true
   - Fill title via textarea[name=\"title\"] and body via textarea[name=\"text\"] (use browser_evaluate to set value directly if locator fails)
   - Select flair if the subreddit requires one
   - Click submit button
   - Verify the post appeared and capture the permalink URL
   - Close tabs after each navigation

5. Log to database:
   INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
     thread_title, thread_content, our_url, our_content, our_account,
     source_summary, project_name, status, posted_at)
   VALUES ('reddit', PERMALINK, 'Deep_Ad1959', 'Deep_Ad1959',
     TITLE, BODY, PERMALINK, BODY, 'Deep_Ad1959',
     'thread on ${SUBREDDIT} for ${PROJECT}', '${PROJECT}', 'active', NOW());

6. After posting, output a single line summary:
   POSTED: [permalink] | [title]

CRITICAL: NEVER use em dashes in any content.
CRITICAL: Use ONLY mcp__reddit-agent__* tools. NEVER use generic browser tools.
CRITICAL: If browser tools are blocked/timeout, wait 30 seconds and retry up to 3 times. If still blocked, log the error and stop.
CRITICAL: Close browser tabs after page visits (browser_tabs action 'close', NOT browser_close)." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-reddit-threads-*.log" -mtime +14 -delete 2>/dev/null || true
