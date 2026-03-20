#!/usr/bin/env python3
"""Scan replies table for users worth DMing on Reddit.

Criteria for DM candidates:
- User replied to our post/comment with a substantive comment (status='replied', meaning we already engaged publicly)
- We haven't already DM'd this user for this reply
- User isn't in exclusion list
- Comment has enough substance (>10 words) to continue the conversation
- Not a bot or deleted account
- Post is recent enough (last 7 days)

Usage:
    python3 scripts/scan_dm_candidates.py [--dry-run] [--max N]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")
MIN_WORDS = 10
MAX_AGE_DAYS = 7
DEFAULT_MAX_CANDIDATES = 5


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def word_count(text):
    return len(text.split()) if text else 0


def main():
    parser = argparse.ArgumentParser(description="Find Reddit users worth DMing based on comment engagement")
    parser.add_argument("--dry-run", action="store_true", help="Print candidates without inserting")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_CANDIDATES, help="Max candidates per run")
    args = parser.parse_args()

    config = load_config()
    reddit_account = config.get("accounts", {}).get("reddit", {}).get("username", "")
    excluded_authors = {a.lower() for a in config.get("exclusions", {}).get("authors", [])}
    excluded_authors.add(reddit_account.lower())
    excluded_authors.add("automoderator")
    excluded_authors.add("[deleted]")

    dbmod.load_env()
    conn = dbmod.get_conn()

    # Find substantive replies where we already responded publicly
    # and haven't DM'd the user for this reply yet
    candidates = conn.execute("""
        SELECT r.id as reply_id, r.post_id, r.their_author, r.their_content,
               r.their_comment_url, r.depth,
               r.our_reply_content, r.our_reply_url,
               p.thread_title, p.our_content as our_post_content,
               p.thread_url, p.our_url,
               r.replied_at
        FROM replies r
        JOIN posts p ON r.post_id = p.id
        LEFT JOIN dms d ON d.reply_id = r.id AND d.platform = 'reddit'
        WHERE r.status = 'replied'
          AND r.platform = 'reddit'
          AND r.our_reply_content IS NOT NULL
          AND r.our_reply_content != ''
          AND d.id IS NULL
          AND r.replied_at >= NOW() - INTERVAL '%s days'
        ORDER BY r.replied_at DESC
        LIMIT %s
    """, (MAX_AGE_DAYS, args.max * 3)).fetchall()  # fetch extra to filter

    inserted = 0
    for row in candidates:
        if inserted >= args.max:
            break

        author = row["their_author"] or ""
        content = row["their_content"] or ""

        # Skip excluded authors
        if author.lower() in excluded_authors:
            continue

        # Skip low-substance comments
        if word_count(content) < MIN_WORDS:
            continue

        # Skip if we've already DM'd this user in the last 30 days (any reply)
        recent_dm = conn.execute("""
            SELECT COUNT(*) FROM dms
            WHERE their_author = %s AND platform = 'reddit'
              AND (status = 'sent' OR status = 'pending')
              AND discovered_at >= NOW() - INTERVAL '30 days'
        """, (author,)).fetchone()

        if recent_dm[0] > 0:
            continue

        # Build comment context for the DM
        context = f"Thread: {row['thread_title'] or 'N/A'}\n"
        context += f"Their comment: {content[:500]}\n"
        context += f"Our reply: {(row['our_reply_content'] or '')[:500]}"

        if args.dry_run:
            print(f"  CANDIDATE: u/{author} (reply #{row['reply_id']})")
            print(f"    Their comment: {content[:100]}...")
            print(f"    Our reply: {(row['our_reply_content'] or '')[:100]}...")
            print()
            inserted += 1
            continue

        conn.execute("""
            INSERT INTO dms (platform, reply_id, post_id, their_author, their_content,
                             comment_context, status)
            VALUES ('reddit', %s, %s, %s, %s, %s, 'pending')
            ON CONFLICT (platform, their_author, reply_id) DO NOTHING
        """, (row["reply_id"], row["post_id"], author, content, context))
        conn.commit()
        inserted += 1
        print(f"  NEW DM candidate: u/{author} (reply #{row['reply_id']}): {content[:80]}...")

    conn.close()
    action = "found" if args.dry_run else "queued"
    print(f"\nDM scan complete: {inserted} candidates {action}")
    return inserted


if __name__ == "__main__":
    count = main()
    sys.exit(0 if count > 0 else 1)
