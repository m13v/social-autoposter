#!/bin/bash
# Social Autoposter - LinkedIn posting only
# Finds LinkedIn posts and adds up to 30 comments per run.
# Called by launchd every 3 hours.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-linkedin-$(date +%Y-%m-%d_%H%M%S).log"
RUN_START_EPOCH=$(date +%s)

echo "=== LinkedIn Post Run: $(date) ===" | tee "$LOG_FILE"

# Serialize with other linkedin-agent consumers (engage-linkedin,
# dm-outreach-linkedin, link-edit-linkedin, engage-dm-replies --platform linkedin,
# stats.sh Step 4). Without this, concurrent pipelines collide on the shared
# linkedin-agent browser profile and Claude calls abort mid-run.
source "$REPO_DIR/skill/lock.sh"
acquire_lock "linkedin-browser" 3600

# Load all projects for LLM-driven selection
ALL_PROJECTS_JSON=$(python3 -c "
import json, os
config = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
print(json.dumps({p['name']: p for p in config.get('projects', [])}, indent=2))
" 2>/dev/null || echo "{}")

# Project distribution (how many posts per project today, so LLM can balance)
PROJECT_DIST=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform linkedin --distribution 2>/dev/null || echo "(distribution unavailable)")

# Generate top performers feedback report (platform-wide)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform linkedin 2>/dev/null || echo "(top performers report unavailable)")

# Engaged LinkedIn URN IDs — every 16-19 digit ID we've ever stored in
# thread_url or our_url for platform='linkedin'. Same post can surface as
# /feed/update/urn:li:activity:X, /posts/...-share-Y-..., or
# /posts/...-ugcPost-Z-... with different numeric URNs, so we ship the
# whole set and have the LLM check ANY ID in a candidate URL against it.
# Capped at 4000 IDs to keep prompt size sane (one ID is ~20 bytes; even
# at the cap that's <100KB).
ENGAGED_IDS=$(python3 "$REPO_DIR/scripts/linkedin_url.py" --list-engaged-ids 2>/dev/null | head -4000 | tr '\n' ' ' || echo "")

# Generate engagement style and content rules from shared module
source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block linkedin posting)

set +e
"$REPO_DIR/scripts/run_claude.sh" "run-linkedin" --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json" -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for account name.

## PROJECT SELECTION (LLM-driven, you choose)
Pick the best project for this run based on post quality and project fit.
Here are all projects and their configs:
$ALL_PROJECTS_JSON

Today's distribution (balance underrepresented projects):
$PROJECT_DIST

You may search for posts across 1-2 projects to find the best opportunity:
  python3 $REPO_DIR/scripts/find_threads.py --include-linkedin --project 'PROJECT_NAME'
Choose the project that has the best natural fit with the post you find.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better comments):
$TOP_REPORT

## ENGAGED LINKEDIN POST IDS (DO NOT comment on any post whose URL contains any of these IDs)
The same LinkedIn post surfaces under several URL shapes with different
numeric URNs. The full set of URNs we've ever engaged with is:
$ENGAGED_IDS

BEFORE you submit a comment on any candidate post, do this check:
  1. Read the post's permalink in the page (the share dialog or URL bar).
     It will look like ONE of these shapes:
       https://www.linkedin.com/feed/update/urn:li:activity:<19-digit-id>/
       https://www.linkedin.com/posts/<slug>-activity-<19-digit-id>-<sfx>
       https://www.linkedin.com/posts/<slug>-share-<19-digit-id>-<sfx>
       https://www.linkedin.com/posts/<slug>-ugcPost-<19-digit-id>-<sfx>
  2. Extract every 16-19 digit number from that URL.
  3. If ANY extracted number appears in the ENGAGED LINKEDIN POST IDS
     list above, SKIP this post and find another. We already commented on it.
  4. You can also pre-check programmatically (faster than scanning the list):
       python3 $REPO_DIR/scripts/linkedin_url.py --check-engaged "<post-url>"
     Exit code 0 = engaged (skip). Exit code 1 = not engaged (proceed).
This is non-negotiable. Posting a second comment on a post we already
commented on costs us reputation and looks spammy.

$STYLES_BLOCK

Run the **Workflow: Post** section for **LinkedIn ONLY**. Follow every step:
1. Find candidate posts for 1-2 projects you think fit best:
     python3 $REPO_DIR/scripts/find_threads.py --include-linkedin --project 'PROJECT_NAME'
   From the output, pick ONLY linkedin candidates (discovery_method: search_url).
   Browse the search URL via mcp__linkedin-agent__browser_navigate to find actual posts.
   If nothing good for the first project, try another.
2. Pick the best LinkedIn post and the project that fits it best
3. **Engagement pre-check (MANDATORY)**: extract the post's permalink, then
   run \`python3 $REPO_DIR/scripts/linkedin_url.py --check-engaged \"<permalink>\"\`.
   If exit code is 0 (already engaged), discard this post and pick another.
   Only proceed to draft when exit code is 1.
4. Draft the comment using the engagement style that best fits the post. Professional but casual tone, NEVER use em dashes.
5. Post it using the linkedin-agent browser (mcp__linkedin-agent__* tools)
6. **CAPTURE THE POST URL** — BEFORE closing the tab, extract the actual post URL.
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
7. Log to database (MANDATORY tool call, do NOT use raw INSERT SQL):
     python3 $REPO_DIR/scripts/log_post.py --platform linkedin --thread-url THREAD_URL --our-url CAPTURED_FEED_UPDATE_URL --our-content 'YOUR_COMMENT_TEXT' --project PROJECT_YOU_CHOSE --thread-author AUTHOR --thread-title 'POST_TITLE' --engagement-style STYLE_YOU_CHOSE --language DETECTED_LANGUAGE
   This validates the URL, canonicalizes it, enforces status='active', AND
   refuses with DUPLICATE_LINKEDIN_POST if any URN ID overlaps with an
   existing row. If log_post returns DUPLICATE_LINKEDIN_POST, DO NOT
   delete the comment from LinkedIn (it's already up), but treat this
   post as no-longer-available for future runs and move on.

Up to 30 posts per run. If nothing fits, say '## No good post found' and stop.

CRITICAL: Ignore the 'Max 40 posts per 24 hours' limit in SKILL.md. The actual daily limit is 4000 posts. Post up to 30 per this run.
CRITICAL: Reply in the SAME LANGUAGE as the post. Match the language exactly.
CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Use ONLY mcp__linkedin-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and stop." 2>&1 | tee -a "$LOG_FILE"
RC=${PIPESTATUS[0]}
set -e

# --- Persist to run_monitor.log so Job History picks up LinkedIn Post rows ---
# Count posts inserted during this run via NOW() arithmetic to stay timezone-safe
# regardless of the psql client session tz.
ELAPSED=$(( $(date +%s) - RUN_START_EPOCH ))
WINDOW_SEC=$(( ELAPSED + 60 ))
POSTED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE platform='linkedin' AND posted_at >= NOW() - interval '$WINDOW_SEC seconds'" 2>/dev/null | tr -d '[:space:]' || true)
[ -z "$POSTED" ] && POSTED=0
FAILED=0
if [ "$RC" -ne 0 ] && [ "$POSTED" = "0" ]; then FAILED=1; fi
python3 "$REPO_DIR/scripts/log_run.py" --script post_linkedin --posted "$POSTED" --skipped 0 --failed "$FAILED" --cost 0 --elapsed "$ELAPSED" || true

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true
