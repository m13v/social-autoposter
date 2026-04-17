#!/bin/bash
#
# SEO pipeline cron orchestrator. Runs every 10 minutes.
# Selects a product based on weight distribution, regenerates
# keywords if stale (>24h), then runs the scoring/generation pipeline.
#
# All state is stored in Postgres (seo_keywords table).
#
# Products must have:
#   1. landing_pages.repo in config.json (repo exists on disk)
#
# Usage: cron_seo.sh (no args, picks product by weighted random)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCK_FILE="$SCRIPT_DIR/.locks/cron_seo.lock"
# Per-run log file lives in skill/logs/ so the dashboard picks it up alongside
# every other pipeline. Filename prefix matches the script basename so
# auto-discovered "Other Jobs" in bin/server.js resolve Last Run correctly.
LOG_FILE="$REPO_DIR/skill/logs/cron_seo-$(date +%Y-%m-%d_%H%M%S).log"
DB="python3 $SCRIPT_DIR/db_helpers.py"
REFRESH_DIR="$SCRIPT_DIR/.refresh_timestamps"

mkdir -p "$SCRIPT_DIR/.locks" "$REPO_DIR/skill/logs" "$REFRESH_DIR"

# --- Global lock (only one cron instance at a time) ---
if [ -f "$LOCK_FILE" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$LOCK_FILE" 2>/dev/null || stat -c %Y "$LOCK_FILE" 2>/dev/null) ))
    if [ "$LOCK_AGE" -gt 3600 ]; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Stale cron lock (${LOCK_AGE}s), removing" >> "$LOG_FILE"
        rm -f "$LOCK_FILE"
    else
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Cron already running (lock age: ${LOCK_AGE}s), skipping" >> "$LOG_FILE"
        exit 0
    fi
fi
echo "$$" > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# --- Reap stuck rows (orphans from killed runs) ---
python3 "$SCRIPT_DIR/reap_stuck.py" >> "$LOG_FILE" 2>&1 || true

# --- Pick product by weighted random ---
# Prefer products with pending-to-generate work (score>=1.5), then fall back
# to products with unscored work (scoring pass). If neither exists, fall
# back to any repo-present product (lets DataForSEO refresh fire for stale queues).
PRODUCT=$(python3 "$SCRIPT_DIR/select_product.py" --mode serp-generate)
PICK_MODE="serp-generate"
if [ "$PRODUCT" = "NONE" ]; then
    PRODUCT=$(python3 "$SCRIPT_DIR/select_product.py" --mode serp-score)
    PICK_MODE="serp-score"
fi
if [ "$PRODUCT" = "NONE" ]; then
    PRODUCT=$(python3 "$SCRIPT_DIR/select_product.py")
    PICK_MODE="any"
fi

if [ "$PRODUCT" = "NONE" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) No eligible products found" >> "$LOG_FILE"
    exit 0
fi

PRODUCT_LOWER=$(echo "$PRODUCT" | tr '[:upper:]' '[:lower:]')

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Selected product: $PRODUCT (mode=$PICK_MODE)" >> "$LOG_FILE"

# --- Refresh keywords if stale (>24h since last DataForSEO fetch) ---
REFRESH_FILE="$REFRESH_DIR/$PRODUCT_LOWER"

NEEDS_REFRESH=false
if [ ! -f "$REFRESH_FILE" ]; then
    NEEDS_REFRESH=true
else
    REFRESH_AGE=$(( $(date +%s) - $(stat -f %m "$REFRESH_FILE" 2>/dev/null || stat -c %Y "$REFRESH_FILE" 2>/dev/null) ))
    if [ "$REFRESH_AGE" -gt 86400 ]; then
        NEEDS_REFRESH=true
    fi
fi

if [ "$NEEDS_REFRESH" = true ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Refreshing keywords for $PRODUCT via DataForSEO" >> "$LOG_FILE"
    python3 "$SCRIPT_DIR/generate_keywords.py" "$PRODUCT" >> "$LOG_FILE" 2>&1
    touch "$REFRESH_FILE"
fi

# --- Check if there's work to do (from Postgres) ---
HAS_WORK=$($DB has_work "$PRODUCT")

if [ "$HAS_WORK" = "no" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) No work for $PRODUCT (all scored/done/skipped)" >> "$LOG_FILE"
    exit 0
fi

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Running pipeline for $PRODUCT" >> "$LOG_FILE"

# --- Run the pipeline ---
"$SCRIPT_DIR/run_serp_pipeline.sh" "$PRODUCT" >> "$LOG_FILE" 2>&1

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Pipeline complete for $PRODUCT" >> "$LOG_FILE"

# Clean up old consolidated run logs in skill/logs (keep 14 days)
find "$REPO_DIR/skill/logs" -name "cron_seo-*.log" -mtime +14 -delete 2>/dev/null || true
