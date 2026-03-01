#!/usr/bin/env bash
# stats.sh — Fetch Reddit engagement stats via public JSON API and update the DB.
# Usage: bash stats.sh [--quiet]
# Requires: curl, jq, sqlite3

set -euo pipefail

DB="$HOME/social-autoposter/social_posts.db"
LOG_DIR="$HOME/.claude/skills/social-autoposter/logs"
UA="social-stats/1.0 (u/Deep_Ad1959)"
QUIET="${1:-}"

# Load secrets (MOLTBOOK_API_KEY, etc.)
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

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
        # Empty response likely means rate-limiting, not deletion.
        # Only mark as deleted if the author field is explicitly "[deleted]".
        # Retry once after a short pause to distinguish rate-limit from real deletion.
        sleep 3
        response2=$(curl -s -A "$UA" --max-time 10 "$json_url" 2>/dev/null) || { log "WARN [$id]: retry also failed — skipping (NOT marking deleted)"; ERRORS=$((ERRORS + 1)); continue; }
        comment_data=$(echo "$response2" | jq -r '.[1].data.children[0].data // empty' 2>/dev/null)
        if [ -z "$comment_data" ]; then
            log "WARN [$id]: no comment data after retry — skipping (NOT marking deleted)"
            ERRORS=$((ERRORS + 1))
            continue
        fi
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
log "Reddit stats done. Log saved to $LOGFILE"

# ───────────────────────────────────────────────
# Moltbook Stats
# ───────────────────────────────────────────────

log ""
log "Starting Moltbook stats fetch"

if [ -z "${MOLTBOOK_API_KEY:-}" ]; then
    log "WARN: MOLTBOOK_API_KEY not set, skipping Moltbook stats"
else
    MB_POSTS=$(sqlite3 "$DB" "SELECT id, our_url FROM posts WHERE platform='moltbook' AND status='active' AND our_url IS NOT NULL ORDER BY id;")

    if [ -z "$MB_POSTS" ]; then
        log "No active Moltbook posts found."
    else
        MB_TOTAL=0
        MB_UPDATED=0
        MB_DELETED=0
        MB_ERRORS=0
        declare -a MB_RESULTS=()

        while IFS='|' read -r id our_url; do
            MB_TOTAL=$((MB_TOTAL + 1))

            # Extract UUID from URL: https://www.moltbook.com/post/{UUID}
            post_uuid=$(echo "$our_url" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

            if [ -z "$post_uuid" ]; then
                log "ERROR [$id]: could not extract UUID from $our_url"
                MB_ERRORS=$((MB_ERRORS + 1))
                continue
            fi

            log_quiet "Fetching [$id] Moltbook post $post_uuid"

            response=$(curl -s --max-time 10 \
                -H "Authorization: Bearer $MOLTBOOK_API_KEY" \
                "https://www.moltbook.com/api/v1/posts/$post_uuid" 2>/dev/null) || {
                log "ERROR [$id]: curl failed for $our_url"
                MB_ERRORS=$((MB_ERRORS + 1))
                continue
            }

            if ! echo "$response" | jq empty 2>/dev/null; then
                log "ERROR [$id]: invalid JSON response for $our_url"
                MB_ERRORS=$((MB_ERRORS + 1))
                continue
            fi

            success=$(echo "$response" | jq -r '.success // false')
            if [ "$success" != "true" ]; then
                log "ERROR [$id]: API returned success=false for $our_url"
                MB_ERRORS=$((MB_ERRORS + 1))
                continue
            fi

            is_deleted=$(echo "$response" | jq -r '.post.is_deleted // false')
            if [ "$is_deleted" = "true" ]; then
                log "DELETED [$id]: Moltbook post was deleted"
                sqlite3 "$DB" "UPDATE posts SET status='deleted', status_checked_at=datetime('now') WHERE id=$id;"
                MB_DELETED=$((MB_DELETED + 1))
                continue
            fi

            upvotes=$(echo "$response" | jq -r '.post.upvotes // 0')
            comment_count=$(echo "$response" | jq -r '.post.comment_count // 0')
            score=$(echo "$response" | jq -r '.post.score // 0')
            title=$(echo "$response" | jq -r '.post.title // ""' | cut -c1-60)

            thread_engagement=$(printf '{"score":%s,"upvotes":%s,"comment_count":%s}' "$score" "$upvotes" "$comment_count")

            sqlite3 "$DB" "UPDATE posts SET upvotes=$upvotes, comments_count=$comment_count, thread_engagement='$thread_engagement', engagement_updated_at=datetime('now'), status_checked_at=datetime('now') WHERE id=$id;"

            MB_UPDATED=$((MB_UPDATED + 1))
            MB_RESULTS+=("$id|$upvotes|$score|$comment_count|$title")

            log_quiet "OK [$id]: upvotes=$upvotes score=$score comments=$comment_count"

        done <<< "$MB_POSTS"

        log ""
        log "=== Moltbook Stats Summary ==="
        log "Total: $MB_TOTAL | Updated: $MB_UPDATED | Deleted: $MB_DELETED | Errors: $MB_ERRORS"
        log ""

        if [ ${#MB_RESULTS[@]} -gt 0 ] && [ "$QUIET" != "--quiet" ]; then
            printf "| %-4s | %-7s | %-5s | %-8s | %-60s |\n" "ID" "Upvotes" "Score" "Comments" "Title" | tee -a "$LOGFILE"
            printf "|------|---------|-------|----------|--------------------------------------------------------------|\n" | tee -a "$LOGFILE"

            printf '%s\n' "${MB_RESULTS[@]}" | sort -t'|' -k2 -rn | while IFS='|' read -r id upvotes score comments title; do
                printf "| %-4s | %-7s | %-5s | %-8s | %-60s |\n" "$id" "$upvotes" "$score" "$comments" "$title" | tee -a "$LOGFILE"
            done
        fi
    fi
fi

log ""
log "All stats done. Log saved to $LOGFILE"

# Sync DB to GitHub for Datasette Lite
cd "$HOME/social-autoposter"
git add social_posts.db
git diff --cached --quiet || git commit -m "stats $(date '+%Y-%m-%d %H:%M')" && git push 2>/dev/null || true
