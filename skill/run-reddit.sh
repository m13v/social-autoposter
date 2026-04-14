#!/bin/bash
# Social Autoposter - Reddit posting only
# Finds Reddit threads and posts up to 5 comments per run.
# Called by launchd every 15 minutes. Fewer posts, higher quality, less spam risk.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-reddit-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Reddit Post Run: $(date) ===" | tee "$LOG_FILE"

# Pick project based on weight distribution
PROJECT=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform reddit 2>/dev/null || echo "Fazm")
PROJECT_JSON=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform reddit --json 2>/dev/null || echo "{}")
echo "Selected project: $PROJECT" | tee -a "$LOG_FILE"

# Generate top performers feedback report (Reddit + project-specific)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform reddit --project "$PROJECT" 2>/dev/null || echo "(top performers report unavailable)")

# Generate engagement style and content rules from shared module
source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block reddit posting)

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for project details.

## TARGET PROJECT FOR THIS RUN: $PROJECT
You MUST find threads relevant to this project and post about it.
Project config: $PROJECT_JSON
Use this project's content_angle/voice if it has one, otherwise use the global content_angle.
The project_name for all posts this run MUST be '$PROJECT'.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better comments):
$TOP_REPORT

$STYLES_BLOCK

Run the **Workflow: Post** section for **Reddit ONLY**. Follow every step:
1. Find candidate threads: python3 $REPO_DIR/scripts/find_threads.py --project '$PROJECT'
2. Pick the best 3-5 Reddit threads relevant to the $PROJECT project. Prefer replying to OP (top-level reply) over replying to commenters. ONE comment per thread max.
3. Draft the comment. CRITICAL CONTENT RULES:
   - Go bimodal: either 1 punchy sentence (<100 chars) OR 4-5 sentences of real substance. AVOID the 2-3 sentence middle ground.
   - Start with 'I' or 'my' when possible (first-person experience gets 37% more upvotes).
   - NEVER mention product names (fazm, assrt, pieline, cyrano, terminator, mk0r, s4l) in comments. Caps upside at 10 upvotes.
   - NEVER include URLs or links. Average drops 2x with links.
   - NEVER use curious_probe style on Reddit (negative avg upvotes, reads as concern-trolling).
   - Favor contrarian and snarky_oneliner styles (highest performers).
   - NEVER use em dashes.
4. Post it using the reddit-agent browser (mcp__reddit-agent__* tools). Wait at least 3 minutes between posts.
5. Log to database with project_name='$PROJECT', engagement_style='STYLE_YOU_CHOSE' (MUST include feedback_report_used=TRUE in the INSERT)

Up to 5 posts per run. If nothing fits, say '## No good thread found' and stop. Quality over quantity. It is better to post 1 great comment than 5 mediocre ones.
CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Close browser tabs after every page visit (browser_tabs action 'close', NOT browser_close).
CRITICAL: Use ONLY mcp__reddit-agent__* tools for Reddit. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and stop.
CRITICAL: Wait at least 3 minutes between posting each comment. Sub-minute posting is the #1 spam signal and risks account suspension.
CRITICAL: Max 2 comments per subreddit per day. Check find_threads output and skip subs you already posted in today." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-reddit-*.log" -mtime +7 -delete 2>/dev/null || true
