#!/usr/bin/env bash
# audit.sh — Post audit pipeline.
#
# Per-platform mode (preferred, driven by launchd via per-platform wrappers):
#   --platform reddit    Reddit API audit via update_stats.py --reddit-only
#   --platform moltbook  Moltbook API audit via update_stats.py --moltbook-only
#   --platform twitter   Twitter API audit via update_stats.py --twitter-audit
#   --platform linkedin  LinkedIn CDP audit (python scripts/linkedin_browser.py)
#
# Every run also executes the orphan/summary step at the end (DB-only, cheap).
# With no --platform, runs all four sequentially (legacy manual path).


set -uo pipefail

# Parse args.
PLATFORM=""
while [ $# -gt 0 ]; do
    case "$1" in
        --platform)    PLATFORM="${2:-}"; shift 2 ;;
        --platform=*)  PLATFORM="${1#--platform=}"; shift ;;
        *)             shift ;;
    esac
done

case "$PLATFORM" in
    ""|reddit|twitter|linkedin|moltbook) ;;
    *)
        echo "audit.sh: invalid --platform '$PLATFORM' (expected reddit, twitter, linkedin, or moltbook)" >&2
        exit 2
        ;;
esac

# Per-platform lock name so all four can run concurrently, but a second
# invocation of the same platform waits. Legacy no-platform run keeps the
# original "audit" lock name.
LOCK_NAME="audit${PLATFORM:+-$PLATFORM}"

source "$(dirname "$0")/lock.sh"
acquire_lock "$LOCK_NAME" 3600

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
LOG_TAG="${PLATFORM:-all}"
LOG_FILE="$LOG_DIR/audit-${LOG_TAG}-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" >> "$LOG_FILE"; echo "[$(date +%H:%M:%S)] $*"; }

RUN_START=$(date +%s)
log "=== Audit Pipeline Run (${LOG_TAG}): $(date) ==="

# Decide which steps run for this invocation.
if [ -z "$PLATFORM" ]; then
    RUN_REDDIT=1; RUN_MOLTBOOK=1; RUN_TWITTER=1; RUN_LINKEDIN=1
else
    RUN_REDDIT=0; RUN_MOLTBOOK=0; RUN_TWITTER=0; RUN_LINKEDIN=0
    case "$PLATFORM" in
        reddit)   RUN_REDDIT=1 ;;
        moltbook) RUN_MOLTBOOK=1 ;;
        twitter)  RUN_TWITTER=1 ;;
        linkedin) RUN_LINKEDIN=1 ;;
    esac
fi

STEP1_EXIT=0
STEP2_EXIT=0
STEP3_EXIT=0

# ═══════════════════════════════════════════════════════
# Reddit API audit
# ═══════════════════════════════════════════════════════
if [ "$RUN_REDDIT" -eq 1 ]; then
    log "Reddit: API audit (update_stats.py --reddit-only)"
    if [ -z "$PLATFORM" ]; then
        # Legacy all-platform path uses the combined default pass which also
        # covers Moltbook + Twitter, so we don't duplicate them below.
        python3 "$REPO_DIR/scripts/update_stats.py" >> "$LOG_FILE" 2>&1
    else
        python3 "$REPO_DIR/scripts/update_stats.py" --reddit-only >> "$LOG_FILE" 2>&1
    fi
    STEP1_EXIT=$?
    if [ "$STEP1_EXIT" -ne 0 ]; then
        log "Reddit: FAILED (exit $STEP1_EXIT)"
    else
        log "Reddit: Done"
    fi
fi

# ═══════════════════════════════════════════════════════
# Moltbook API audit
# ═══════════════════════════════════════════════════════
# Skip in legacy mode — already covered by the combined pass above.
if [ "$RUN_MOLTBOOK" -eq 1 ] && [ -n "$PLATFORM" ]; then
    log "Moltbook: API audit (update_stats.py --moltbook-only)"
    python3 "$REPO_DIR/scripts/update_stats.py" --moltbook-only >> "$LOG_FILE" 2>&1
    MOLTBOOK_EXIT=$?
    if [ "$MOLTBOOK_EXIT" -ne 0 ]; then
        log "Moltbook: FAILED (exit $MOLTBOOK_EXIT)"
    else
        log "Moltbook: Done"
    fi
fi

# ═══════════════════════════════════════════════════════
# Twitter API audit (fxtwitter — no browser)
# ═══════════════════════════════════════════════════════
if [ "$RUN_TWITTER" -eq 1 ]; then
    TWITTER_COUNT=$(psql "$DATABASE_URL" -t -A -c "
        SELECT COUNT(*) FROM posts
        WHERE platform='twitter' AND status='active' AND our_url IS NOT NULL;" 2>/dev/null || echo "0")

    if [ "$TWITTER_COUNT" -gt 0 ]; then
        log "Twitter: API audit — $TWITTER_COUNT active tweets"
        python3 "$REPO_DIR/scripts/update_stats.py" --twitter-audit >> "$LOG_FILE" 2>&1
        STEP2_EXIT=$?
        if [ "$STEP2_EXIT" -ne 0 ]; then
            log "Twitter: FAILED (exit $STEP2_EXIT)"
        else
            log "Twitter: Done"
        fi
    else
        log "Twitter: SKIPPED — no active Twitter posts to audit"
    fi
fi

# ═══════════════════════════════════════════════════════
# LinkedIn audit (Python CDP — no LLM tokens)
# ═══════════════════════════════════════════════════════
if [ "$RUN_LINKEDIN" -eq 1 ]; then
    LINKEDIN_COUNT=$(psql "$DATABASE_URL" -t -A -c "
        SELECT COUNT(*) FROM posts
        WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
          AND our_url LIKE '%linkedin.com/feed/update/%';" 2>/dev/null || echo "0")

    if [ "$LINKEDIN_COUNT" -gt 0 ]; then
        log "LinkedIn: audit — $LINKEDIN_COUNT active posts (Python CDP)"

        OFFSET=0
        TOTAL_CHECKED=0

        while true; do
            BATCH_JSON=$(psql "$DATABASE_URL" -t -A -c "
                SELECT json_agg(q) FROM (
                    SELECT id, our_url as url
                    FROM posts
                    WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL
                      AND our_url LIKE '%linkedin.com/feed/update/%'
                    ORDER BY id
                    LIMIT 30 OFFSET $OFFSET
                ) q;" 2>/dev/null)

            [ "$BATCH_JSON" = "" ] || [ "$BATCH_JSON" = "null" ] && break

            AUDIT_TMPFILE=$(mktemp)
            python3 "$REPO_DIR/scripts/linkedin_browser.py" audit-batch "$BATCH_JSON" > "$AUDIT_TMPFILE" 2>/dev/null

            if [ $? -eq 0 ] && [ -s "$AUDIT_TMPFILE" ]; then
                DATABASE_URL="$DATABASE_URL" python3 - "$AUDIT_TMPFILE" <<'PYEOF' 2>&1 | tee -a "$LOG_FILE"
import json, os, sys, psycopg2

with open(sys.argv[1]) as f:
    results = json.load(f)
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
deleted = 0
for r in results:
    if r.get('status') == 'deleted':
        cur.execute('UPDATE posts SET status=%s, status_checked_at=NOW() WHERE id=%s', ('deleted', r['id']))
        deleted += 1
    elif r.get('status') != 'error':
        cur.execute('UPDATE posts SET upvotes=%s, comments_count=%s, views=%s, engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s',
            (r.get('reactions', 0), r.get('comments', 0), r.get('views', 0), r['id']))
conn.commit()
cur.close()
conn.close()
print(f'Batch: {len(results)} checked, {deleted} deleted')
PYEOF
                rm -f "$AUDIT_TMPFILE"
            else
                rm -f "$AUDIT_TMPFILE"
            fi

            BATCH_SIZE=$(echo "$BATCH_JSON" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
            TOTAL_CHECKED=$((TOTAL_CHECKED + BATCH_SIZE))
            [ "$BATCH_SIZE" -lt 30 ] && break
            OFFSET=$((OFFSET + 30))
        done

        log "LinkedIn: Done — $TOTAL_CHECKED posts audited"
    else
        log "LinkedIn: SKIPPED — no active posts to audit"
    fi
fi

# ═══════════════════════════════════════════════════════
# Orphan / stale post detection + summary (DB-only, every run)
# ═══════════════════════════════════════════════════════
log "Orphan/stale detection"

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
    log "Orphan/stale: Clean (no orphans, no broken URLs)"
fi

log "Summary"

ACTIVE=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='active';" 2>/dev/null || echo "?")
DELETED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='deleted';" 2>/dev/null || echo "?")
REMOVED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE status='removed';" 2>/dev/null || echo "?")

log "Post status: active=$ACTIVE deleted=$DELETED removed=$REMOVED"

# Log run to persistent monitor.
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
AUDIT_FAILED=$(( (STEP1_EXIT != 0 ? 1 : 0) + (STEP2_EXIT != 0 ? 1 : 0) + (STEP3_EXIT != 0 ? 1 : 0) ))
SCRIPT_TAG="audit${PLATFORM:+-$PLATFORM}"
python3 "$REPO_DIR/scripts/log_run.py" --script "$SCRIPT_TAG" --posted "$ACTIVE" --skipped 0 --failed "$AUDIT_FAILED" --cost 0 --elapsed "$RUN_ELAPSED"

log "=== Audit Pipeline complete (${LOG_TAG}): $(date) ==="

# Clean up old logs (keep last 14 days) — covers both audit-all-* and audit-<platform>-*.
find "$LOG_DIR" -name "audit-*.log" -mtime +14 -delete 2>/dev/null || true
