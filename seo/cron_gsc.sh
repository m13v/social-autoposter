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

[ -f "$HOME/.social-paused" ] && echo "PAUSED: ~/.social-paused exists, skipping run." && exit 0
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG="$ROOT_DIR/config.json"
LOCK_FILE="$SCRIPT_DIR/.locks/cron_gsc.lock"
LOG_FILE="$SCRIPT_DIR/logs/cron_gsc.log"

mkdir -p "$SCRIPT_DIR/.locks" "$SCRIPT_DIR/logs"

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
PRODUCT=$(python3 -c "
import json, random, os

with open('$CONFIG') as f:
    config = json.load(f)

eligible = []
for p in config.get('projects', []):
    lp = p.get('landing_pages', {})
    if not lp.get('gsc_property'):
        continue
    repo = lp.get('repo', '')
    if not repo or not os.path.isdir(os.path.expanduser(repo)):
        continue
    eligible.append((p['name'], p.get('weight', 1)))

if not eligible:
    print('NONE')
else:
    names, weights = zip(*eligible)
    print(random.choices(names, weights=weights, k=1)[0])
")

if [ "$PRODUCT" = "NONE" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) No eligible products with gsc_property configured" >> "$LOG_FILE"
    exit 0
fi

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Selected product: $PRODUCT" >> "$LOG_FILE"

"$SCRIPT_DIR/run_gsc_pipeline.sh" "$PRODUCT" >> "$LOG_FILE" 2>&1

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Pipeline complete for $PRODUCT" >> "$LOG_FILE"

# Clean up old per-product logs (keep 7 days)
find "$SCRIPT_DIR/logs" -name "gsc_*.log" -mtime +7 -delete 2>/dev/null || true
