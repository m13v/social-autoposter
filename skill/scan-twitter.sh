#!/bin/bash
# scan-twitter.sh — Lightweight Twitter thread scanner
# Searches Twitter via browser agent, extracts raw tweet data,
# enriches with fxtwitter (follower counts, views),
# scores and upserts into twitter_candidates table.
# Called by launchd every 10 minutes.
# Does NOT post anything.

set -uo pipefail

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/scan-twitter-$(date +%Y-%m-%d_%H%M%S).log"
RAW_FILE="/tmp/twitter_scan_raw_$(date +%s).json"

[ -f "$REPO_DIR/.env" ] && source "$REPO_DIR/.env"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOGFILE"; }

log "=== Twitter Scan: $(date) ==="

# Load all projects + distribution for LLM-driven topic selection
ALL_PROJECTS_JSON=$(python3 -c "
import json, os
config = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
print(json.dumps([{k: p.get(k) for k in ['name', 'description', 'topics', 'twitter_topics', 'weight'] if p.get(k)} for p in config.get('projects', []) if p.get('weight', 0) > 0], indent=2))
" 2>/dev/null || echo "[]")

PROJECT_DIST=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform twitter --distribution 2>/dev/null || echo "(unavailable)")

log "LLM-driven topic selection across all projects"

# Claude prompt: LLM picks search queries, searches Twitter, extracts raw tweet data
claude -p "You are a Twitter thread scanner. Your ONLY job is to search Twitter and extract tweet data. Do NOT post anything.

## Step 1: Choose 4-6 search queries
Based on the projects below and today's posting distribution, pick 4-6 Twitter search queries that will find high-engagement threads where we can add value. Prioritize underrepresented projects.

Available projects:
$ALL_PROJECTS_JSON

Today's distribution:
$PROJECT_DIST

Guidelines for choosing queries:
- Mix project-specific queries (e.g. 'voice AI restaurant') with broader queries that multiple projects could match (e.g. 'Claude Code', 'AI agent')
- Favor queries that find discussions and opinions, not news or promos
- Use natural language queries that real people would tweet about
- Add 'min_faves:10' to filter for engagement
- Avoid queries that return non-English noise unless a project specifically targets non-English markets

## Step 2: Search and extract
For EACH query you chose:
1. Navigate to: https://x.com/search?q={your_query} -filter:replies&f=live
   Use mcp__twitter-agent__browser_navigate
2. Wait 4 seconds, then run this JavaScript via mcp__twitter-agent__browser_run_code to extract tweets:

async (page) => {
  await page.waitForTimeout(3000);
  const tweets = await page.evaluate(() => {
    const results = [];
    for (const article of [...document.querySelectorAll('article[data-testid=\"tweet\"]')].slice(0, 5)) {
      try {
        let handle = '';
        for (const link of article.querySelectorAll('a[role=\"link\"]')) {
          const href = link.getAttribute('href');
          if (href && href.startsWith('/') && !href.includes('/status/') && !href.includes('/search') && href.length > 1 && href.split('/').length === 2) {
            handle = href.replace('/', ''); break;
          }
        }
        const tweetText = article.querySelector('[data-testid=\"tweetText\"]');
        const text = tweetText ? tweetText.textContent.substring(0, 300) : '';
        const timeEl = article.querySelector('time');
        const timeParent = timeEl ? timeEl.closest('a') : null;
        const tweetUrl = timeParent ? 'https://x.com' + timeParent.getAttribute('href') : '';
        const datetime = timeEl ? timeEl.getAttribute('datetime') : '';
        let replies=0, retweets=0, likes=0, views=0, bookmarks=0;
        for (const btn of article.querySelectorAll('[role=\"group\"] button')) {
          const al = btn.getAttribute('aria-label') || '';
          let m;
          if (m=al.match(/([\d,]+)\s*repl/i)) replies=parseInt(m[1].replace(/,/g,''));
          if (m=al.match(/([\d,]+)\s*repost/i)) retweets=parseInt(m[1].replace(/,/g,''));
          if (m=al.match(/([\d,]+)\s*like/i)) likes=parseInt(m[1].replace(/,/g,''));
          if (m=al.match(/([\d,]+)\s*view/i)) views=parseInt(m[1].replace(/,/g,''));
          if (m=al.match(/([\d,]+)\s*bookmark/i)) bookmarks=parseInt(m[1].replace(/,/g,''));
        }
        results.push({handle, text, tweetUrl, datetime, replies, retweets, likes, views, bookmarks});
      } catch(e) {}
    }
    return results;
  });
  return JSON.stringify(tweets);
}

3. After ALL topics are searched, combine ALL extracted tweets into a single JSON array and output it.

CRITICAL RULES:
- Use ONLY mcp__twitter-agent__* tools
- Do NOT post, reply, like, or interact with any tweet
- Do NOT generate any content
- Output the final combined JSON array at the end of your response, wrapped in a code block tagged \`\`\`json
- If a search fails or times out, skip it and continue to the next topic
- Add a 'search_topic' field to each tweet with the query that found it
- Add a 'matched_project' field with the project name you think best fits each tweet (from the projects list above)" 2>&1 | tee -a "$LOGFILE" | python3 -c "
import sys, json, re

# Extract JSON from Claude output
text = sys.stdin.read()
matches = re.findall(r'\`\`\`json\s*(\[.*?\])\s*\`\`\`', text, re.DOTALL)
if matches:
    # Take the last JSON block (final combined output)
    tweets = json.loads(matches[-1])
    json.dump(tweets, open('$RAW_FILE', 'w'))
    print(f'Extracted {len(tweets)} tweets to $RAW_FILE', file=sys.stderr)
else:
    # Try to find any JSON array in the output
    m = re.search(r'\[[\s\S]*\"tweetUrl\"[\s\S]*\]', text)
    if m:
        try:
            tweets = json.loads(m.group())
            json.dump(tweets, open('$RAW_FILE', 'w'))
            print(f'Extracted {len(tweets)} tweets to $RAW_FILE', file=sys.stderr)
        except:
            print('No valid JSON found in output', file=sys.stderr)
            exit(1)
    else:
        print('No tweet data found in output', file=sys.stderr)
        exit(1)
" 2>&1 | tee -a "$LOGFILE"

EXTRACT_EXIT=$?

if [ "$EXTRACT_EXIT" -eq 0 ] && [ -f "$RAW_FILE" ]; then
    log "Enriching with fxtwitter data..."
    cat "$RAW_FILE" \
        | python3 "$REPO_DIR/scripts/enrich_twitter_candidates.py" \
        | python3 "$REPO_DIR/scripts/score_twitter_candidates.py" \
        2>&1 | tee -a "$LOGFILE"
    rm -f "$RAW_FILE"
else
    log "No tweets extracted, skipping enrichment"
fi

# Always expire old candidates
python3 "$REPO_DIR/scripts/score_twitter_candidates.py" --expire-only 2>&1 | tee -a "$LOGFILE"

log "=== Scan complete: $(date) ==="
find "$LOG_DIR" -name "scan-twitter-*.log" -mtime +3 -delete 2>/dev/null || true
