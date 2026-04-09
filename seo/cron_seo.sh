#!/bin/bash
#
# SEO pipeline cron orchestrator. Runs every 10 minutes.
# Selects a product based on weight distribution, regenerates
# keywords if stale (>24h), then runs the scoring/generation pipeline.
#
# Products must have:
#   1. landing_pages.repo in config.json (repo exists on disk)
#   2. An SEO page skill in .claude/skills/
#
# Usage: cron_seo.sh (no args, picks product by weighted random)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG="$ROOT_DIR/config.json"
LOCK_FILE="$SCRIPT_DIR/.locks/cron_seo.lock"
LOG_FILE="$SCRIPT_DIR/logs/cron_seo.log"

mkdir -p "$SCRIPT_DIR/.locks" "$SCRIPT_DIR/logs"

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
PRODUCT=$(python3 -c "
import json, random, os

with open('$CONFIG') as f:
    config = json.load(f)

# Filter to products that have repos with SEO skills
eligible = []
for p in config.get('projects', []):
    repo = p.get('landing_pages', {}).get('repo', '')
    if not repo:
        continue
    repo_path = os.path.expanduser(repo)
    if not os.path.isdir(repo_path):
        continue
    # Check for page generation template in social-autoposter
    template_path = os.path.join('$SCRIPT_DIR', 'templates', p['name'].lower() + '.md')
    if os.path.exists(template_path):
        eligible.append((p['name'], p.get('weight', 1)))

if not eligible:
    print('NONE')
else:
    names, weights = zip(*eligible)
    chosen = random.choices(names, weights=weights, k=1)[0]
    print(chosen)
")

if [ "$PRODUCT" = "NONE" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) No eligible products found" >> "$LOG_FILE"
    exit 0
fi

PRODUCT_LOWER=$(echo "$PRODUCT" | tr '[:upper:]' '[:lower:]')

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Selected product: $PRODUCT (weighted random)" >> "$LOG_FILE"

# --- Refresh keywords if stale (>24h since last generation) ---
STATE_FILE="$SCRIPT_DIR/state/$PRODUCT_LOWER/underserved_keywords.json"

NEEDS_REFRESH=false
if [ ! -f "$STATE_FILE" ]; then
    NEEDS_REFRESH=true
else
    LAST_UPDATE=$(python3 -c "
import json
from datetime import datetime, timezone, timedelta
with open('$STATE_FILE') as f:
    state = json.load(f)
updated = state.get('updated_at', '')
if not updated:
    print('stale')
else:
    try:
        dt = datetime.fromisoformat(updated)
        if datetime.now(timezone.utc) - dt > timedelta(hours=24):
            print('stale')
        else:
            print('fresh')
    except:
        print('stale')
")
    if [ "$LAST_UPDATE" = "stale" ]; then
        NEEDS_REFRESH=true
    fi
fi

if [ "$NEEDS_REFRESH" = true ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Refreshing keywords for $PRODUCT via DataForSEO" >> "$LOG_FILE"
    python3 "$SCRIPT_DIR/generate_keywords.py" "$PRODUCT" >> "$LOG_FILE" 2>&1
fi

# --- Check if there's work to do ---
HAS_WORK=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
unscored = sum(1 for k in state['keywords'] if k.get('status') == 'unscored')
pending = sum(1 for k in state['keywords'] if k.get('status') == 'pending' and (k.get('score') or 0) >= 1.5)
if unscored > 0 or pending > 0:
    print(f'yes:unscored={unscored},pending={pending}')
else:
    print('no')
")

if [[ "$HAS_WORK" == no ]]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) No work for $PRODUCT (all scored/done/skipped)" >> "$LOG_FILE"
    exit 0
fi

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Running pipeline for $PRODUCT ($HAS_WORK)" >> "$LOG_FILE"

# --- Run the pipeline ---
"$SCRIPT_DIR/run_pipeline.sh" "$PRODUCT" >> "$LOG_FILE" 2>&1

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Pipeline complete for $PRODUCT" >> "$LOG_FILE"
