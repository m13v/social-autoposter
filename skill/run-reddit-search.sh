#!/bin/bash
# Social Autoposter - Reddit comment posting via search API + CDP browser
#
# Mirrors run-twitter-cycle.sh's release_lock pattern: each of N iterations
# splits into a "plan" phase (project pick + Claude session that uses
# reddit_tools.py HTTP search/fetch — no browser) and a "post" phase (CDP
# posting via reddit_browser.py). The reddit-browser lock is held only around
# the post phase, freeing it during plan so peers (dm-outreach-reddit,
# link-edit-reddit, engage-dm-replies-reddit, audit-reddit*) can run their
# browser steps in those windows instead of waiting on us.
#
# Called by launchd every 30 minutes.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-reddit-search-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== Reddit Search Post Run: $(date) ==="

source "$REPO_DIR/skill/lock.sh"

ITERATIONS=5
LIMIT=1
EXCLUDE=""
TOTAL_POSTED=0
TOTAL_FAILED=0
TOTAL_SKIPPED=0
RUN_START=$(date +%s)

# Acquire once at the top so the EXIT trap leaves a clean state and any
# already-running peer is queued behind us. The plan loop releases for the
# Claude/HTTP phase and re-acquires before posting.
acquire_lock "reddit-browser" 3600

for i in $(seq 1 "$ITERATIONS"); do
    log "--- Iteration $i/$ITERATIONS ---"
    PLAN_FILE=$(mktemp -t post_reddit_plan.XXXXXX.json)

    # Plan phase: no browser. Release the lock so peers can run.
    log "Releasing reddit-browser lock for plan phase..."
    release_lock "reddit-browser"

    set +e
    python3 "$REPO_DIR/scripts/post_reddit.py" \
        --phase plan \
        --out "$PLAN_FILE" \
        --exclude "$EXCLUDE" \
        --limit "$LIMIT" 2>&1 | tee -a "$LOG_FILE"
    PLAN_RC=${PIPESTATUS[0]}
    set -e

    case "$PLAN_RC" in
        0)
            : # plan succeeded with decisions
            ;;
        3)
            log "Plan phase: rate-limited; ending run."
            rm -f "$PLAN_FILE"
            acquire_lock "reddit-browser" 3600
            break
            ;;
        4)
            log "Plan phase: no eligible project left; ending run."
            rm -f "$PLAN_FILE"
            acquire_lock "reddit-browser" 3600
            break
            ;;
        5)
            log "Plan phase: Claude failed; counting as failed and continuing."
            TOTAL_FAILED=$((TOTAL_FAILED + 1))
            rm -f "$PLAN_FILE"
            acquire_lock "reddit-browser" 3600
            continue
            ;;
        6)
            log "Plan phase: no decisions drafted; counting as skipped and continuing."
            TOTAL_SKIPPED=$((TOTAL_SKIPPED + 1))
            PICKED=$(python3 -c "import json,sys;print(json.load(open('$PLAN_FILE')).get('project_name',''))" 2>/dev/null || echo "")
            [ -n "$PICKED" ] && EXCLUDE="${EXCLUDE:+$EXCLUDE,}$PICKED"
            rm -f "$PLAN_FILE"
            acquire_lock "reddit-browser" 3600
            continue
            ;;
        *)
            log "Plan phase: unexpected exit code $PLAN_RC; aborting iteration."
            TOTAL_FAILED=$((TOTAL_FAILED + 1))
            rm -f "$PLAN_FILE"
            acquire_lock "reddit-browser" 3600
            continue
            ;;
    esac

    PICKED=$(python3 -c "import json,sys;print(json.load(open('$PLAN_FILE')).get('project_name',''))" 2>/dev/null || echo "")
    [ -n "$PICKED" ] && EXCLUDE="${EXCLUDE:+$EXCLUDE,}$PICKED"

    # Post phase: needs browser. Re-acquire (blocks if a peer is mid-run).
    log "Re-acquiring reddit-browser lock for post phase..."
    acquire_lock "reddit-browser" 3600

    set +e
    POST_OUT=$(python3 "$REPO_DIR/scripts/post_reddit.py" --phase post --in "$PLAN_FILE" 2>&1 | tee -a "$LOG_FILE")
    POST_RC=${PIPESTATUS[0]}
    set -e

    if [ "$POST_RC" = "0" ]; then
        ITER_POSTED=$(echo "$POST_OUT" | grep -oE 'posted=[0-9]+' | tail -1 | cut -d= -f2 || echo 0)
        ITER_FAILED=$(echo "$POST_OUT" | grep -oE 'failed=[0-9]+' | tail -1 | cut -d= -f2 || echo 0)
        TOTAL_POSTED=$((TOTAL_POSTED + ${ITER_POSTED:-0}))
        TOTAL_FAILED=$((TOTAL_FAILED + ${ITER_FAILED:-0}))
    else
        log "Post phase: exit code $POST_RC; counting as failed."
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
    fi

    rm -f "$PLAN_FILE"
done

ELAPSED=$(( $(date +%s) - RUN_START ))
log "=== Run summary: posted=$TOTAL_POSTED failed=$TOTAL_FAILED skipped=$TOTAL_SKIPPED projects=[$EXCLUDE] elapsed=${ELAPSED}s ==="

python3 "$REPO_DIR/scripts/log_run.py" \
    --script "post_reddit" \
    --posted "$TOTAL_POSTED" \
    --skipped "$TOTAL_SKIPPED" \
    --failed "$TOTAL_FAILED" \
    --cost 0 \
    --elapsed "$ELAPSED" || true

log "=== Done: $(date) ==="
