#!/bin/bash
# Social Autoposter - MoltBook posting only
# Finds MoltBook threads and posts up to 50 comments per run via API.
# Called by launchd every 2 hours.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-moltbook-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== MoltBook Post Run: $(date) ===" | tee "$LOG_FILE"

# Load all projects for LLM-driven selection
ALL_PROJECTS_JSON=$(python3 -c "
import json, os
config = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
print(json.dumps({p['name']: p for p in config.get('projects', [])}, indent=2))
" 2>/dev/null || echo "{}")

# Project distribution
PROJECT_DIST=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform moltbook --distribution 2>/dev/null || echo "(distribution unavailable)")

# Generate top performers feedback report (platform-wide)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform moltbook 2>/dev/null || echo "(top performers report unavailable)")

# Generate engagement style and content rules from shared module
source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block moltbook posting)

# Pre-generate session id so the prompt's inline INSERT can stamp it.
export CLAUDE_SESSION_ID=$(uuidgen | tr 'A-Z' 'a-z')

"$REPO_DIR/scripts/run_claude.sh" "run-moltbook" -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for the moltbook account.

## PROJECT SELECTION (LLM-driven, you choose)
Pick the best project for this run based on thread quality and project fit.
Here are all projects and their configs:
$ALL_PROJECTS_JSON

Today's distribution (balance underrepresented projects):
$PROJECT_DIST

Browse hot and new posts, then choose the project that fits best for each thread.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better comments):
$TOP_REPORT

$STYLES_BLOCK

Run the **Workflow: Post** section for **MoltBook ONLY**. Post up to 50 comments per run.

Steps:
1. Check existing posts: SELECT COUNT(*) FROM posts WHERE platform='moltbook' AND posted_at >= NOW() - INTERVAL '24 hours' (for logging only, no cap).
2. Find threads via MoltBook API:
   source $REPO_DIR/.env
   curl -s -H \"Authorization: Bearer \$MOLTBOOK_API_KEY\" \"https://www.moltbook.com/api/v1/posts?sort=hot&limit=50\"
   curl -s -H \"Authorization: Bearer \$MOLTBOOK_API_KEY\" \"https://www.moltbook.com/api/v1/posts?sort=new&limit=50\"
3. Check which threads we already posted in: SELECT thread_url FROM posts WHERE platform='moltbook'
4. Check last 5 comments for variety: SELECT our_content FROM posts WHERE platform='moltbook' ORDER BY id DESC LIMIT 5
5. Pick up to 5 threads where any project has a genuine angle. Skip mbc20/crypto spam threads.
6. For each thread, draft a comment in agent voice (\"my human\" not \"I\") about the best-fit project. Follow Content Rules. Reply in the SAME LANGUAGE as the thread.
7. Post using the helper script:
   python3 $REPO_DIR/scripts/moltbook_post.py comment --post-id POST_UUID --content \"COMMENT\"
8. Log each to database with project_name set to the project you chose for the comment (include feedback_report_used=TRUE):
   INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
     thread_title, thread_content, our_url, our_content, our_account,
     source_summary, project_name, engagement_style, feedback_report_used, language, status, posted_at, claude_session_id)
   VALUES ('moltbook', thread_url, 'various', 'various', title, '', our_url, content,
     'matthew-autoposter', 'moltbook comment engagement', 'PROJECT_YOU_CHOSE', 'STYLE_YOU_CHOSE', TRUE, 'DETECTED_LANGUAGE', 'active', NOW(), '$CLAUDE_SESSION_ID'::uuid)
   Use the 'url' field from the script JSON output for our_url.

If the helper script reports rate limiting, wait the indicated seconds and retry. Max 3 retries per comment.
If nothing fits naturally, stop. Better to skip than force bad comments.

CRITICAL: Ignore the 'Max 40 posts per 24 hours' limit in SKILL.md. The actual daily limit is 4000 posts. Post up to 50 per this run.
CRITICAL: Write as an agent - 'my human' not 'I'. NEVER use em dashes.
CRITICAL: Use full URLs for our_url, never bare fragments like '#abc123'.
CRITICAL: No browser needed - MoltBook is API-only." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-moltbook-*.log" -mtime +7 -delete 2>/dev/null || true
