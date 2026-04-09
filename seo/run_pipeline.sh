#!/bin/bash
#
# Generalized SEO pipeline orchestrator.
# Picks the next unscored or pending keyword for a product,
# scores it via SERP analysis, and if it passes the threshold,
# triggers page generation in the product's website repo.
#
# Usage:
#   ./run_pipeline.sh <product_name> [--score-only] [--generate-only]
#
# Page generation uses product-specific prompt templates stored in
# seo/templates/<product>.md. No per-repo skills needed.
#
# Requires: python3, claude CLI
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG="$ROOT_DIR/config.json"
LOCK_DIR="$SCRIPT_DIR/.locks"
TEMPLATES_DIR="$SCRIPT_DIR/templates"

PRODUCT="${1:?Usage: $0 <product_name> [--score-only] [--generate-only]}"
MODE="${2:-full}"  # full, --score-only, --generate-only

PRODUCT_LOWER=$(echo "$PRODUCT" | tr '[:upper:]' '[:lower:]')
STATE_FILE="$SCRIPT_DIR/state/$PRODUCT_LOWER/underserved_keywords.json"
LOCK_FILE="$LOCK_DIR/$PRODUCT_LOWER.lock"
LOG_DIR="$SCRIPT_DIR/logs/$PRODUCT_LOWER"

mkdir -p "$LOCK_DIR" "$LOG_DIR"

# --- Lock ---
if [ -f "$LOCK_FILE" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$LOCK_FILE" 2>/dev/null || stat -c %Y "$LOCK_FILE" 2>/dev/null) ))
    if [ "$LOCK_AGE" -gt 1800 ]; then
        echo "Stale lock (${LOCK_AGE}s old), removing"
        rm -f "$LOCK_FILE"
    else
        echo "Pipeline already running for $PRODUCT (lock age: ${LOCK_AGE}s)"
        exit 0
    fi
fi
echo "$$" > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# --- Ensure state file exists ---
if [ ! -f "$STATE_FILE" ]; then
    echo "No state file found. Generating keywords first..."
    python3 "$SCRIPT_DIR/generate_keywords.py" "$PRODUCT"
fi

# --- Read product config ---
REPO_PATH=$(python3 -c "
import json, os
with open('$CONFIG') as f:
    c = json.load(f)
for p in c.get('projects', []):
    if p['name'].lower() == '$PRODUCT_LOWER':
        repo = p.get('landing_pages', {}).get('repo', '')
        print(os.path.expanduser(repo))
        break
")

WEBSITE=$(python3 -c "
import json
with open('$CONFIG') as f:
    c = json.load(f)
for p in c.get('projects', []):
    if p['name'].lower() == '$PRODUCT_LOWER':
        print(p.get('website', ''))
        break
")

DIFFERENTIATOR=$(python3 -c "
import json
with open('$CONFIG') as f:
    c = json.load(f)
for p in c.get('projects', []):
    if p['name'].lower() == '$PRODUCT_LOWER':
        print(p.get('differentiator', ''))
        break
")

if [ -z "$REPO_PATH" ]; then
    echo "Error: no landing_pages.repo configured for $PRODUCT in config.json"
    exit 1
fi

if [ ! -d "$REPO_PATH" ]; then
    echo "Error: repo not found at $REPO_PATH"
    exit 1
fi

# --- Ensure template exists ---
TEMPLATE_FILE="$TEMPLATES_DIR/$PRODUCT_LOWER.md"
if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "Error: no page template found at $TEMPLATE_FILE"
    echo "Create a template for $PRODUCT first."
    exit 1
fi

echo "=== SEO Pipeline: $PRODUCT ==="
echo "  Repo: $REPO_PATH"
echo "  Website: $WEBSITE"
echo "  Template: $TEMPLATE_FILE"
echo "  State: $STATE_FILE"
echo ""

# --- Step 1: Pick next keyword ---
if [ "$MODE" = "--generate-only" ]; then
    # Pick highest-scored pending keyword
    NEXT=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
pending = [k for k in state['keywords'] if k.get('status') == 'pending' and k.get('score') is not None and k['score'] >= 1.5]
pending.sort(key=lambda x: x['score'], reverse=True)
if pending:
    print(json.dumps(pending[0]))
else:
    print('NONE')
")
else
    # Priority: pending (ready to build) first, then unscored (need scoring)
    NEXT=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
# First: build pages for keywords already scored above threshold
pending = [k for k in state['keywords'] if k.get('status') == 'pending' and k.get('score') is not None and k['score'] >= 1.5]
pending.sort(key=lambda x: x['score'], reverse=True)
if pending:
    print(json.dumps(pending[0]))
else:
    # Then: score the next unscored keyword
    unscored = [k for k in state['keywords'] if k.get('status') == 'unscored']
    if unscored:
        print(json.dumps(unscored[0]))
    else:
        print('NONE')
")
fi

if [ "$NEXT" = "NONE" ]; then
    echo "No keywords to process. Generate more with: python3 generate_keywords.py $PRODUCT"
    exit 0
fi

KEYWORD=$(echo "$NEXT" | python3 -c "import json,sys; print(json.load(sys.stdin)['keyword'])")
SLUG=$(echo "$NEXT" | python3 -c "import json,sys; print(json.load(sys.stdin)['slug'])")
STATUS=$(echo "$NEXT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','unscored'))")

echo "Next keyword: $KEYWORD (slug: $SLUG, status: $STATUS)"

TIMESTAMP=$(date -u +%Y%m%d-%H%M%S)
LOG_FILE="$LOG_DIR/${TIMESTAMP}_${SLUG}.log"

# --- Step 2: Score via SERP (if unscored) ---
if [ "$STATUS" = "unscored" ] && [ "$MODE" != "--generate-only" ]; then
    echo ""
    echo "--- SERP Scoring ---"

    # Mark as scoring
    python3 -c "
import json
from datetime import datetime, timezone
with open('$STATE_FILE') as f:
    state = json.load(f)
for kw in state['keywords']:
    if kw['keyword'] == '$KEYWORD':
        kw['status'] = 'scoring'
        break
state['updated_at'] = datetime.now(timezone.utc).isoformat()
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f, indent=2)
"

    # Run Claude to score the SERP
    claude -p "You are a SERP analyst. Score this keyword for the product '$PRODUCT'.

Product: $PRODUCT
Website: $WEBSITE
Differentiator: $DIFFERENTIATOR

Keyword to score: \"$KEYWORD\"

Use WebSearch to analyze the SERP for this keyword. Score 3 signals (0-2 each):

Signal 1: Product Angle Gap (40% weight)
Search: \"$KEYWORD $PRODUCT_LOWER\" or similar
- Score 2: No pages specifically address this from $PRODUCT's angle ($DIFFERENTIATOR)
- Score 1: 1-2 generic pages exist
- Score 0: Multiple competitors already cover this well

Signal 2: Result Quality Gap (35% weight)
Search: \"$KEYWORD\"
- Score 2: Top results are thin (<500 words), outdated (>1 year), or off-topic
- Score 1: Decent content but lacks depth or specificity
- Score 0: Comprehensive, authoritative pages already exist

Signal 3: Commercial Fit (25% weight)
- Score 2: Exactly what $PRODUCT does
- Score 1: Moderate fit, some caveats
- Score 0: Poor fit for the product

RESPOND IN EXACTLY THIS JSON FORMAT (nothing else):
{
  \"keyword\": \"$KEYWORD\",
  \"signal1\": <0-2>,
  \"signal2\": <0-2>,
  \"signal3\": <0-2>,
  \"score\": <weighted composite>,
  \"notes\": \"<1-2 sentence SERP observation>\"
}
" --output-format json 2>"$LOG_FILE" | tee "$LOG_FILE.score"

    # Parse score and update state
    python3 -c "
import json, sys
from datetime import datetime, timezone

try:
    raw = open('$LOG_FILE.score').read().strip()
    start = raw.find('{')
    end = raw.rfind('}') + 1
    if start >= 0 and end > start:
        result = json.loads(raw[start:end])
    else:
        print('ERROR: Could not parse score output')
        sys.exit(1)

    with open('$STATE_FILE') as f:
        state = json.load(f)

    for kw in state['keywords']:
        if kw['keyword'] == '$KEYWORD':
            kw['signal1'] = result.get('signal1', 0)
            kw['signal2'] = result.get('signal2', 0)
            kw['signal3'] = result.get('signal3', 0)
            kw['score'] = result.get('score', 0)
            kw['notes'] = result.get('notes', '')
            kw['scored_at'] = datetime.now(timezone.utc).isoformat()
            if kw['score'] >= 1.5:
                kw['status'] = 'pending'
                print(f'SCORED: {kw[\"score\"]} -> pending (will build)')
            else:
                kw['status'] = 'skip'
                print(f'SCORED: {kw[\"score\"]} -> skip (below threshold)')
            break

    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    with open('$STATE_FILE', 'w') as f:
        json.dump(state, f, indent=2)

except Exception as e:
    print(f'ERROR parsing score: {e}')
    with open('$STATE_FILE') as f:
        state = json.load(f)
    for kw in state['keywords']:
        if kw['keyword'] == '$KEYWORD':
            kw['status'] = 'unscored'
            break
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    with open('$STATE_FILE', 'w') as f:
        json.dump(state, f, indent=2)
    sys.exit(1)
" || exit 1

    # Re-read status after scoring
    STATUS=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
for kw in state['keywords']:
    if kw['keyword'] == '$KEYWORD':
        print(kw['status'])
        break
")
fi

# --- Step 3: Generate page (if pending) ---
if [ "$STATUS" = "pending" ] && [ "$MODE" != "--score-only" ]; then
    echo ""
    echo "--- Page Generation ---"
    echo "Keyword: $KEYWORD"
    echo "Repo: $REPO_PATH"
    echo "Template: $TEMPLATE_FILE"

    # Mark as in_progress
    python3 -c "
import json
from datetime import datetime, timezone
with open('$STATE_FILE') as f:
    state = json.load(f)
for kw in state['keywords']:
    if kw['keyword'] == '$KEYWORD':
        kw['status'] = 'in_progress'
        break
state['updated_at'] = datetime.now(timezone.utc).isoformat()
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f, indent=2)
"

    # Read the template and substitute variables
    TEMPLATE_CONTENT=$(cat "$TEMPLATE_FILE")

    # Run Claude in the target repo to generate the page
    cd "$REPO_PATH"
    claude -p "You are an SEO content engineer. Create a guide page for this keyword.

KEYWORD: \"$KEYWORD\"
SLUG: \"$SLUG\"
PRODUCT: $PRODUCT
WEBSITE: $WEBSITE
DIFFERENTIATOR: $DIFFERENTIATOR

Follow these instructions exactly:

$TEMPLATE_CONTENT

After the page is created, committed, and deployed, report back with:
1. The page URL
2. The slug
3. Whether the build succeeded
" 2>>"$LOG_FILE" | tee -a "$LOG_FILE"

    # Mark as done
    cd "$SCRIPT_DIR"
    python3 -c "
import json
from datetime import datetime, timezone
with open('$STATE_FILE') as f:
    state = json.load(f)
for kw in state['keywords']:
    if kw['keyword'] == '$KEYWORD':
        kw['status'] = 'done'
        kw['completed_at'] = datetime.now(timezone.utc).isoformat()
        kw['page_url'] = '$WEBSITE/t/$SLUG'
        break
state['updated_at'] = datetime.now(timezone.utc).isoformat()
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f, indent=2)
"

    echo ""
    echo "=== Page generated for: $KEYWORD ==="
else
    if [ "$STATUS" = "skip" ]; then
        echo "Keyword scored below threshold, skipping page generation."
    fi
fi

# --- Report ---
echo ""
echo "=== Pipeline Report: $PRODUCT ==="
python3 -c "
import json
with open('$STATE_FILE') as f:
    state = json.load(f)
statuses = {}
for kw in state['keywords']:
    s = kw.get('status', 'unscored')
    statuses[s] = statuses.get(s, 0) + 1
total = len(state['keywords'])
print(f'  Total keywords: {total}')
for s, count in sorted(statuses.items()):
    print(f'  {s}: {count}')

pending = [k for k in state['keywords'] if k.get('status') == 'pending']
pending.sort(key=lambda x: x.get('score', 0), reverse=True)
if pending:
    print(f'  Top pending:')
    for p in pending[:5]:
        print(f'    {p[\"score\"]:.1f} | {p[\"keyword\"]}')
"
