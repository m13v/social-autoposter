#!/usr/bin/env bash
# stats.sh — Fetch Reddit engagement stats via public JSON API and update the DB.
# Usage: bash stats.sh [--quiet]
# Requires: curl, jq, sqlite3

set -euo pipefail

DB="$HOME/social-autoposter/social_posts.db"
LOG_DIR="$HOME/.claude/skills/social-autoposter/logs"
UA="social-stats/1.0 (u/Deep_Ad1959)"
QUIET="${1:-}"

mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/stats-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOGFILE"; }
log_quiet() { echo "[$(date +%H:%M:%S)] $*" >> "$LOGFILE"; }

log "Starting Reddit stats fetch"

# Query all active Reddit posts with URLs
POSTS=$(sqlite3 "$DB" "SELECT id, our_url, thread_url, upvotes, comments_count FROM posts WHERE platform='reddit' AND status='active' AND our_url IS NOT NULL ORDER BY id;")

if [ -z "$POSTS" ]; then
    log "No active Reddit posts found."
    exit 0
fi

TOTAL=0
UPDATED=0
DELETED=0
REMOVED=0
ERRORS=0

# Collect results for summary table
declare -a RESULTS=()

while IFS='|' read -r id our_url thread_url old_upvotes old_comments; do
    TOTAL=$((TOTAL + 1))

    # Normalize URL: ensure old.reddit.com and strip trailing slash, append .json
    json_url=$(echo "$our_url" | sed 's|www\.reddit\.com|old.reddit.com|; s|/$||').json

    log_quiet "Fetching [$id] $json_url"

    # Fetch the JSON
    response=$(curl -s -A "$UA" --max-time 10 "$json_url" 2>/dev/null) || {
        log "ERROR [$id]: curl failed for $our_url"
        ERRORS=$((ERRORS + 1))
        continue
    }

    # Check if we got valid JSON
    if ! echo "$response" | jq empty 2>/dev/null; then
        log "ERROR [$id]: invalid JSON response for $our_url"
        ERRORS=$((ERRORS + 1))
        continue
    fi

    # Extract comment data (our comment is in .[1].data.children[0].data)
    comment_data=$(echo "$response" | jq -r '.[1].data.children[0].data // empty' 2>/dev/null)

    if [ -z "$comment_data" ]; then
        log "WARN [$id]: no comment data found — may be deleted"
        sqlite3 "$DB" "UPDATE posts SET status='deleted', status_checked_at=datetime('now') WHERE id=$id;"
        DELETED=$((DELETED + 1))
        continue
    fi

    comment_score=$(echo "$comment_data" | jq -r '.score // 0')
    comment_body=$(echo "$comment_data" | jq -r '.body // ""')
    comment_author=$(echo "$comment_data" | jq -r '.author // ""')

    # Detect deleted or removed comments
    if [ "$comment_body" = "[deleted]" ] || [ "$comment_author" = "[deleted]" ]; then
        log "DELETED [$id]: comment was deleted"
        sqlite3 "$DB" "UPDATE posts SET status='deleted', status_checked_at=datetime('now') WHERE id=$id;"
        DELETED=$((DELETED + 1))
        continue
    fi

    if [ "$comment_body" = "[removed]" ]; then
        log "REMOVED [$id]: comment was removed by moderator"
        sqlite3 "$DB" "UPDATE posts SET status='removed', status_checked_at=datetime('now') WHERE id=$id;"
        REMOVED=$((REMOVED + 1))
        continue
    fi

    # Extract thread data (.[0].data.children[0].data)
    thread_score=$(echo "$response" | jq -r '.[0].data.children[0].data.score // 0')
    thread_comments=$(echo "$response" | jq -r '.[0].data.children[0].data.num_comments // 0')
    thread_title=$(echo "$response" | jq -r '.[0].data.children[0].data.title // ""' | cut -c1-60)

    # Build thread engagement JSON
    thread_engagement=$(printf '{"thread_score":%s,"thread_comments":%s}' "$thread_score" "$thread_comments")

    # Update the DB
    sqlite3 "$DB" "UPDATE posts SET upvotes=$comment_score, comments_count=$thread_comments, thread_engagement='$thread_engagement', engagement_updated_at=datetime('now'), status_checked_at=datetime('now') WHERE id=$id;"

    UPDATED=$((UPDATED + 1))

    # Store for summary table
    RESULTS+=("$id|$comment_score|$thread_score|$thread_comments|$thread_title")

    log_quiet "OK [$id]: score=$comment_score thread_score=$thread_score thread_comments=$thread_comments"

    # Rate limit: 1 second between requests
    sleep 1
done <<< "$POSTS"

# Print summary
log ""
log "=== Reddit Stats Summary ==="
log "Total: $TOTAL | Updated: $UPDATED | Deleted: $DELETED | Removed: $REMOVED | Errors: $ERRORS"
log ""

if [ ${#RESULTS[@]} -gt 0 ] && [ "$QUIET" != "--quiet" ]; then
    # Sort by comment score descending and print table
    printf "| %-4s | %-5s | %-7s | %-8s | %-60s |\n" "ID" "Score" "Thread" "Comments" "Title" | tee -a "$LOGFILE"
    printf "|------|-------|---------|----------|--------------------------------------------------------------|\n" | tee -a "$LOGFILE"

    # Sort results by score (field 2) descending
    printf '%s\n' "${RESULTS[@]}" | sort -t'|' -k2 -rn | while IFS='|' read -r id score tscore tcomments title; do
        printf "| %-4s | %-5s | %-7s | %-8s | %-60s |\n" "$id" "$score" "$tscore" "$tcomments" "$title" | tee -a "$LOGFILE"
    done
fi

log ""
log "Done. Log saved to $LOGFILE"

# Sync DB to GitHub for Datasette Lite
cd "$HOME/social-autoposter"
git add social_posts.db
git diff --cached --quiet || git commit -m "stats $(date '+%Y-%m-%d %H:%M')" && git push 2>/dev/null || true
