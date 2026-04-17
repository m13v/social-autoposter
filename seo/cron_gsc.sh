#!/bin/bash
#
# GSC SEO Inbox cron orchestrator. Runs every 10 minutes.
# Picks a product that has gsc_property configured in config.json,
# then runs run_gsc_pipeline.sh for that product.
#
# Products are weighted-randomly selected (same weight field as DataForSEO pipeline).
# Only products with landing_pages.gsc_property set are eligible.
#
# Usage: cron_gsc.sh (no args)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCK_FILE="$SCRIPT_DIR/.locks/cron_gsc.lock"
# Per-run log file lives in skill/logs/ so the dashboard picks it up alongside
# every other pipeline. Filename prefix matches the script basename so
# auto-discovered "Other Jobs" in bin/server.js resolve Last Run correctly.
LOG_FILE="$REPO_DIR/skill/logs/cron_gsc-$(date +%Y-%m-%d_%H%M%S).log"

mkdir -p "$SCRIPT_DIR/.locks" "$REPO_DIR/skill/logs"

# --- Global lock ---
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

# --- Pick product by weighted random (only those with gsc_property) ---
# Shared logic in seo/select_product.py (single source of truth for both pipelines).
PRODUCT=$(python3 "$SCRIPT_DIR/select_product.py" --require-gsc)

if [ "$PRODUCT" = "NONE" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) No eligible products with gsc_property configured" >> "$LOG_FILE"
    exit 0
fi

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Selected product: $PRODUCT" >> "$LOG_FILE"

"$SCRIPT_DIR/run_gsc_pipeline.sh" "$PRODUCT" >> "$LOG_FILE" 2>&1

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Pipeline complete for $PRODUCT" >> "$LOG_FILE"

# Clean up old per-product logs (keep 7 days)
find "$SCRIPT_DIR/logs" -name "gsc_*.log" -mtime +7 -delete 2>/dev/null || true
# Clean up old consolidated run logs in skill/logs (keep 14 days)
find "$REPO_DIR/skill/logs" -name "cron_gsc-*.log" -mtime +14 -delete 2>/dev/null || true
