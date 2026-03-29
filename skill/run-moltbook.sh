#!/bin/bash
# Social Autoposter - MoltBook posting only
# Finds MoltBook threads and posts 5 comments per run via API.
# Called by launchd every 2 hours.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-moltbook-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== MoltBook Post Run: $(date) ===" | tee "$LOG_FILE"

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for the moltbook account and content_angle.

Run the **Workflow: Post** section for **MoltBook ONLY**. Post up to 5 comments per run.

Steps:
1. Check existing posts: SELECT COUNT(*) FROM posts WHERE platform='moltbook' AND posted_at >= NOW() - INTERVAL '24 hours' (for logging only, no cap).
2. Find threads via MoltBook API:
   source $REPO_DIR/.env
   curl -s -H \"Authorization: Bearer \$MOLTBOOK_API_KEY\" \"https://www.moltbook.com/api/v1/posts?sort=hot&limit=50\"
   curl -s -H \"Authorization: Bearer \$MOLTBOOK_API_KEY\" \"https://www.moltbook.com/api/v1/posts?sort=new&limit=50\"
3. Check which threads we already posted in: SELECT thread_url FROM posts WHERE platform='moltbook'
4. Check last 5 comments for variety: SELECT our_content FROM posts WHERE platform='moltbook' ORDER BY id DESC LIMIT 5
5. Pick up to 5 threads where we have a genuine angle from content_angle. Skip mbc20/crypto spam threads.
6. For each thread, draft a comment in agent voice (\"my human\" not \"I\"). Follow Content Rules.
7. Post using the helper script:
   python3 $REPO_DIR/scripts/moltbook_post.py comment --post-id POST_UUID --content \"COMMENT\"
8. Determine which project from config.json this thread best matches (compare thread topic against each project's topics array). Every post must have a project_name.
9. Log each to database:
   INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
     thread_title, thread_content, our_url, our_content, our_account,
     source_summary, project_name, status, posted_at)
   VALUES ('moltbook', thread_url, 'various', 'various', title, '', our_url, content,
     'matthew-autoposter', 'moltbook comment engagement', PROJECT_NAME, 'active', NOW())
   Use the 'url' field from the script JSON output for our_url.

If the helper script reports rate limiting, wait the indicated seconds and retry. Max 3 retries per comment.
If nothing fits naturally, stop. Better to skip than force bad comments.

CRITICAL: Write as an agent - 'my human' not 'I'. NEVER use em dashes.
CRITICAL: Use full URLs for our_url, never bare fragments like '#abc123'.
CRITICAL: No browser needed - MoltBook is API-only." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-moltbook-*.log" -mtime +7 -delete 2>/dev/null || true
