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

# Pick 2-3 projects to scan for (rotate via pick_project)
PROJECT1=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform twitter 2>/dev/null || echo "Fazm")
PROJECT2=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform twitter 2>/dev/null || echo "Assrt")
log "Scanning for projects: $PROJECT1, $PROJECT2"

# Get search topics for these projects
TOPICS_JSON=$(python3 -c "
import json, os
config = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
projects = {p['name']: p for p in config.get('projects', [])}
topics = []
for name in ['$PROJECT1', '$PROJECT2']:
    p = projects.get(name, {})
    for t in p.get('topics', [])[:3]:
        topics.append({'topic': t, 'project': name})
# Always include high-signal general topics
topics.append({'topic': 'Claude Code', 'project': 'Assrt'})
topics.append({'topic': 'AI agent', 'project': 'Fazm'})
print(json.dumps(topics))
" 2>/dev/null || echo "[]")

log "Topics: $TOPICS_JSON"

# Claude prompt: search Twitter, extract raw tweet data, output JSON
# This is a minimal prompt: no posting, no content generation, just data extraction
claude -p "You are a Twitter thread scanner. Your ONLY job is to search Twitter and extract tweet data. Do NOT post anything.

For each search topic below, navigate to Twitter search and extract tweet data.

TOPICS: $TOPICS_JSON

For EACH topic:
1. Navigate to: https://x.com/search?q={topic} min_faves:10 -filter:replies&f=live
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
- Add a 'search_topic' field to each tweet with the topic that found it" 2>&1 | tee -a "$LOGFILE" | python3 -c "
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
