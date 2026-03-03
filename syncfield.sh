#!/usr/bin/env bash
# syncfield.sh — Sync SQLite → Neon Postgres (idempotent upsert)
# Called after git push in stats.sh and engage.sh
# Requires: sqlite3, psql, DATABASE_URL in .env

set -euo pipefail

DB="$HOME/social-autoposter/social_posts.db"

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "syncfield: DATABASE_URL not set, skipping sync"
    exit 0
fi

TMPDIR="${TMPDIR:-/tmp}"

sync_table() {
    local table="$1"
    local columns="$2"
    local conflict_col="${3:-id}"
    local csv_file="$TMPDIR/syncfield_${table}.csv"

    # Export from SQLite as CSV
    sqlite3 -header -csv "$DB" "SELECT $columns FROM $table;" > "$csv_file"

    local row_count
    row_count=$(wc -l < "$csv_file" | tr -d ' ')
    row_count=$((row_count - 1))  # subtract header

    if [ "$row_count" -le 0 ]; then
        rm -f "$csv_file"
        return
    fi

    # Build column list for SET clause (exclude conflict column)
    local set_clause=""
    IFS=',' read -ra cols <<< "$columns"
    for col in "${cols[@]}"; do
        col=$(echo "$col" | tr -d ' ')
        if [ "$col" != "$conflict_col" ]; then
            if [ -n "$set_clause" ]; then
                set_clause="$set_clause, "
            fi
            set_clause="${set_clause}${col} = EXCLUDED.${col}"
        fi
    done

    # Upsert via temp table + INSERT ON CONFLICT
    psql "$DATABASE_URL" -q <<SQL
CREATE TEMP TABLE _tmp_${table} (LIKE ${table} INCLUDING ALL);
\\copy _tmp_${table}($columns) FROM '$csv_file' WITH (FORMAT csv, HEADER true, NULL '');
INSERT INTO ${table}($columns)
SELECT $columns FROM _tmp_${table}
ON CONFLICT ($conflict_col) DO UPDATE SET $set_clause;
DROP TABLE _tmp_${table};
SQL

    echo "syncfield: synced $table ($row_count rows)"
    rm -f "$csv_file"
}

# Sync each table
sync_table "posts" "id,platform,thread_url,thread_author,thread_author_handle,thread_title,thread_content,thread_engagement,our_url,our_content,our_account,posted_at,discovered_at,status,status_checked_at,engagement_updated_at,upvotes,comments_count,views,source_turn_id,source_summary,top_comment_author,top_comment_content,top_comment_upvotes,top_comment_url"

sync_table "campaigns" "id,name,prompt,platforms,status,max_posts_per_day,posts_made,created_at,updated_at"

sync_table "replies" "id,post_id,platform,their_comment_id,their_author,their_content,their_comment_url,our_reply_id,our_reply_content,our_reply_url,parent_reply_id,moltbook_post_uuid,moltbook_parent_comment_uuid,depth,status,skip_reason,discovered_at,replied_at"

sync_table "thread_comments" "id,thread_id,author,author_handle,content,engagement,discovered_at"

# Update sync timestamp
psql "$DATABASE_URL" -q -c "INSERT INTO _syncfield_meta (key, value) VALUES ('last_sync', NOW()::text) ON CONFLICT (key) DO UPDATE SET value = NOW()::text;"

echo "syncfield: sync complete at $(date '+%Y-%m-%d %H:%M:%S')"
