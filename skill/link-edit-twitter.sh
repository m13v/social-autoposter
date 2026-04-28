#!/usr/bin/env bash
# link-edit-twitter.sh — Post a follow-up self-reply with a project link on
# Twitter posts whose inline self-reply (run-twitter-cycle.sh step 6) never
# completed. Mirrors link-edit-reddit.sh but uses a self-reply (new tweet)
# instead of editing the original.
#
# Called by launchd (com.m13v.social-link-edit-twitter).
# Posts stay eligible until we either succeed (link_edited_at set) or
# explicitly skip (link_edit_content='SKIPPED: ...').

set -uo pipefail

source "$(dirname "$0")/lock.sh"
acquire_lock "twitter-browser" 3600
acquire_lock "link-edit-twitter" 5400

# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/link-edit-twitter-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== Twitter Link Edit Run: $(date) ==="

# Orphan posts: primary reply landed, but no self-reply recorded.
# We only sweep recent posts (14d) with real engagement so we don't spam
# old or dead threads.
EDITABLE=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(q) FROM (
        SELECT id, our_url, our_content, thread_url, thread_author,
               thread_title, upvotes, project_name
        FROM posts
        WHERE status='active'
          AND platform='twitter'
          AND posted_at > NOW() - INTERVAL '14 days'
          AND posted_at < NOW() - INTERVAL '30 minutes'
          AND link_edited_at IS NULL
          AND our_url IS NOT NULL
          AND our_url LIKE 'https://x.com/%'
          AND COALESCE(upvotes, 0) >= 3
        ORDER BY COALESCE(upvotes, 0) DESC, posted_at DESC
        LIMIT 15
    ) q;" 2>/dev/null || echo "")

if [ "$EDITABLE" = "null" ] || [ -z "$EDITABLE" ]; then
    log "No Twitter posts eligible for link follow-up"
    python3 "$REPO_DIR/scripts/log_run.py" --script "link_edit_twitter" \
        --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed $(( $(date +%s) - RUN_START ))
    exit 0
fi

EDITABLE_COUNT=$(echo "$EDITABLE" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
log "Twitter: $EDITABLE_COUNT posts eligible for link follow-up"

ALL_PROJECTS_JSON=$(python3 -c "
import json, os
config = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
print(json.dumps({p['name']: p for p in config.get('projects', [])}, indent=2))
" 2>/dev/null || echo "{}")

source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block twitter posting)

PROMPT_FILE=$(mktemp)
cat > "$PROMPT_FILE" <<PROMPT_EOF
You are the Social Autoposter Twitter link follow-up bot.

Read $SKILL_FILE for the full workflow. Your job: for each listed post that
has NO follow-up self-reply yet, post one short self-reply to our own tweet
that includes the matched project's URL. This is the sweep backstop for the
inline self-reply step in run-twitter-cycle.sh.

CRITICAL: ALL browser calls MUST use mcp__twitter-agent__* tools. NEVER use
mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
Posting MUST use twitter_browser.py (CDP), not the MCP browser tools. If a
twitter-agent call is blocked or times out, wait 30s and retry (up to 3
times). If still blocked, skip that post.

CRITICAL: This is a single-shot run. NEVER call ScheduleWakeup, CronCreate,
CronDelete, CronList, EnterPlanMode, EnterWorktree, or any deferred-execution /
scheduling tool. You MUST complete or skip every post in this one run; do not
defer work to "a future run". If you hit a hard block, mark the post SKIPPED
via step 6 and move on to the next post.

Twitter posts eligible for link follow-up:
$EDITABLE

All project configs:
$ALL_PROJECTS_JSON

$STYLES_BLOCK

For each post:
1. Decide which project applies.
   a. If project_name is set AND its topics/description fit the thread, use it.
   b. If project_name is set but CLEARLY does not fit, pick a better project
      from the config and run:
        psql "\$DATABASE_URL" -c "UPDATE posts SET project_name='BETTER_PROJECT' WHERE id=POST_ID"
   c. If no project fits at all, mark skipped (step 6) and continue.
2. Decide PROJECT_URL for this post.
   a. If the matched project has a landing_pages config (with repo, base_url),
      generate a fresh SEO page for this thread by delegating to the unified
      generator:
        i. Decide a SHORT keyword phrase (3-6 words) that captures what page
           would help this thread's audience. Think SEO intent, not headline
           copy. Examples: "local ai agent", "macos accessibility automation",
           "self hosted llm inference".
       ii. Derive a URL slug from the keyword: lowercase, kebab-case,
           alphanumeric and hyphens only, max 50 chars. Examples:
           "local-ai-agent", "macos-accessibility-automation".
      iii. Run the unified SEO page generator (it loads the @m13v/seo-components
           palette, picks content type, builds the page, commits, pushes,
           verifies the live URL, and writes the seo_keywords row that surfaces
           in the dashboard activity feed). Use the Bash tool:
                python3 $REPO_DIR/seo/generate_page.py --product PROJECT_NAME --keyword "KEYWORD_PHRASE" --slug "url-slug" --trigger twitter
           This call can take 10-20 minutes per page. The final stdout is a
           JSON object; parse it. On success it contains "success": true and
           "page_url": "https://...". On failure it contains "success": false
           and "error": "...".
       iv. On success, set PROJECT_URL = page_url from the JSON output.
        v. On failure, DO NOT fall back to a bare project website URL. DO NOT
           post the self-reply. DO NOT update link_edited_at on the post. Log
           the error in your output and move to the next post. The post will
           stay eligible and be retried on the next scheduled run (every 6h).
           A custom landing page per thread is a hard requirement when
           landing_pages is configured; a bare homepage link is never
           acceptable in that case.
   b. If the matched project has NO landing_pages config at all (not a
      generation failure, genuinely unconfigured), use the project's plain
      website URL as PROJECT_URL.
   c. If the project has no URL of any kind, mark skipped with
      SKIPPED: no_project_url (step 6) and continue.
3. Draft FOLLOW_UP_TEXT: 1 short casual sentence, lowercase, no hard sell,
   no em dashes. Match the parent tweet's language. Do NOT include the URL
   in FOLLOW_UP_TEXT; the tool appends it automatically and will reject
   anything that starts with http/https.
   ALWAYS frame as our own creation, never as a third-party tool we just
   discovered. We built / made / shipped this. Good: "built a tool for this",
   "made this for exactly that case", "shipped a small thing that handles it".
   NEVER write: "I found this", "there's a tool", "came across this",
   "saw this manual", "found this guide". That phrasing pretends we are a
   neutral commenter pointing at someone else's project. We are the authors.
4. Post the self-reply via the CDP script:
     python3 $REPO_DIR/scripts/twitter_browser.py self-reply \\
       "POST_OUR_URL" "FOLLOW_UP_TEXT" "PROJECT_URL"
   Parse the JSON output. On success it returns reply_url and final_text.
   If reply_url is missing/invalid/doesn't start with https://x.com/m13v_/status/,
   treat as FAILED: do NOT record, do NOT mark. Leave link_edited_at NULL so
   the next sweep retries.
5. On success, record it so the post drops out of the sweep:
     python3 $REPO_DIR/scripts/log_post.py --mark-self-reply \\
       --post-id POST_ID \\
       --self-reply-url REPLY_URL \\
       --self-reply-content 'FINAL_TEXT'
6. On SKIP (no project match / no project URL / comment removed), mark so
   it won't be retried:
     psql "\$DATABASE_URL" -c "UPDATE posts SET link_edited_at=NOW(), link_edit_content='SKIPPED: REASON' WHERE id=POST_ID"

COMMITMENT GUARDRAILS (never violate):
- NEVER suggest, offer, or agree to calls, meetings, demos, or video chats.
- NEVER promise to share links, files, or resources you don't have.
  Only share URLs from config.json projects.
- NEVER offer to DM or send anything outside the reply.
- NEVER make time-bound promises.
PROMPT_EOF

gtimeout 5400 "$REPO_DIR/scripts/run_claude.sh" "link-edit-twitter" \
    --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/twitter-agent-mcp.json" \
    --disallowed-tools "ScheduleWakeup,CronCreate,CronDelete,CronList,EnterPlanMode,EnterWorktree" \
    -p "$(cat "$PROMPT_FILE")" 2>&1 | tee -a "$LOG_FILE" \
    || log "WARNING: Twitter link-edit claude exited with code $?"
rm -f "$PROMPT_FILE"

EDITED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE platform='twitter' AND link_edited_at IS NOT NULL;" 2>/dev/null || echo "0")
log "Twitter link-edit complete. Total twitter posts with link follow-up (all-time): $EDITED"

RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
python3 "$REPO_DIR/scripts/log_run.py" --script "link_edit_twitter" \
    --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed "$RUN_ELAPSED"

find "$LOG_DIR" -name "link-edit-twitter-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== Twitter link-edit complete: $(date) ==="
