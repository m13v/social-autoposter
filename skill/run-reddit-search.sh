#!/bin/bash
# Social Autoposter - Reddit comment posting via search API + CDP browser
#
# 4-phase pipeline per iteration (mirrors Twitter's discover/T1/post split):
#   1. Discover  - Claude searches and selects threads only (no drafting, no browser)
#   2. Ripen     - T0 snapshot, 5-min sleep, T1 re-poll, composite delta gate
#   3. Draft     - Claude writes comments ONLY for ripen-survivors (no browser)
#   4. Post      - CDP browser posts survivors with drafted text
#
# Browser lock is held ONLY around post phase. All other phases run unlocked
# so peers (dm-outreach, link-edit, engage-dm-replies, audit) can use the
# browser during our HTTP/Claude work.
#
# Called by launchd every 15 minutes via run-reddit-search-launchd.sh.

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

for i in $(seq 1 "$ITERATIONS"); do
    log "--- Iteration $i/$ITERATIONS ---"
    DISCOVER_FILE=$(mktemp -t post_reddit_discover.XXXXXX.json)

    # Phase 1: Discover — search and select threads. No browser, no drafting.
    set +e
    python3 "$REPO_DIR/scripts/post_reddit.py" \
        --phase discover \
        --out "$DISCOVER_FILE" \
        --exclude "$EXCLUDE" \
        --limit "$LIMIT" 2>&1 | tee -a "$LOG_FILE"
    DISCOVER_RC=${PIPESTATUS[0]}
    set -e

    case "$DISCOVER_RC" in
        0)
            : # discover succeeded with candidates
            ;;
        3)
            log "Discover phase: rate-limited; ending run."
            rm -f "$DISCOVER_FILE"
            break
            ;;
        4)
            log "Discover phase: no eligible project left; ending run."
            rm -f "$DISCOVER_FILE"
            break
            ;;
        5)
            log "Discover phase: Claude failed; counting as failed and continuing."
            TOTAL_FAILED=$((TOTAL_FAILED + 1))
            rm -f "$DISCOVER_FILE"
            continue
            ;;
        6)
            log "Discover phase: no candidates found; counting as skipped and continuing."
            TOTAL_SKIPPED=$((TOTAL_SKIPPED + 1))
            PICKED=$(python3 -c "import json,sys;print(json.load(open('$DISCOVER_FILE')).get('project_name',''))" 2>/dev/null || echo "")
            [ -n "$PICKED" ] && EXCLUDE="${EXCLUDE:+$EXCLUDE,}$PICKED"
            rm -f "$DISCOVER_FILE"
            continue
            ;;
        *)
            log "Discover phase: unexpected exit code $DISCOVER_RC; counting as failed."
            TOTAL_FAILED=$((TOTAL_FAILED + 1))
            rm -f "$DISCOVER_FILE"
            continue
            ;;
    esac

    PICKED=$(python3 -c "import json,sys;print(json.load(open('$DISCOVER_FILE')).get('project_name',''))" 2>/dev/null || echo "")
    [ -n "$PICKED" ] && EXCLUDE="${EXCLUDE:+$EXCLUDE,}$PICKED"

    # Phase 2: Ripen — T0 snapshot, 5-min sleep, T1 re-poll, composite delta gate.
    # composite = Δup + 4*Δcomments, floor > 1. Runs without browser lock.
    RIPEN_FILE=$(mktemp -t post_reddit_ripened.XXXXXX.json)
    log "Ripening candidates (5-min delta gate, floor>1, w_comments=4)..."
    set +e
    python3 "$REPO_DIR/scripts/ripen_reddit_plan.py" \
        --in "$DISCOVER_FILE" \
        --out "$RIPEN_FILE" 2>&1 | tee -a "$LOG_FILE"
    RIPEN_RC=${PIPESTATUS[0]}
    set -e

    if [ "$RIPEN_RC" != "0" ]; then
        log "Ripen phase: exit code $RIPEN_RC; falling back to unfiltered discover output."
        cp "$DISCOVER_FILE" "$RIPEN_FILE"
    fi

    SURVIVORS=$(python3 -c "import json;print(len(json.load(open('$RIPEN_FILE')).get('decisions',[])))" 2>/dev/null || echo 0)
    if [ "$SURVIVORS" = "0" ]; then
        log "Ripen phase: 0 survivors; skipping draft and post for this iteration."
        TOTAL_SKIPPED=$((TOTAL_SKIPPED + 1))
        rm -f "$DISCOVER_FILE" "$RIPEN_FILE"
        continue
    fi
    log "Ripen phase: $SURVIVORS candidate(s) passed delta gate."

    # Phase 3: Draft — Claude writes comments for survivors only. No browser.
    DRAFT_FILE=$(mktemp -t post_reddit_draft.XXXXXX.json)
    log "Drafting comments for $SURVIVORS survivor(s)..."
    set +e
    python3 "$REPO_DIR/scripts/post_reddit.py" \
        --phase draft \
        --in "$RIPEN_FILE" \
        --out "$DRAFT_FILE" 2>&1 | tee -a "$LOG_FILE"
    DRAFT_RC=${PIPESTATUS[0]}
    set -e

    case "$DRAFT_RC" in
        0)
            : # draft succeeded
            ;;
        5)
            log "Draft phase: Claude failed; counting as failed and continuing."
            TOTAL_FAILED=$((TOTAL_FAILED + 1))
            rm -f "$DISCOVER_FILE" "$RIPEN_FILE" "$DRAFT_FILE"
            continue
            ;;
        6)
            log "Draft phase: no drafted decisions; counting as skipped."
            TOTAL_SKIPPED=$((TOTAL_SKIPPED + 1))
            rm -f "$DISCOVER_FILE" "$RIPEN_FILE" "$DRAFT_FILE"
            continue
            ;;
        *)
            log "Draft phase: unexpected exit code $DRAFT_RC; counting as failed."
            TOTAL_FAILED=$((TOTAL_FAILED + 1))
            rm -f "$DISCOVER_FILE" "$RIPEN_FILE" "$DRAFT_FILE"
            continue
            ;;
    esac

    # Phase 4: Post — needs browser. Acquire lock, post, release immediately.
    log "Acquiring reddit-browser lock for post phase..."
    acquire_lock "reddit-browser" 3600
    ensure_browser_healthy "reddit"

    set +e
    POST_OUT=$(python3 "$REPO_DIR/scripts/post_reddit.py" --phase post --in "$DRAFT_FILE" 2>&1 | tee -a "$LOG_FILE")
    POST_RC=${PIPESTATUS[0]}
    set -e

    release_lock "reddit-browser"

    if [ "$POST_RC" = "0" ]; then
        ITER_POSTED=$(echo "$POST_OUT" | grep -oE 'posted=[0-9]+' | tail -1 | cut -d= -f2 || echo 0)
        ITER_FAILED=$(echo "$POST_OUT" | grep -oE 'failed=[0-9]+' | tail -1 | cut -d= -f2 || echo 0)
        TOTAL_POSTED=$((TOTAL_POSTED + ${ITER_POSTED:-0}))
        TOTAL_FAILED=$((TOTAL_FAILED + ${ITER_FAILED:-0}))
    else
        log "Post phase: exit code $POST_RC; counting as failed."
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
    fi

    rm -f "$DISCOVER_FILE" "$RIPEN_FILE" "$DRAFT_FILE"
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
