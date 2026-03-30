#!/bin/bash
# Social Autoposter - LinkedIn posting only
# Finds LinkedIn posts and adds up to 3 comments per run.
# Called by launchd every 3 hours.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-linkedin-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== LinkedIn Post Run: $(date) ===" | tee "$LOG_FILE"

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for the linkedin_topics list and account name.

Run the **Workflow: Post** section for **LinkedIn ONLY**. Follow every step:
1. Find candidate posts: python3 $REPO_DIR/scripts/find_threads.py --include-linkedin
   From the output, pick ONLY linkedin candidates (discovery_method: search_url).
   Browse the search URL via mcp__linkedin-agent__browser_navigate to find actual posts.
2. Pick the best LinkedIn post to comment on
3. Draft the comment (follow Content Rules - NEVER use em dashes, professional but casual tone)
4. Post it using the linkedin-agent browser (mcp__linkedin-agent__* tools)
5. **CAPTURE THE POST URL** — BEFORE closing the tab, extract the actual post URL.
   After posting the comment, run this JS via mcp__linkedin-agent__browser_run_code:
   \`\`\`javascript
   async (page) => {
     const url = page.url();
     // If on a feed/update page, use it directly
     if (url.includes('/feed/update/')) return url.split('?')[0];
     // Otherwise extract from the page - find the post's share/permalink
     const shareLink = await page.evaluate(() => {
       // Look for the post's activity URN in the page
       const postEl = document.querySelector('[data-urn*=\"activity\"], [data-id*=\"activity\"]');
       if (postEl) {
         const urn = postEl.getAttribute('data-urn') || postEl.getAttribute('data-id');
         const match = urn.match(/activity:(\\d+)/);
         if (match) return 'https://www.linkedin.com/feed/update/urn:li:activity:' + match[1] + '/';
       }
       // Fallback: check URL bar or og:url meta
       const ogUrl = document.querySelector('meta[property=\"og:url\"]');
       if (ogUrl) return ogUrl.content;
       return null;
     });
     return shareLink || url;
   }
   \`\`\`
   Use this URL as \`our_url\` in the database INSERT. It MUST be a linkedin.com/feed/update/ URL.
   If you cannot get a feed/update URL, use the current page URL as fallback.
6. Determine project_name by matching thread topic to config.json projects[].topics
7. Log to database (MUST include project_name in the INSERT). Use the captured feed/update URL for our_url.

Up to 3 posts per run. If nothing fits, say '## No good post found' and stop.

CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Use ONLY mcp__linkedin-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and stop." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true
