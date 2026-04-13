#!/bin/bash
# Social Autoposter - GitHub Issues posting
# Find relevant open issues across GitHub, post helpful comments, self-reply with specific file links.
# Called by launchd every 4 hours.

set -euo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/github-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== GitHub Issues Run: $(date) ===" | tee "$LOG_FILE"

# Pick project based on weight distribution
PROJECT=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform github_issues 2>/dev/null || echo "Fazm")
PROJECT_JSON=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform github_issues --json 2>/dev/null || echo "{}")
echo "Selected project: $PROJECT" | tee -a "$LOG_FILE"

# Generate engagement style and content rules from shared module
source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block github_issues posting)

# Load exclusions from config
EXCLUDED_REPOS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('github_repos',[])))" 2>/dev/null || echo "")
EXCLUDED_AUTHORS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('authors',[])))" 2>/dev/null || echo "")

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Also read $REPO_DIR/config.json for accounts, projects, and search_topics.

## TARGET PROJECT FOR THIS RUN: $PROJECT
You MUST find GitHub issues relevant to this project and comment about it.
Project config: $PROJECT_JSON
Use this project's github_search_topics if available, otherwise use the global search_topics.
The project_name for all posts this run MUST be '$PROJECT'.

EXCLUSIONS — do NOT interact with these:
- Excluded repos/orgs: $EXCLUDED_REPOS
- Excluded authors: $EXCLUDED_AUTHORS
Skip any issues from excluded repos/orgs. Do not reply to excluded authors. Do not post on issues owned by excluded orgs.

$STYLES_BLOCK

TARGETING (data-driven, from engagement analysis):
- Best topics: Agents (8.6%), Accessibility (8.3%), Voice/ASR (8.0%), Tool Use (7.9%). Prioritize these.
- Avoid: Browser automation (0% engagement), reduce MCP volume (oversaturated, only 6.3%).
- Target small-to-mid repos (<1000 stars) where maintainer is active. Solo maintainers reply; big repos bury comments.
- Prefer issues updated in last 7 days.

COMMENT STYLE (what gets replies):
- Lead with the pain you hit, then your fix. \"the token overhead is brutal\" > \"here is how to optimize\".
- Keep it conversational, no code blocks in the initial comment.
- Aim for 400-600 chars. Short enough to read, long enough to show real experience.
- Do NOT include any links to our repos in the comment. Links are added later by Phase D after the comment earns engagement.
- Share specific implementation details (file names, metrics, tradeoffs), not generic advice.

Run the **Workflow: GitHub Issues** section. Follow every step:
1. Search for relevant issues using the $PROJECT project's github_search_topics (from its config above).
   If the project doesn't have github_search_topics, use config.json -> accounts.github.search_topics.
   Rotate through different search topics each run - don't always search the same keywords.
   Use: gh search issues \"TOPIC\" --limit 10 --state open --sort updated
3. Check dedup: SELECT thread_url FROM posts WHERE platform='github_issues'
4. Pick the best 2-3 issues where our experience genuinely adds value
5. Read each issue fully (body + existing comments)
6. Draft helpful comments (follow Content Rules and COMMENT STYLE above - NEVER use em dashes)
7. Post via: gh issue comment NUMBER -R OWNER/REPO --body \"...\"
8. Log to database with project_name='$PROJECT', engagement_style='STYLE_YOU_CHOSE' (MUST include engagement_style and project_name in the INSERT)
   IMPORTANT: Save the comment URL from gh output. Store it as our_url in the INSERT.

Do NOT self-reply with links. Links are added later by a separate pipeline (Phase D) that edits the comment after it earns engagement.

Post to 10 issues per run. Spread across different repos and topics.

CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: In self-replies, link to SPECIFIC FILES (blob/main/path/to/file.ext), not just repo homepages." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"

# Clean up old logs (keep last 7 days)
find "$LOG_DIR" -name "github-*.log" -mtime +7 -delete 2>/dev/null || true
