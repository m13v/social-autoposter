#!/bin/bash
#
# Weekly Cross-Product Roundup Pipeline.
#
# Iterates every project in config.json that has `seo_roundup.category` set
# and a landing_pages.repo on disk, and generates one dated best-of listicle
# per project. Each page lives at /best/<slug>-YYYY-MM-DD on the host
# product's own domain, lists 6-10 sibling projects (including cross-industry
# picks), and wires every sibling CTA to trackCrossProductClick so clicks
# attribute to the dashboard's Cross Product column.
#
# Lanes run in parallel (one background process per product) with a 0-30s
# jitter, matching the pattern used by cron_seo.sh. Per-product locks
# prevent double-generation if a previous run is still processing.
#
# Scheduling: launchd com.m13v.seo-weekly-roundup (Mondays 07:00 local).
# Also runnable ad-hoc: ./run_weekly_roundup.sh
#
# State: writes rows to seo_keywords with source='roundup'. The dashboard
# Status tab reads these as page_published_roundup events.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG="$ROOT_DIR/config.json"
GENERATOR="python3 $SCRIPT_DIR/generate_page.py"

TICK_ID=$(date +%Y-%m-%d_%H%M%S)
TICK_LOG_DIR="$SCRIPT_DIR/logs/roundup"
TICK_LOG="$TICK_LOG_DIR/tick_${TICK_ID}.log"

mkdir -p "$SCRIPT_DIR/.locks" "$TICK_LOG_DIR"

# Load .env so DATABASE_URL and API keys are available to spawned lanes.
[ -f "$ROOT_DIR/.env" ] && set -a && . "$ROOT_DIR/.env" && set +a

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" | tee -a "$TICK_LOG"; }

log "=== weekly-roundup tick $TICK_ID starting ==="

# Produce the eligible product list: anything with seo_roundup.category set
# and a real landing_pages.repo on disk. Emits TSV rows: name<TAB>category.
mapfile -t ELIGIBLE < <(python3 - <<PY
import json, os
with open("$CONFIG") as f:
    cfg = json.load(f)
for p in cfg.get("projects", []):
    sr = p.get("seo_roundup") or {}
    cat = sr.get("category")
    repo = (p.get("landing_pages") or {}).get("repo", "")
    repo = os.path.expanduser(repo) if repo else ""
    if cat and repo and os.path.isdir(repo):
        print(f"{p['name']}\t{cat}")
PY
)

if [ "${#ELIGIBLE[@]}" -eq 0 ]; then
    log "no eligible projects (seo_roundup.category unset or repo missing). nothing to do."
    exit 0
fi

log "eligible projects: ${#ELIGIBLE[@]}"
for row in "${ELIGIBLE[@]}"; do
    log "  $(echo "$row" | awk -F'\t' '{printf "%-20s %s", $1, $2}')"
done

# Spawn one background lane per product with a small jitter (0-30s) so we
# don't fire 14 Claude sessions at the exact same millisecond.
for row in "${ELIGIBLE[@]}"; do
    product=$(echo "$row" | awk -F'\t' '{print $1}')
    category=$(echo "$row" | awk -F'\t' '{print $2}')
    product_lower=$(echo "$product" | tr '[:upper:] ' '[:lower:]-')
    lock="$SCRIPT_DIR/.locks/roundup_${product_lower}.lock"
    lane_log="$TICK_LOG_DIR/${TICK_ID}_${product_lower}.log"

    (
        jitter=$((RANDOM % 30))
        sleep "$jitter"

        if [ -f "$lock" ]; then
            age=$(( $(date +%s) - $(stat -f %m "$lock" 2>/dev/null || stat -c %Y "$lock" 2>/dev/null || echo 0) ))
            if [ "$age" -gt 1800 ]; then
                echo "[$(ts)] $product: stale lock (${age}s), removing" >> "$lane_log"
                rm -f "$lock"
            else
                echo "[$(ts)] $product: lock held (${age}s), skipping tick" >> "$lane_log"
                exit 0
            fi
        fi
        echo "$$" > "$lock"
        trap 'rm -f "$lock"' EXIT

        today=$(date +%Y-%m-%d)
        month_name=$(date +%B)
        day_num=$(date +%-d)
        year=$(date +%Y)
        # Slug: best-<category-kebab>-YYYY-MM-DD. Weekly cadence means each
        # run produces a distinct slug even if two fire in the same month.
        cat_slug=$(echo "$category" | tr '[:upper:] ' '[:lower:]-' | sed -E 's/[^a-z0-9-]+/-/g; s/-+/-/g; s/^-|-$//g')
        slug="best-${cat_slug}-${today}"
        keyword="Best ${category} for ${month_name} ${day_num}, ${year}"

        echo "[$(ts)] $product: starting roundup" >> "$lane_log"
        echo "  slug=$slug" >> "$lane_log"
        echo "  keyword='$keyword'" >> "$lane_log"

        # Hand off to the unified generator. content_type=cross_roundup
        # routes to /best/<slug>; trigger=roundup writes seo_keywords rows
        # with source='roundup'. force=false by default; each week's slug
        # is distinct so there's no overwrite to guard against.
        $GENERATOR \
            --product "$product" \
            --keyword "$keyword" \
            --slug "$slug" \
            --trigger roundup \
            --content-type cross_roundup \
            >> "$lane_log" 2>&1
        exit_code=$?
        echo "[$(ts)] $product: generator exit=$exit_code" >> "$lane_log"
        exit "$exit_code"
    ) &
done

wait
log "=== weekly-roundup tick $TICK_ID complete ==="
