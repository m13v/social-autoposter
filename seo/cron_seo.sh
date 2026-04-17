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
LOCK_FILE="$SCRIPT_DIR/.locks/cron_seo.lock"
LOG_FILE="$SCRIPT_DIR/logs/cron_seo.log"
DB="python3 $SCRIPT_DIR/db_helpers.py"
REFRESH_DIR="$SCRIPT_DIR/.refresh_timestamps"

mkdir -p "$SCRIPT_DIR/.locks" "$SCRIPT_DIR/logs" "$REFRESH_DIR"

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

# --- Pick product by weighted random ---
# Shared logic in seo/select_product.py (single source of truth for both pipelines).
PRODUCT=$(python3 "$SCRIPT_DIR/select_product.py")

if [ "$PRODUCT" = "NONE" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) No eligible products found" >> "$LOG_FILE"
    exit 0
fi

PRODUCT_LOWER=$(echo "$PRODUCT" | tr '[:upper:]' '[:lower:]')

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Selected product: $PRODUCT (weighted random)" >> "$LOG_FILE"

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
