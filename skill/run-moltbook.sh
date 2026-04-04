#!/bin/bash
# Social Autoposter - MoltBook posting only
# Finds MoltBook threads and posts up to 50 comments per run via API.
# Called by launchd every 2 hours.

set -euo pipefail

# Platform lock: wait up to 60min for previous run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "moltbook" 3600

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-moltbook-$(date +%Y-%m-%d_%H%M%S).log"

RUN_START=$(date +%s)
echo "=== MoltBook Post Run: $(date) ===" | tee "$LOG_FILE"

# Pick project based on weight distribution
PROJECT=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform moltbook 2>/dev/null || echo "Fazm")
PROJECT_JSON=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform moltbook --json 2>/dev/null || echo "{}")
echo "Selected project: $PROJECT" | tee -a "$LOG_FILE"

# Generate top performers feedback report (Moltbook-specific)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform moltbook 2>/dev/null || echo "(top performers report unavailable)")

claude --strict-mcp-config -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for the moltbook account.

## TARGET PROJECT FOR THIS RUN: $PROJECT
You MUST find Moltbook threads relevant to this project and comment about it.
Project config: $PROJECT_JSON
Use this project's content_angle/voice if it has one, otherwise use the global content_angle.
The project_name for all posts this run MUST be '$PROJECT'.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better comments):
$TOP_REPORT

Run the **Workflow: Post** section for **MoltBook ONLY**. Post up to 50 comments per run.

Steps:
1. Check existing posts: SELECT COUNT(*) FROM posts WHERE platform='moltbook' AND posted_at >= NOW() - INTERVAL '24 hours' (for logging only, no cap).
2. Find threads via MoltBook API:
   source $REPO_DIR/.env
   curl -s -H \"Authorization: Bearer \$MOLTBOOK_API_KEY\" \"https://www.moltbook.com/api/v1/posts?sort=hot&limit=50\"
   curl -s -H \"Authorization: Bearer \$MOLTBOOK_API_KEY\" \"https://www.moltbook.com/api/v1/posts?sort=new&limit=50\"
3. Check which threads we already posted in: SELECT thread_url FROM posts WHERE platform='moltbook'
4. Check last 5 comments for variety: SELECT our_content FROM posts WHERE platform='moltbook' ORDER BY id DESC LIMIT 5
5. Pick up to 5 threads where the $PROJECT project has a genuine angle. Skip mbc20/crypto spam threads.
6. For each thread, draft a comment in agent voice (\"my human\" not \"I\") about $PROJECT. Follow Content Rules.
7. Post using the helper script:
   python3 $REPO_DIR/scripts/moltbook_post.py comment --post-id POST_UUID --content \"COMMENT\"
8. Log each to database with project_name='$PROJECT' (include feedback_report_used=TRUE):
   INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
     thread_title, thread_content, our_url, our_content, our_account,
     source_summary, project_name, status, posted_at)
   VALUES ('moltbook', thread_url, 'various', 'various', title, '', our_url, content,
     'matthew-autoposter', 'moltbook comment engagement', '$PROJECT', 'active', NOW())
   Use the 'url' field from the script JSON output for our_url.

If the helper script reports rate limiting, wait the indicated seconds and retry. Max 3 retries per comment.
If nothing fits naturally, stop. Better to skip than force bad comments.

CRITICAL: Ignore the 'Max 40 posts per 24 hours' limit in SKILL.md. The actual daily limit is 4000 posts. Post up to 50 per this run.
CRITICAL: Write as an agent - 'my human' not 'I'. NEVER use em dashes.
CRITICAL: Use full URLs for our_url, never bare fragments like '#abc123'.
CRITICAL: No browser needed - MoltBook is API-only." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"

# Log run to persistent monitor
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
POSTED=$(grep -c "INSERT INTO posts" "$LOG_FILE" 2>/dev/null) || true
SKIPPED=$(grep -ci "skipped" "$LOG_FILE" 2>/dev/null) || true
FAILED=$(grep -ci "error\|failed\|FAILED" "$LOG_FILE" 2>/dev/null) || true
python3 "$REPO_DIR/scripts/log_run.py" --script "post_moltbook" --posted "$POSTED" --skipped "$SKIPPED" --failed "$FAILED" --cost 0 --elapsed "$RUN_ELAPSED"

find "$LOG_DIR" -name "run-moltbook-*.log" -mtime +7 -delete 2>/dev/null || true
