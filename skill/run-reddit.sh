#!/bin/bash
# Social Autoposter - Reddit posting only
# Finds Reddit threads and posts up to 100 comments per run.
# Called by launchd every 1 hour.

set -euo pipefail

# Platform lock: wait up to 60min for previous run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "reddit" 3600

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

Run the **Workflow: Post** section for **Reddit ONLY**. Follow every step:
1. Find candidate threads: python3 $REPO_DIR/scripts/find_threads.py --project '$PROJECT'
2. Pick the best Reddit thread relevant to the $PROJECT project
3. Draft the comment using the project's voice/angle (follow Content Rules - NEVER use em dashes)
4. Post it using the reddit-agent browser (mcp__reddit-agent__* tools)
5. Log to database with project_name='$PROJECT' (MUST include feedback_report_used=TRUE in the INSERT)

Up to 100 posts per run. If nothing fits, say '## No good thread found' and stop.

CRITICAL: Ignore the 'Max 40 posts per 24 hours' limit in SKILL.md. The actual daily limit is 4000 posts. Post up to 100 per this run.
CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Close browser tabs after every page visit (browser_tabs action 'close', NOT browser_close).
CRITICAL: Use ONLY mcp__reddit-agent__* tools for Reddit. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and stop." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-reddit-*.log" -mtime +7 -delete 2>/dev/null || true
