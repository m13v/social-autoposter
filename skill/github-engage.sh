#!/usr/bin/env bash
# github-engage.sh — GitHub Issues engagement loop
# Scan our GitHub issue comments for replies, respond to substantive ones.
# Called by launchd every 6 hours.

set -euo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/skill/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/github-engage-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== GitHub Engagement Run: $(date) ==="

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

# ═══════════════════════════════════════════════════════
# PHASE A: Scan for replies to our GitHub comments
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning GitHub issues for replies..."
python3 "$REPO_DIR/scripts/scan_github_replies.py" 2>&1 | tee -a "$LOG_FILE"

# ═══════════════════════════════════════════════════════
# PHASE B: Respond to pending GitHub replies
# ═══════════════════════════════════════════════════════
PENDING_COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='github_issues' AND status='pending';")

if [ "$PENDING_COUNT" -eq 0 ]; then
    log "Phase B: No pending GitHub replies. Done!"
    exit 0
fi

log "Phase B: $PENDING_COUNT pending GitHub replies to process"

PENDING_DATA=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(q) FROM (
        SELECT r.id, r.platform, r.their_author,
               LEFT(r.their_content, 500) as their_content,
               r.their_comment_url, r.their_comment_id, r.depth,
               LEFT(p.thread_title, 100) as thread_title,
               p.thread_url, LEFT(p.our_content, 300) as our_content, p.our_url
        FROM replies r
        JOIN posts p ON r.post_id = p.id
        WHERE r.platform='github_issues' AND r.status='pending'
        ORDER BY r.discovered_at ASC
        LIMIT 50
    ) q;")

# Load exclusions
EXCLUDED_AUTHORS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('authors',[])))" 2>/dev/null || echo "")
EXCLUDED_REPOS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('github_repos',[])))" 2>/dev/null || echo "")

timeout 1800 claude -p "You are the Social Autoposter GitHub engagement bot.

Read $SKILL_FILE for content rules (especially: NEVER use em dashes).
Also read $REPO_DIR/config.json for projects and their channel_links.

EXCLUSIONS — do NOT engage with these (skip and mark as 'skipped' with reason 'excluded_author' or 'excluded_repo'):
- Excluded authors: $EXCLUDED_AUTHORS
- Excluded repos/orgs: $EXCLUDED_REPOS

## Respond to GitHub issue replies

These are replies from other users to our GitHub issue comments. Respond to each one.

### Rules:
1. Be genuine and conversational. Continue the technical discussion.
2. If they asked a question, answer it directly.
3. If they thanked us, be brief and warm. Ask a follow-up question about their use case.
4. If they're interested in our project, use the channel link (e.g. fazm.ai/gh) for general mentions, direct GitHub file links only for specific code references.
5. NEVER use em dashes. Use commas, periods, or regular dashes (-).
6. Keep replies concise - match the energy of their message.

### How to post:
Extract owner/repo and issue number from thread_url, then:
  gh issue comment NUMBER -R OWNER/REPO --body \"...\"

### How to log:
For each reply, use the helper script:
  python3 $REPO_DIR/scripts/reply_db.py replied ID \"reply text\" [url]
  python3 $REPO_DIR/scripts/reply_db.py skipped ID \"reason\"

For light acknowledgments (just 'thanks', emoji reactions, etc), skip them:
  python3 $REPO_DIR/scripts/reply_db.py skipped ID \"light acknowledgment\"

### Replies to process:
$PENDING_DATA

Process EVERY reply in this batch." --max-turns 100 2>&1 | tee -a "$LOG_FILE"

# ═══════════════════════════════════════════════════════
# PHASE C: Summary
# ═══════════════════════════════════════════════════════
TOTAL_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='github_issues' AND status='pending';")
TOTAL_REPLIED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='github_issues' AND status='replied';")
TOTAL_SKIPPED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='github_issues' AND status='skipped';")

log "GitHub replies summary: pending=$TOTAL_PENDING replied=$TOTAL_REPLIED skipped=$TOTAL_SKIPPED"
log "=== GitHub Engagement complete: $(date) ==="

# Clean up old logs
find "$LOG_DIR" -name "github-engage-*.log" -mtime +7 -delete 2>/dev/null || true
