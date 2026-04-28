#!/usr/bin/env bash
# engage-linkedin.sh — LinkedIn engagement loop
# Phase A: Discover replies/mentions from LinkedIn notifications (Claude-driven MCP).
# Phase B: Respond to pending LinkedIn replies (Claude-driven, OAuth API for posting).
# Called by launchd every 3 hours.
#
# IMPORTANT: all LinkedIn browser work goes through the linkedin-agent MCP, driven
# by Claude (the LLM). Do NOT re-introduce Python Playwright scrapers, Voyager API
# calls (/voyager/api/*), comment-page scroll+expand loops, or programmatic
# re-login flows. See CLAUDE.md "LinkedIn: flagged patterns to avoid" for why.

set -euo pipefail

# Browser-profile lock first (shared with other linkedin pipelines), then pipeline lock.
source "$(dirname "$0")/lock.sh"
acquire_lock "linkedin-browser" 3600
acquire_lock "linkedin" 3600

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
BATCH_SIZE=500
MCP_CONFIG="$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/engage-linkedin-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== LinkedIn Engagement Run: $(date) ==="

# Load exclusions from config
EXCLUDED_AUTHORS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('authors',[])))" 2>/dev/null || echo "")
EXCLUDED_LINKEDIN=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('linkedin_profiles',[])))" 2>/dev/null || echo "")

# ═══════════════════════════════════════════════════════
# PHASE A: Discover new replies/mentions from LinkedIn notifications
# Claude-driven: LLM navigates linkedin-agent MCP to /notifications/, extracts
# actionable items from the notifications page DOM (NOT from Voyager API,
# NOT by opening each permalink).
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning LinkedIn notifications (Claude-driven)..."

PHASE_A_PROMPT=$(mktemp)
cat > "$PHASE_A_PROMPT" <<PROMPT_EOF
You are the Social Autoposter LinkedIn discovery bot.

Read $SKILL_FILE for content rules (tone, anti-AI detection, no em dashes).

## Task: Discover new LinkedIn replies and mentions from the notifications page

CRITICAL - Browser agent rule: ONLY use mcp__linkedin-agent__* tools (e.g. mcp__linkedin-agent__browser_navigate, mcp__linkedin-agent__browser_snapshot, mcp__linkedin-agent__browser_run_code). NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
CRITICAL: If a browser agent tool call is blocked or times out, wait 30 seconds and retry the same agent. Repeat up to 3 times. If still blocked, stop.
CRITICAL: Do NOT open individual post permalinks to fetch comment text. Everything we need is on the notifications page. Opening per-comment permalinks is a flagged scraping pattern.
CRITICAL: Do NOT call any /voyager/api/ endpoint, do NOT fetch() from the linkedin.com session. Use only UI navigation (browser_navigate, browser_snapshot, browser_run_code).

EXCLUSIONS - do NOT engage with these accounts:
- Excluded authors: $EXCLUDED_AUTHORS
- Excluded LinkedIn profiles: $EXCLUDED_LINKEDIN
- Skip comments by "Matthew Diakonov" or "m13v" (our own account).

### Step 1: Load existing reply comment IDs (dedup)
\`\`\`bash
source ~/social-autoposter/.env
psql "\$DATABASE_URL" -t -A -c "SELECT their_comment_id FROM replies WHERE platform='linkedin';"
\`\`\`
Skip any notification whose comment URN is already in this list.

### Step 2: Load author+post pairs we already engaged with
\`\`\`bash
psql "\$DATABASE_URL" -t -A -c "SELECT DISTINCT r.their_author || '|||' || p.our_url FROM replies r JOIN posts p ON r.post_id = p.id WHERE r.platform='linkedin' AND r.status IN ('replied','pending','processing');"
\`\`\`
Skip any notification whose (author, post) pair is already here. One reply per author per thread.

### Step 3: Load our LinkedIn posts for matching
\`\`\`bash
psql "\$DATABASE_URL" -t -A -c "SELECT id, our_url FROM posts WHERE platform='linkedin' AND status='active';"
\`\`\`

### Step 4: Navigate to LinkedIn notifications and verify session
Use mcp__linkedin-agent__browser_navigate to go to https://www.linkedin.com/notifications/

Take a browser_snapshot and verify the page is the notifications feed (not a login/checkpoint page). If you see login, captcha, or a verification challenge, STOP immediately and print: SESSION_INVALID — do not attempt to log in. Exit.

### Step 5: Load more notifications
Scroll the page down a few times to lazy-load notifications. If a "Show more results" button is visible, click it — up to 5 times total, with a pause of 2-3 seconds between clicks. Stop if the button disappears.

### Step 6: Extract actionable notifications from the notifications page DOM
Use mcp__linkedin-agent__browser_run_code with this JS (single evaluate — do NOT navigate to any other URL):

\`\`\`javascript
async (page) => {
  const actionable = [];
  const actionablePhrases = [
    'replied to your comment',
    'mentioned you in a comment',
    'mentioned you in this',
    'commented on your post',
    'commented on your update',
  ];

  for (const article of document.querySelectorAll('article')) {
    const text = (article.innerText || '').toLowerCase();
    const matched = actionablePhrases.find(p => text.includes(p));
    if (!matched) continue;

    const strong = article.querySelector('strong');
    const author = strong ? strong.textContent.trim() : 'unknown';

    const link = article.querySelector('a[href*="commentUrn"]') ||
                 article.querySelector('a[href*="replyUrn"]') ||
                 article.querySelector('a[href*="feed/update"]');
    const href = link ? link.getAttribute('href') : null;
    if (!href) continue;

    // Extract activity ID and commentUrn from the href
    const activityMatch = href.match(/urn:li:activity:(\d+)/);
    const activityId = activityMatch ? activityMatch[1] : null;
    const commentUrnMatch = href.match(/commentUrn=([^&]+)/);
    const commentUrn = commentUrnMatch ? decodeURIComponent(commentUrnMatch[1]) : null;

    // Best-effort snippet: text inside the article, minus the author header
    const snippet = (article.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 500);

    actionable.push({
      type: matched,
      author,
      href: href.startsWith('http') ? href : ('https://www.linkedin.com' + href),
      activity_id: activityId,
      comment_urn: commentUrn,
      snippet,
    });
  }
  return JSON.stringify(actionable);
}
\`\`\`

### Step 7: For each extracted notification, decide whether to insert
For each item:
- If comment_urn is null OR activity_id is null: skip (no_comment_urn)
- If comment_urn is in the Step 1 dedup list: skip (already_tracked)
- If author matches an excluded account or is our own: skip (excluded_author / own_account)
- Build author_post_key = author + '|||' + our_url-for-this-post. If this pair is in the Step 2 list: skip (author_already_engaged)
- Find matching post_id from Step 3 by activity_id in the our_url. If none: create one via INSERT (use PROJECT_NAME matched from config.json projects[].topics against the post topic, thread_url = https://www.linkedin.com/feed/update/urn:li:activity:\$ACTIVITY_ID/, our_url same).

Insert the reply:
\`\`\`bash
psql "\$DATABASE_URL" -c "INSERT INTO replies (post_id, platform, their_comment_id, their_author, their_content, their_comment_url, depth, status) VALUES (POST_ID, 'linkedin', 'COMMENT_URN', 'AUTHOR', 'SNIPPET', 'HREF', 1, 'pending');"
\`\`\`

### Step 8: Summary
Print:
- N new replies discovered
- N already tracked
- N author already engaged on thread
- N excluded
- N own account
- N no comment URN

PROMPT_EOF

gtimeout 1800 "$REPO_DIR/scripts/run_claude.sh" "engage-linkedin-phaseA" --strict-mcp-config --mcp-config "$MCP_CONFIG" -p "$(cat "$PHASE_A_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase A claude exited with code $?"
rm -f "$PHASE_A_PROMPT"

# ═══════════════════════════════════════════════════════
# PHASE B: Respond to pending LinkedIn replies
# Claude-driven. Posts via OAuth API (api.linkedin.com/v2/socialActions) by
# default (documented, authorized integration). Falls back to linkedin-agent
# MCP browser click-through if API errors.
# ═══════════════════════════════════════════════════════

# Reset any 'processing' items older than 2 hours back to 'pending'
RESET_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    UPDATE replies SET status='pending'
    WHERE platform='linkedin' AND status='processing' AND processing_at < NOW() - INTERVAL '2 hours'
    RETURNING id;" | wc -l | tr -d ' ')
[ "$RESET_COUNT" -gt 0 ] && log "Phase B: Reset $RESET_COUNT stuck 'processing' LinkedIn items back to pending"

PENDING_COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='linkedin' AND status='pending';")

if [ "$PENDING_COUNT" -eq 0 ]; then
    log "Phase B: No pending LinkedIn replies. Done!"
else
    log "Phase B: $PENDING_COUNT pending LinkedIn replies to process"

    PENDING_DATA=$(psql "$DATABASE_URL" -t -A -c "
        SELECT json_agg(q) FROM (
            SELECT r.id, r.platform, r.their_author,
                   LEFT(r.their_content, 300) as their_content,
                   r.their_comment_url, r.their_comment_id, r.depth,
                   LEFT(p.thread_title, 100) as thread_title,
                   p.thread_url, LEFT(p.our_content, 200) as our_content, p.our_url,
                   CASE WHEN p.thread_url = p.our_url THEN 1 ELSE 0 END as is_our_original_post,
                   p.project_name
            FROM replies r
            JOIN posts p ON r.post_id = p.id
            WHERE r.platform='linkedin' AND r.status='pending'
            ORDER BY
                CASE WHEN p.thread_url = p.our_url THEN 0 ELSE 1 END,
                r.discovered_at ASC
            LIMIT $BATCH_SIZE
        ) q;")

    # Per-project voice map (so each reply can be drafted in the matched project's voice)
    PROJECTS_VOICE_JSON=$(python3 -c "
import json
c = json.load(open('$REPO_DIR/config.json'))
print(json.dumps({p['name']: p.get('voice', {}) for p in c.get('projects', []) if p.get('voice')}, indent=2))
" 2>/dev/null || echo "{}")

    # Index of our recent active posts: activity_id -> {project, our_content, posted_at}.
    # Used by the engage step to resolve the *real* parent post when navigating
    # the thread, since the scanner's project_name may be best-effort.
    OUR_POSTS_INDEX=$(psql "$DATABASE_URL" -t -A -c "
        SELECT COALESCE(json_object_agg(
            (regexp_match(our_url, 'urn:li:activity:([0-9]+)'))[1],
            json_build_object(
                'project', project_name,
                'our_content', LEFT(our_content, 500),
                'thread_url', thread_url,
                'posted_at', posted_at
            )
        ), '{}'::json)
        FROM posts
        WHERE platform='linkedin' AND status='active'
          AND our_url ~ 'urn:li:activity:[0-9]+'
          AND posted_at > NOW() - INTERVAL '30 days'
    " 2>/dev/null || echo "{}")

    # Generate engagement style and content rules from shared module
    source "$REPO_DIR/skill/styles.sh"
    STYLES_BLOCK=$(generate_styles_block linkedin replying)

    # Top performers feedback report (platform-wide)
    TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform linkedin 2>/dev/null || echo "(top performers report unavailable)")

    PHASE_B_PROMPT=$(mktemp)
    cat > "$PHASE_B_PROMPT" <<PROMPT_EOF
You are the Social Autoposter LinkedIn engagement bot.

Read $SKILL_FILE for the full workflow, content rules, and platform details.

EXCLUSIONS - do NOT engage with these accounts (skip and mark as 'skipped' with reason 'excluded_author'):
- Excluded authors: $EXCLUDED_AUTHORS
- Excluded LinkedIn profiles: $EXCLUDED_LINKEDIN

CRITICAL - Browser agent rule: ONLY use mcp__linkedin-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
CRITICAL: If a browser agent tool call is blocked or times out, DO NOT fall back to any other browser tool. Wait 30 seconds and retry the same agent. Repeat up to 3 times. If still blocked, skip that item and move on.
CRITICAL: Do NOT call /voyager/api/ endpoints. Posting goes through linkedin_api.py (OAuth api.linkedin.com). Browser is the fallback only.

## Respond to pending LinkedIn replies ($PENDING_COUNT total)

### Priority order:
1. **Replies on our original posts** (is_our_original_post=1) - highest priority
2. **Direct questions** ("what tool", "how do you", "can you share")
3. **Everything else** - general engagement

### Tiered link strategy:
- **Tier 1 (default):** No link. Genuine engagement, expand topic.
- **Tier 2 (natural mention):** Conversation touches a topic matching a project in config. Recommend it casually as a tool you've come across.
- **Tier 3 (direct ask):** They ask for link/tool/source. Give it immediately.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better replies):
$TOP_REPORT

$STYLES_BLOCK

## Per-project voice map
For each reply you draft, look up the matched project's voice block below and apply it: follow \`voice.tone\`, never violate any item in \`voice.never\`, mirror \`voice.examples\` / \`voice.examples_good\` when present.
$PROJECTS_VOICE_JSON

## Our recent active posts (activity_id -> {project, our_content, thread_url, posted_at})
Each pending row's \`project_name\` is a best-effort guess. After navigating the thread (Step 2 below), use this index to resolve the *real* parent post and override the project before drafting. Match by activity_id extracted from the page URL or comment URN.
$OUR_POSTS_INDEX

Here are the replies to process:
$PENDING_DATA

CRITICAL: Reply in the SAME LANGUAGE as the message you are responding to. Match the language exactly.
CRITICAL: Process EVERY reply. For each: either post a response and mark as 'replied', OR mark as 'skipped' with a skip_reason.

CRITICAL: For ALL database operations, use the reply_db.py helper (NOT raw psql):
  python3 $REPO_DIR/scripts/reply_db.py processing ID          # BEFORE posting
  python3 $REPO_DIR/scripts/reply_db.py replied ID "reply text" [url] [engagement_style] [is_recommendation]   # AFTER posting. engagement_style is TONE (critic, storyteller, etc). Pass "1" for is_recommendation ONLY when the reply casually recommends a project (Tier 2/3); leave blank otherwise.
  python3 $REPO_DIR/scripts/reply_db.py skipped ID "reason"
  python3 $REPO_DIR/scripts/reply_db.py skip_batch '{"ids":[1,2,3],"reason":"..."}'
  python3 $REPO_DIR/scripts/reply_db.py status
NEVER use psql directly for reply status updates.

### Project tracking on replies
When you recommend a project in a reply (Tier 2 or Tier 3), set project_name on the reply:
  source ~/social-autoposter/.env
  psql "\$DATABASE_URL" -c "UPDATE replies SET project_name='PROJECT_NAME' WHERE id=REPLY_ID;"

MANDATORY reply flow for every item:
  Step 1: python3 reply_db.py processing ID      <- mark BEFORE posting
  Step 2: NAVIGATE TO THE THREAD AND READ CONTEXT (mandatory, do NOT skip).
          Do NOT draft a reply from the notification snippet alone — the snippet
          is truncated and lacks the parent post content + sibling replies.
          a) mcp__linkedin-agent__browser_navigate to their_comment_url
          b) mcp__linkedin-agent__browser_snapshot (depth 8) to read:
             - the FULL parent post text (our original post if this is on our thread)
             - the immediate ancestor of their_comment_id
             - sibling replies (so you don't repeat what someone else already said)
          c) Extract the activity_id from the URL or comment URN. Look it up in
             OUR_POSTS_INDEX above. If found, OVERRIDE the project_name on this
             reply row to the indexed project (the scan-time guess is unreliable):
               source ~/social-autoposter/.env
               psql "\$DATABASE_URL" -c "UPDATE replies SET project_name='RESOLVED_PROJECT' WHERE id=REPLY_ID;"
             Then use that project's voice from PROJECTS_VOICE_JSON for drafting.
             If unmatched, keep whatever the row already has and follow global rules.
  Step 3: Draft the reply using the resolved project's voice + chosen engagement
          style. Professional but casual. NEVER em dashes. Match parent post language.
  Step 4: post reply (OAuth API first, browser fallback)
  Step 5: python3 reply_db.py replied ID "text" [url] [engagement_style] [is_recommendation]   <- mark AFTER success. engagement_style is TONE; pass is_recommendation="1" only when you mentioned a project (Tier 2/3).
If Step 5 fails, the item stays 'processing' and will be reset to 'pending' on the next run.

For LinkedIn replies - use the OAuth API first:
1. Extract the activity ID from their_comment_url or their_comment_id.
   - From their_comment_id like \`urn:li:comment:(activity:7438226125077549056,7438815640536170496)\`, the activity ID is \`7438226125077549056\` and the full URN is the parent_comment_urn.
   - From their_comment_url, extract the activity ID from the URL path.
2. Post the reply via API:
   \`\`\`bash
   python3 $REPO_DIR/scripts/linkedin_api.py reply ACTIVITY_ID "PARENT_COMMENT_URN" "YOUR REPLY TEXT"
   \`\`\`
   This returns JSON with {ok, reply_urn, permalink}. Use permalink as the reply URL.
3. If the API call fails (e.g., token expired, comment deleted), fall back to the linkedin-agent browser:
   - Navigate to their_comment_url via mcp__linkedin-agent__browser_navigate
   - browser_snapshot to find the comment, click Reply, type, submit
   - Do NOT aggressively scroll-and-expand comments; if the comment isn't visible after a normal scroll, mark as 'skipped' with reason 'comment_not_found'
4. If both API and browser fail, mark as 'skipped' with reason 'comment_not_found'.

After every 10 replies, run: python3 $REPO_DIR/scripts/reply_db.py status
PROMPT_EOF

    gtimeout 5400 "$REPO_DIR/scripts/run_claude.sh" "engage-linkedin-phaseB" --strict-mcp-config --mcp-config "$MCP_CONFIG" -p "$(cat "$PHASE_B_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase B claude exited with code $?"
    rm -f "$PHASE_B_PROMPT"
fi

# ═══════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════
TOTAL_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='linkedin' AND status='pending';")
TOTAL_REPLIED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='linkedin' AND status='replied';")
TOTAL_SKIPPED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='linkedin' AND status='skipped';")

log "LinkedIn summary: pending=$TOTAL_PENDING replied=$TOTAL_REPLIED skipped=$TOTAL_SKIPPED"

# Log run to persistent monitor
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
python3 "$REPO_DIR/scripts/log_run.py" --script "engage_linkedin" --posted "$TOTAL_REPLIED" --skipped "$TOTAL_SKIPPED" --failed 0 --cost 0 --elapsed "$RUN_ELAPSED"

# Delete old logs
find "$LOG_DIR" -name "engage-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== LinkedIn engagement complete: $(date) ==="
