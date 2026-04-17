#!/usr/bin/env bash
# audit.sh — Full post audit pipeline:
#   Step 1: API audit (Reddit + Moltbook) via Python
#   Step 2: X/Twitter audit via Claude + Playwright (browser required)
#   Step 3: LinkedIn audit via Claude + Playwright (browser required)
#   Step 4: Mark deleted/removed posts
#   Step 5: Report summary
# Called by launchd every 24 hours.


set -uo pipefail

# Audit lock: wait up to 60min for previous audit run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "audit" 3600

# Load secrets
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
LOG_FILE="$LOG_DIR/audit-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" >> "$LOG_FILE"; echo "[$(date +%H:%M:%S)] $*"; }

RUN_START=$(date +%s)
log "=== Audit Pipeline Run: $(date) ==="

# ═══════════════════════════════════════════════════════
# STEP 1: API audit (Reddit + Moltbook)
# ═══════════════════════════════════════════════════════
log "Step 1: API audit (Python — checks deleted/removed + updates stats)"
python3 "$REPO_DIR/scripts/update_stats.py" >> "$LOG_FILE" 2>&1
STEP1_EXIT=$?
if [ "$STEP1_EXIT" -ne 0 ]; then
    log "Step 1: FAILED (exit $STEP1_EXIT) — continuing to Step 2"
else
    log "Step 1: Done"
fi

# ═══════════════════════════════════════════════════════
# STEP 2: X/Twitter audit (API via fxtwitter — no browser needed)
# ═══════════════════════════════════════════════════════
TWITTER_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='twitter' AND status='active' AND our_url IS NOT NULL;" 2>/dev/null || echo "0")

if [ "$TWITTER_COUNT" -gt 0 ]; then
    log "Step 2: X/Twitter audit — $TWITTER_COUNT active tweets (API via fxtwitter)"
    python3 "$REPO_DIR/scripts/update_stats.py" --twitter-audit >> "$LOG_FILE" 2>&1
    STEP2_EXIT=$?
    if [ "$STEP2_EXIT" -ne 0 ]; then
        log "Step 2: FAILED (exit $STEP2_EXIT)"
    else
        log "Step 2: Done"
    fi
else
    log "Step 2: SKIPPED — no active Twitter posts to audit"
fi

# ═══════════════════════════════════════════════════════
# STEP 3: LinkedIn audit (Claude-driven via linkedin-agent MCP)
# Small batch, one-post-at-a-time, no /voyager/api/, no scripted bulk scrape.
# See CLAUDE.md "LinkedIn: flagged patterns to avoid" for why.
# ═══════════════════════════════════════════════════════
LINKEDIN_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
      AND our_url LIKE '%linkedin.com/feed/update/%';" 2>/dev/null || echo "0")

# Prefer posts that haven't been checked recently, cap at 15 per run to stay under the anti-bot radar
LINKEDIN_BATCH_LIMIT=15
if [ "$LINKEDIN_COUNT" -gt 0 ]; then
    LINKEDIN_BATCH=$(psql "$DATABASE_URL" -t -A -c "
        SELECT json_agg(q) FROM (
            SELECT id, our_url as url
            FROM posts
            WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
              AND our_url LIKE '%linkedin.com/feed/update/%'
            ORDER BY status_checked_at NULLS FIRST, id
            LIMIT $LINKEDIN_BATCH_LIMIT
        ) q;" 2>/dev/null)

    log "Step 3: LinkedIn audit (Claude-driven), batch up to $LINKEDIN_BATCH_LIMIT posts"

    LINKEDIN_AUDIT_PROMPT=$(mktemp)
    MCP_CONFIG_AUDIT="$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json"
    cat > "$LINKEDIN_AUDIT_PROMPT" <<PROMPT_EOF
You are the Social Autoposter LinkedIn audit bot.

CRITICAL - Browser agent rule: ONLY use mcp__linkedin-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
CRITICAL: Do NOT call /voyager/api/ endpoints, do NOT fetch() against linkedin.com. Use only UI navigation (browser_navigate, browser_snapshot, browser_run_code).
CRITICAL: If a tool call is blocked, times out, or you see a login/captcha/checkpoint, STOP immediately and print SESSION_INVALID. Do not re-login.

## Task: audit the engagement counts and status of these LinkedIn posts

Posts to audit:
$LINKEDIN_BATCH

For EACH post (one at a time, with a 3-5 second pause between posts):

1. mcp__linkedin-agent__browser_navigate to the post URL.
2. Wait for the page to load, then browser_snapshot once.
3. Determine status:
   - If the page shows "This post is no longer available", "content unavailable", or a 404, status = "deleted".
   - Otherwise status = "active" and extract engagement numbers from the DOM (reactions, comments, views/impressions). Best-effort integers, 0 if not visible.
4. Update the DB via psql (one UPDATE per post, not a batch fetch):
   \`\`\`bash
   source ~/social-autoposter/.env
   # For active posts:
   psql "\$DATABASE_URL" -c "UPDATE posts SET upvotes=REACTIONS, comments_count=COMMENTS, views=VIEWS, engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=POST_ID;"
   # For deleted posts:
   psql "\$DATABASE_URL" -c "UPDATE posts SET status='deleted', status_checked_at=NOW() WHERE id=POST_ID;"
   \`\`\`
5. Do NOT open comment threads, do NOT expand "See more" repeatedly, do NOT scroll hunt for every reaction. Read what's visible after the first snapshot and move on.

When done, print a one-line summary: N checked, N deleted, N errors.
PROMPT_EOF

    if [ "$LINKEDIN_BATCH" != "null" ] && [ -n "$LINKEDIN_BATCH" ]; then
        gtimeout 1800 claude --strict-mcp-config --mcp-config "$MCP_CONFIG_AUDIT" -p "$(cat "$LINKEDIN_AUDIT_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Step 3 claude exited with code $?"
    else
        log "Step 3: nothing to audit this cycle"
    fi
    rm -f "$LINKEDIN_AUDIT_PROMPT"
else
    log "Step 3: SKIPPED (no active LinkedIn posts to audit)"
fi

# ═══════════════════════════════════════════════════════
# STEP 4: Orphan / stale post detection
# ═══════════════════════════════════════════════════════
log "Step 4: Orphan/stale detection"

ORPHAN_REPORT=$(psql "$DATABASE_URL" -t -A -c "
    SELECT platform, status, COUNT(*)
    FROM posts
    WHERE status NOT IN ('active', 'deleted', 'removed')
    GROUP BY platform, status
    ORDER BY platform, status;" 2>/dev/null || echo "")

BROKEN_URL_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*)
    FROM posts
    WHERE status = 'active'
      AND (our_url IS NULL OR our_url = '' OR our_url NOT LIKE 'http%');" 2>/dev/null || echo "0")

if [ -n "$ORPHAN_REPORT" ]; then
    log "WARNING: Posts with non-standard status:"
    echo "$ORPHAN_REPORT" | while IFS='|' read -r plat stat cnt; do
        log "  $plat $stat: $cnt"
    done
fi
if [ "$BROKEN_URL_COUNT" -gt 0 ]; then
    log "WARNING: $BROKEN_URL_COUNT active posts with missing/invalid our_url"
fi
if [ -z "$ORPHAN_REPORT" ] && [ "$BROKEN_URL_COUNT" = "0" ]; then
    log "Step 4: Clean (no orphans, no broken URLs)"
fi

# ═══════════════════════════════════════════════════════
# STEP 5: Report summary
# ═══════════════════════════════════════════════════════
log "Step 5: Summary"

ACTIVE=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='active';" 2>/dev/null || echo "?")
DELETED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='deleted';" 2>/dev/null || echo "?")
REMOVED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='removed';" 2>/dev/null || echo "?")

log "Post status: active=$ACTIVE deleted=$DELETED removed=$REMOVED"

# Log run to persistent monitor
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
AUDIT_FAILED=$(( (STEP1_EXIT != 0 ? 1 : 0) + (${STEP2_EXIT:-0} != 0 ? 1 : 0) ))
python3 "$REPO_DIR/scripts/log_run.py" --script "audit" --posted "$ACTIVE" --skipped 0 --failed "$AUDIT_FAILED" --cost 0 --elapsed "$RUN_ELAPSED"

log "=== Audit Pipeline complete: $(date) ==="

# Clean up old logs (keep last 14 days)
find "$LOG_DIR" -name "audit-*.log" -mtime +14 -delete 2>/dev/null || true
