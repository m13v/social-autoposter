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

## ENGAGED LINKEDIN POST DEDUP (DO NOT comment on a post we already commented on)
The same LinkedIn post surfaces under several URL shapes with different
numeric URNs (activity URN, share URN, ugcPost URN). For example
'/feed/update/urn:li:activity:7443531396306100224/' and
'/posts/<slug>-share-7443531393558638592-<sfx>' are the SAME post but
contain different numbers. The URL bar alone is not a reliable identity.

The mandatory pre-comment check is in step 3 of the workflow below: walk
the rendered DOM for every URN it contains, then pipe ALL of them to
linkedin_url.py --check-engaged-ids. If any one collides with our DB,
skip the post.

This is non-negotiable. Posting a second comment on a post we already
commented on costs us reputation and looks spammy.

$STYLES_BLOCK

Run the **Workflow: Post** section for **LinkedIn ONLY**. Follow every step:
1. Find candidate posts for 1-2 projects you think fit best:
     python3 $REPO_DIR/scripts/find_threads.py --include-linkedin --project 'PROJECT_NAME'
   From the output, pick ONLY linkedin candidates (discovery_method: search_url).
   Browse the search URL via mcp__linkedin-agent__browser_navigate to find actual posts.
   If nothing good for the first project, try another.
2. Pick the best LinkedIn post and the project that fits it best.
2b. **SELF-AUTHOR GUARD (MANDATORY, programmatic).** Our LinkedIn
    account is Matthew Diakonov, profile at
    https://www.linkedin.com/in/m13v/. Search results frequently
    surface posts we authored (Matthew posts on MCP, AI agents,
    desktop automation, the same topics our search runs use).
    Commenting on our own post is wasteful and looks like astroturfing.

    2b.1. Extract the candidate post's author profile URL from the
        rendered DOM via mcp__linkedin-agent__browser_run_code. The
        author link is the anchor wrapping the author name in the
        post header. Sample JS:
   \`\`\`javascript
   async (page) => {
     return await page.evaluate(() => {
       // First post in the rendered list / detail page
       const post = document.querySelector('[data-urn*=\"activity\"], div.feed-shared-update-v2');
       if (!post) return null;
       const anchor = post.querySelector('a[href*=\"/in/\"]');
       return anchor ? anchor.href : null;
     });
   }
   \`\`\`
        If multiple candidate posts are visible, capture each post's
        author URL and check them all individually.

    2b.2. For each captured author URL, run:
        python3 $REPO_DIR/scripts/linkedin_url.py --check-self-author 'AUTHOR_URL_HERE'
        Substitute AUTHOR_URL_HERE with the actual URL string (single
        quotes keep shell metachars safe). Exit code 0 = self-authored,
        SKIP this post and pick another. Exit code 1 = not self,
        proceed to step 3 with this candidate. The script accepts
        full URLs, /in/SLUG/ paths, or bare slugs; it canonicalizes
        case and strips trailing slashes, so pass whatever you got.

    2b.3. Visual fallback. If for some reason the DOM walk returns
        null and you cannot get an author URL, look for a '· You' or
        'Author' tag next to the author name in the snapshot. Either
        means it's our post; skip it. Do NOT proceed without a
        successful author check.
3. **Engagement pre-check (MANDATORY, must run before drafting)**.
   The URL bar alone is not a reliable identity for a LinkedIn post,
   because /posts/<slug>-share-<X>-<sfx> and /feed/update/activity:<Y>/
   are the same post with different URNs. Walk the rendered DOM for ALL
   URNs and pipe them to linkedin_url.py.

   3a. With the candidate post page already loaded in linkedin-agent,
       run this JS via mcp__linkedin-agent__browser_run_code and capture
       its return value (an object with allIds and activityId):
   \`\`\`javascript
   async (page) => {
     await page.waitForTimeout(2000);
     return await page.evaluate(() => {
       const all = new Set();
       let activity = null;
       const re = /(activity|share|ugcPost)[:_-](\\d{16,19})/gi;
       const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
       let node, scanned = 0;
       while ((node = walker.nextNode()) && scanned++ < 8000) {
         for (const a of node.attributes || []) {
           const v = a.value || '';
           let m;
           re.lastIndex = 0;
           while ((m = re.exec(v))) {
             all.add(m[2]);
             if (m[1].toLowerCase() === 'activity' && !activity) activity = m[2];
           }
         }
       }
       const urlMatch = location.href.match(/(\\d{16,19})/g) || [];
       urlMatch.forEach(v => all.add(v));
       return { allIds: Array.from(all), activityId: activity };
     });
   }
   \`\`\`

   3b. Pipe the allIds array (comma-separated) to:
       python3 $REPO_DIR/scripts/linkedin_url.py --check-engaged-ids "ID1,ID2,ID3"
       Exit code 0 = already engaged, SKIP this post and pick another.
       Exit code 1 = not engaged, proceed to step 4.
       The check matches against posts.urns (GIN-indexed, all known
       URN forms for each prior post) AND against thread_url/our_url
       ILIKE as a fallback. Going forward, log_post.py --urns seeds
       posts.urns from the createComment network response so search-page
       DOM walks (which often only expose the ugcPost URN) collide
       cleanly with rows whose canonical our_url stored the activity URN.

   3c. Remember the activityId returned by the JS. You'll need it in
       step 6 to construct a canonical our_url.

4. Draft the comment using the engagement style that best fits the post. Professional but casual tone, NEVER use em dashes. Apply the matched project's \`voice\` block from ALL_PROJECTS_JSON above: follow \`voice.tone\`, never violate any item in \`voice.never\`, and mirror \`voice.examples\` / \`voice.examples_good\` when present.
5. Post it using the linkedin-agent browser (mcp__linkedin-agent__* tools).
5b. **POST-SUBMIT VERIFICATION (MANDATORY).** LinkedIn often silently
   rejects comments via a soft-block that looks like success (editor
   clears, no exception raised). You MUST verify the comment actually
   landed before logging as success.

   5b.1. Immediately after clicking the Comment submit button, call:
       mcp__linkedin-agent__browser_network_requests with:
         filter: 'normCommentsCreate|normComments|contentcreation|socialActions'
         requestBody: true
         static: false
       Save the response. Look for the POST request to a comment-create
       endpoint and note its status code and response body. Record this
       string verbatim as NETWORK_RESPONSE.

   5b.1.1. **EXTRACT EVERY URN ID FROM NETWORK_RESPONSE**. The
       createComment payload typically references the post under several
       URN forms in the same response (activity, ugcPost, share, comment).
       Walk NETWORK_RESPONSE for every 16-19 digit number and collect
       them into ALL_POST_URNS (comma-separated, deduped). Include the
       activityId from step 3c too. You will pass ALL_POST_URNS to
       log_post.py in step 7 so dedup catches future cross-URN hits on
       the same post (search-page DOM only exposes the ugcPost URN
       while our DB previously stored only the activity URN; storing
       every URN closes that gap).
       If you cannot get NETWORK_RESPONSE (rare), fall back to
       activityId alone — log_post.py will still extract IDs from
       thread_url and our_url and merge them.

   5b.2. Take a viewport screenshot via
       mcp__linkedin-agent__browser_take_screenshot. The toast
       'Your comment could not be created at this time' is brief, so
       grab it quickly. Save the screenshot path.

   5b.3. Take a fresh snapshot via mcp__linkedin-agent__browser_snapshot
       (depth 12) and inspect:
         (a) the comment count near the post (e.g. '4 comments' before,
             should now show '5 comments' or higher)
         (b) any newly-rendered comment whose author text contains
             'Matthew Diakonov' or 'You'
         (c) presence of the toast text 'could not be created'
         (d) editor textbox state (active/empty placeholder vs still
             holding your text)

   5b.4. Decide:
       SUCCESS = comment count increased AND a fresh comment by you
           is in the DOM AND no 'could not be created' toast.
       REJECTED = toast text present OR comment count unchanged after
           reload (you may navigate to the same URL once to confirm).

   5b.5. If REJECTED: do NOT call the success log_post.py path. Instead
       record the rejection so dedup blocks future retries on this thread:
         python3 $REPO_DIR/scripts/log_post.py --rejected \\
           --platform linkedin \\
           --thread-url THREAD_URL \\
           --our-content 'YOUR_COMMENT_TEXT' \\
           --project PROJECT_YOU_CHOSE \\
           --thread-author AUTHOR \\
           --thread-title 'POST_TITLE' \\
           --engagement-style STYLE_YOU_CHOSE \\
           --language DETECTED_LANGUAGE \\
           --rejection-reason 'TOAST: <verbatim toast text or quiet-fail>' \\
           --network-response 'NETWORK_RESPONSE_FROM_5b.1'
       Then stop the run with '## Comment soft-blocked, ledgered'. Do
       NOT retry the same post. Do NOT pick another post in the same
       run (consecutive submits compound the throttle).

   5b.6. If SUCCESS: proceed to step 6.

6. **CAPTURE THE CANONICAL POST URL**. Use the activityId you saved in
   step 3c to build:
       https://www.linkedin.com/feed/update/urn:li:activity:<activityId>/
   That is the our_url you log. If you somehow have no activityId
   (rare; the DOM walk in 3a almost always finds one), fall back to the
   current page URL via page.url().split('?')[0].
7. Log to database (MANDATORY tool call, do NOT use raw INSERT SQL):
     python3 $REPO_DIR/scripts/log_post.py --platform linkedin --thread-url THREAD_URL --our-url CAPTURED_FEED_UPDATE_URL --our-content 'YOUR_COMMENT_TEXT' --project PROJECT_YOU_CHOSE --thread-author AUTHOR --thread-title 'POST_TITLE' --engagement-style STYLE_YOU_CHOSE --language DETECTED_LANGUAGE --urns 'ALL_POST_URNS'
   log_post.py validates the URL, canonicalizes our_url to the
   /feed/update/urn:li:activity:<id>/ form, enforces status='active',
   and stores ALL_POST_URNS (comma- or whitespace-separated 16-19 digit
   IDs from step 5b.1.1) into posts.urns so the next run's
   --check-engaged-ids dedup catches the post under any URN form.
   --urns is required for LinkedIn; pass at minimum the activityId.
   Duplicate prevention is the responsibility of step 3, NOT this step.

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
