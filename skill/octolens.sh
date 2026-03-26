#!/usr/bin/env bash
# Octolens mention engagement - find mentions via Octolens and engage
set -euo pipefail
cd ~/social-autoposter

# Load env
set -a; source .env 2>/dev/null || true; set +a

LOG_DIR="skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/octolens-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Octolens Engagement Run: $(date) ===" | tee "$LOG_FILE"

# Find candidates from Octolens API
echo "Fetching Octolens mentions..." | tee -a "$LOG_FILE"
CANDIDATES=$(python3 scripts/octolens_threads.py --from-api --limit 20 2>>"$LOG_FILE")
echo "$CANDIDATES" >> "$LOG_FILE"

CANDIDATE_COUNT=$(echo "$CANDIDATES" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('candidates',[])))" 2>/dev/null || echo "0")
echo "Found $CANDIDATE_COUNT candidates" | tee -a "$LOG_FILE"

if [ "$CANDIDATE_COUNT" = "0" ]; then
    echo "No new candidates to engage with." | tee -a "$LOG_FILE"
    exit 0
fi

# Run Claude with the social-autoposter skill to engage
echo "Starting Claude engagement..." | tee -a "$LOG_FILE"
echo "$CANDIDATES" | claude -p "You are running the social-autoposter Octolens engagement workflow.

Here are the Octolens mention candidates (JSON):
$(echo "$CANDIDATES")

IMPORTANT: This is the Octolens engagement pipeline.
- Max 10 Octolens-sourced posts per run
- Check: SELECT COUNT(*) FROM posts WHERE source_summary LIKE '%octolens%' AND posted_at >= NOW() - INTERVAL '24 hours'
- If >= 100 octolens posts in 24h, stop. Otherwise proceed.

Pick the BEST 5-10 candidates to engage with. Prioritize:
1. buy_intent or product_question tags (someone looking for a solution)
2. Negative competitor mentions on Reddit (opportunity to suggest alternative)
3. High-follower authors on Twitter/X
4. Reddit threads with active discussion
5. Skip tweets that are just replies to other tweets (low visibility)
6. Skip [removed] or empty content posts

For each picked candidate, follow the standard social-autoposter posting flow:
- Read the full thread/post via browser to understand context
- Draft a natural comment following content_angle from config.json
- Post via browser automation (reddit-agent for Reddit, twitter-agent for Twitter, etc.)
- Log to the posts table with source_summary = 'octolens: [keyword]'

Skip if nothing fits naturally. Config is at ~/social-autoposter/config.json" 2>&1 | tee -a "$LOG_FILE"

echo "=== Done: $(date) ===" | tee -a "$LOG_FILE"
