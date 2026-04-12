#!/bin/bash
#
# Generalized SEO pipeline orchestrator.
# Picks the next unscored or pending keyword for a product,
# scores it via SERP analysis, and if it passes the threshold,
# triggers page generation in the product's website repo.
#
# All state is stored in Postgres (seo_keywords table).
#
# Usage:
#   ./run_pipeline.sh <product_name> [--score-only] [--generate-only]
#
# Page generation uses product-specific prompt templates stored in
# seo/templates/<product>.md. No per-repo skills needed.
#
# Requires: python3, claude CLI, psycopg2
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG="$ROOT_DIR/config.json"
LOCK_DIR="$SCRIPT_DIR/.locks"
TEMPLATES_DIR="$SCRIPT_DIR/templates"
DB="python3 $SCRIPT_DIR/db_helpers.py"

PRODUCT="${1:?Usage: $0 <product_name> [--score-only] [--generate-only]}"
MODE="${2:-full}"  # full, --score-only, --generate-only

PRODUCT_LOWER=$(echo "$PRODUCT" | tr '[:upper:]' '[:lower:]')
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

PRODUCT_SOURCE_BLOCK=$(CONFIG="$CONFIG" PRODUCT_LOWER="$PRODUCT_LOWER" python3 <<'PYEOF'
import json, os
with open(os.environ["CONFIG"]) as f:
    c = json.load(f)
block = ""
for p in c.get("projects", []):
    if p["name"].lower() == os.environ["PRODUCT_LOWER"]:
        sources = p.get("landing_pages", {}).get("product_source", [])
        if sources:
            parts = []
            for s in sources:
                path = os.path.expanduser(s.get("path", ""))
                desc = s.get("description", "").strip()
                parts.append(f"- {path}\n  {desc}")
            block = "\n\n".join(parts)
        else:
            block = "(no external product source repo configured for this product)\n\nYour current working directory is effectively both the website and the product. Feel free to read anywhere in the repo if it helps you find a real angle, including landing copy, existing guide pages, components, and any fixtures or data files."
        break
print(block)
PYEOF
)

echo "=== SEO Pipeline: $PRODUCT ==="
echo "  Repo: $REPO_PATH"
echo "  Website: $WEBSITE"
echo "  Template: $TEMPLATE_FILE"
echo ""

# --- Step 1: Pick next keyword from Postgres ---
NEXT=$($DB pick "$PRODUCT")

if [ "$NEXT" = "NONE" ] || [ "$NEXT" = "null" ]; then
    echo "No keywords to process. Generate more with: python3 generate_keywords.py $PRODUCT"
    exit 0
fi

KEYWORD=$(echo "$NEXT" | python3 -c "import json,sys; print(json.load(sys.stdin)['keyword'])")
SLUG=$(echo "$NEXT" | python3 -c "import json,sys; print(json.load(sys.stdin)['slug'])")
STATUS=$(echo "$NEXT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','unscored'))")

# If --generate-only, skip unscored keywords
if [ "$MODE" = "--generate-only" ] && [ "$STATUS" = "unscored" ]; then
    echo "No pending keywords to generate. Score some first."
    exit 0
fi

echo "Next keyword: $KEYWORD (slug: $SLUG, status: $STATUS)"

TIMESTAMP=$(date -u +%Y%m%d-%H%M%S)
LOG_FILE="$LOG_DIR/${TIMESTAMP}_${SLUG}.log"

# --- Step 2: Score via SERP (if unscored) ---
if [ "$STATUS" = "unscored" ] && [ "$MODE" != "--generate-only" ]; then
    echo ""
    echo "--- SERP Scoring ---"

    # Mark as scoring in Postgres
    $DB update "$PRODUCT" "$KEYWORD" scoring

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

    # Parse score and update Postgres (use env vars to avoid quoting issues)
    SEO_SCORE_FILE="$LOG_FILE.score" SEO_PRODUCT="$PRODUCT" SEO_KEYWORD="$KEYWORD" SEO_SCRIPT_DIR="$SCRIPT_DIR" \
    python3 -c "
import json, sys, os
sys.path.insert(0, os.environ['SEO_SCRIPT_DIR'])
from db_helpers import update_status

product = os.environ['SEO_PRODUCT']
keyword = os.environ['SEO_KEYWORD']

try:
    raw = open(os.environ['SEO_SCORE_FILE']).read().strip()
    start = raw.find('{')
    end = raw.rfind('}') + 1
    if start >= 0 and end > start:
        outer = json.loads(raw[start:end])
    else:
        print('ERROR: Could not parse score output')
        sys.exit(1)

    # --output-format json wraps the score JSON inside a 'result' string field
    if 'result' in outer and isinstance(outer['result'], str) and 'score' not in outer:
        inner_raw = outer['result']
        inner_start = inner_raw.find('{')
        inner_end = inner_raw.rfind('}') + 1
        if inner_start >= 0 and inner_end > inner_start:
            result = json.loads(inner_raw[inner_start:inner_end])
        else:
            print('ERROR: Could not parse inner score JSON from envelope')
            sys.exit(1)
    else:
        result = outer

    score = result.get('score', 0)
    status = 'pending' if score >= 1.5 else 'skip'

    update_status(product, keyword, status,
        score=score,
        signal1=result.get('signal1', 0),
        signal2=result.get('signal2', 0),
        signal3=result.get('signal3', 0),
        notes=result.get('notes', ''))

    print(f'SCORED: {score} -> {status}')

except Exception as e:
    print(f'ERROR parsing score: {e}')
    update_status(product, keyword, 'unscored')
    sys.exit(1)
" || exit 1

    # Re-read status after scoring
    STATUS=$(SEO_PRODUCT="$PRODUCT" SEO_KEYWORD="$KEYWORD" SEO_SCRIPT_DIR="$SCRIPT_DIR" \
    python3 -c "
import sys, os
sys.path.insert(0, os.environ['SEO_SCRIPT_DIR'])
from db_helpers import get_conn
conn = get_conn()
cur = conn.cursor()
cur.execute('SELECT status FROM seo_keywords WHERE product = %s AND keyword = %s',
            (os.environ['SEO_PRODUCT'], os.environ['SEO_KEYWORD']))
row = cur.fetchone()
print(row[0] if row else 'unknown')
cur.close()
conn.close()
")
fi

# --- Step 3: Generate page (if pending) ---
if [ "$STATUS" = "pending" ] && [ "$MODE" != "--score-only" ]; then
    echo ""
    echo "--- Page Generation ---"
    echo "Keyword: $KEYWORD"
    echo "Repo: $REPO_PATH"
    echo "Template: $TEMPLATE_FILE"

    # Check if slug already exists as a done page
    SLUG_CHECK=$($DB check_slug "$PRODUCT" "$SLUG")
    if [ "$SLUG_CHECK" = "exists" ]; then
        echo "Slug '$SLUG' already exists as a completed page. Skipping."
        $DB update "$PRODUCT" "$KEYWORD" done
        exit 0
    fi

    # Mark as in_progress in Postgres
    $DB update "$PRODUCT" "$KEYWORD" in_progress

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

## Source material beyond the website repo

You are working in a website repo, but the product is not only here. If it helps you find a more unique angle for this page, you are free to read from the locations listed below. Nothing is required. Use them when they serve the topic, ignore them when they do not.

Product source for $PRODUCT:

$PRODUCT_SOURCE_BLOCK

These are not prompts to extract facts from. They are places where real implementation, real behavior, real data, and real constraints live. If the keyword touches something the product actually does, reading the relevant files often yields an angle no competitor has. If it does not, do not force it.

Same for product data: if the product has stored runs, logs, scenarios, examples, or records, you are welcome to look at them as inspiration or for real numbers to cite. Do not invent stats. Do not copy private data verbatim. Use judgment.

Follow these instructions exactly:

$TEMPLATE_CONTENT

After the page is created, committed, and deployed, report back with:
1. The page URL
2. The slug
3. Whether the build succeeded
" 2>>"$LOG_FILE" | tee -a "$LOG_FILE"

    # Mark as done in Postgres
    cd "$SCRIPT_DIR"
    SEO_PRODUCT="$PRODUCT" SEO_KEYWORD="$KEYWORD" SEO_PAGE_URL="$WEBSITE/t/$SLUG" SEO_SCRIPT_DIR="$SCRIPT_DIR" \
    python3 -c "
import sys, os
sys.path.insert(0, os.environ['SEO_SCRIPT_DIR'])
from db_helpers import update_status
update_status(os.environ['SEO_PRODUCT'], os.environ['SEO_KEYWORD'], 'done',
              page_url=os.environ['SEO_PAGE_URL'])
"

    echo ""
    echo "=== Page generated for: $KEYWORD ==="
else
    if [ "$STATUS" = "skip" ]; then
        echo "Keyword scored below threshold, skipping page generation."
    fi
fi

# --- Report from Postgres ---
echo ""
echo "=== Pipeline Report: $PRODUCT ==="
$DB report "$PRODUCT"
