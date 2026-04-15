#!/bin/bash
# Social Autoposter - Reddit posting only
# Finds Reddit threads and posts 1 best comment per run.
# Called by launchd every 3 minutes. One post, highest quality, natural spacing.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-reddit-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Reddit Post Run: $(date) ===" | tee "$LOG_FILE"

# Load all projects for LLM-driven selection
ALL_PROJECTS_JSON=$(python3 -c "
import json, os
config = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
print(json.dumps({p['name']: p for p in config.get('projects', [])}, indent=2))
" 2>/dev/null || echo "{}")

# Project distribution (how many posts per project today, so LLM can balance)
PROJECT_DIST=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform reddit --distribution 2>/dev/null || echo "(distribution unavailable)")

# Generate top performers feedback report (platform-wide)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform reddit 2>/dev/null || echo "(top performers report unavailable)")

# Generate engagement style and content rules from shared module
source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block reddit posting)

# Load banned/restricted subreddits list
BANNED_SUBS=$(python3 -c "
import json, os
path = os.path.expanduser('~/social-autoposter/scripts/.restricted_subreddits.json')
if os.path.exists(path):
    subs = list(json.load(open(path)).keys())
    print(', '.join('r/' + s for s in sorted(subs)))
else:
    print('(none)')
" 2>/dev/null || echo "(unavailable)")

# Active campaigns (prompt injections + budget tracking)
CAMPAIGN_BLOCK=$(python3 "$REPO_DIR/scripts/active_campaigns.py" --platform reddit --repo-dir "$REPO_DIR" 2>/dev/null || echo "")
CAMPAIGN_IDS=$(python3 "$REPO_DIR/scripts/active_campaigns.py" --platform reddit --json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('campaign_ids',''))" 2>/dev/null || echo "")
if [ -n "$CAMPAIGN_IDS" ]; then
    echo "Active campaigns: $CAMPAIGN_IDS" | tee -a "$LOG_FILE"
else
    echo "Active campaigns: none" | tee -a "$LOG_FILE"
fi

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for project details.

## PROJECT SELECTION (LLM-driven, you choose)
Pick the best project for this run based on thread quality and project fit.
Here are all projects and their configs:
$ALL_PROJECTS_JSON

Today's distribution (balance underrepresented projects):
$PROJECT_DIST

You may search for threads across 1-2 projects to find the best opportunity:
  python3 $REPO_DIR/scripts/find_threads.py --project 'PROJECT_NAME'
Choose the project that has the best natural fit with the thread you find.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better comments):
$TOP_REPORT

$STYLES_BLOCK

$CAMPAIGN_BLOCK

Run the **Workflow: Post** section for **Reddit ONLY**. Follow every step:
1. Find candidate threads for 1-2 projects you think fit best:
     python3 $REPO_DIR/scripts/find_threads.py --project 'PROJECT_NAME'
   Try the project with the best thread opportunities. If nothing good, try another project.
2. Pick the single best Reddit thread. Prefer replying to OP (top-level reply) over replying to commenters.
3. Draft the comment. CRITICAL CONTENT RULES:
   - Go bimodal: either 1 punchy sentence (<100 chars) OR 4-5 sentences of real substance. AVOID the 2-3 sentence middle ground.
   - Start with 'I' or 'my' when possible (first-person experience gets 37% more upvotes).
   - NEVER mention product names (fazm, assrt, pieline, cyrano, terminator, mk0r, s4l) in comments. Caps upside at 10 upvotes.
   - NEVER include URLs or links. Average drops 2x with links.
   - NEVER use curious_probe style on Reddit (negative avg upvotes, reads as concern-trolling).
   - Favor contrarian and snarky_oneliner styles (highest performers).
   - NEVER use em dashes.
4. Post it using the reddit-agent browser (mcp__reddit-agent__* tools). Wait at least 3 minutes between posts.
5. Log to database (MANDATORY tool call, do NOT use raw INSERT SQL):
     python3 $REPO_DIR/scripts/log_post.py --platform reddit --thread-url THREAD_URL --our-url OUR_PERMALINK --our-content 'YOUR_COMMENT_TEXT' --project PROJECT_YOU_CHOSE --thread-author THREAD_AUTHOR --thread-title 'THREAD_TITLE' --engagement-style STYLE_YOU_CHOSE --language DETECTED_LANGUAGE
   This validates the URL and enforces status='active'. Parse the JSON output for post_id.
   If you could not capture a valid our_url (must start with http), do NOT log the post. Skip to the end.
6. **Campaign attribution (only if the ACTIVE CAMPAIGNS section above was non-empty).** Active campaign IDs for this run: '$CAMPAIGN_IDS'. If that string is non-empty, run: python3 $REPO_DIR/scripts/campaign_bump.py --post-id POST_ID_FROM_STEP_5 --campaign-ids $CAMPAIGN_IDS
   This is required when any campaign is active. Skipping it will cause the campaign to over-post beyond its budget.

## BANNED SUBREDDITS (we are banned or have poor performance here, NEVER post)
$BANNED_SUBS
If find_threads.py returns threads from any of these subs, skip them entirely.

Post exactly 1 comment per run. Pick the single best thread and write the best possible comment. If nothing fits, say '## No good thread found' and stop.
CRITICAL: Reply in the SAME LANGUAGE as the thread/post. Match the language exactly.
CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Close browser tabs after every page visit (browser_tabs action 'close', NOT browser_close).
CRITICAL: Use ONLY mcp__reddit-agent__* tools for Reddit. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and stop.
CRITICAL: Only 1 post per run. The 3-minute launchd interval handles spacing naturally.
CRITICAL: Max 2 comments per subreddit per day. Check find_threads output and skip subs you already posted in today." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-reddit-*.log" -mtime +7 -delete 2>/dev/null || true
