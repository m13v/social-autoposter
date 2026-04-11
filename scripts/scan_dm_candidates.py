#!/usr/bin/env python3
"""Scan replies table for users worth DMing across all platforms.

Criteria for DM candidates:
- User replied to our post/comment with a substantive comment (status='replied', meaning we already engaged publicly)
- We haven't already DM'd this user for this reply
- User isn't in exclusion list
- Comment has enough substance (>10 words) to continue the conversation
- Not a bot or deleted account
- Post is recent enough (last 7 days)

Supports: Reddit, LinkedIn, Twitter/X

Usage:
    python3 scripts/scan_dm_candidates.py [--dry-run] [--max N] [--platform reddit|linkedin|x|all]
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
DEFAULT_MAX_CANDIDATES = 100
PLATFORMS = ["reddit", "linkedin", "x"]


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def word_count(text):
    return len(text.split()) if text else 0


def get_excluded_authors(config, platform):
    """Build excluded authors set for a given platform."""
    excluded = {a.lower() for a in config.get("exclusions", {}).get("authors", [])}
    excluded.add("automoderator")
    excluded.add("[deleted]")

    if platform == "reddit":
        reddit_account = config.get("accounts", {}).get("reddit", {}).get("username", "")
        if reddit_account:
            excluded.add(reddit_account.lower())
    elif platform == "linkedin":
        linkedin_name = config.get("accounts", {}).get("linkedin", {}).get("name", "")
        if linkedin_name:
            excluded.add(linkedin_name.lower())
        for p in config.get("exclusions", {}).get("linkedin_profiles", []):
            excluded.add(p.lower())
    elif platform == "x":
        twitter_handle = config.get("accounts", {}).get("twitter", {}).get("handle", "").lstrip("@")
        if twitter_handle:
            excluded.add(twitter_handle.lower())
        for t in config.get("exclusions", {}).get("twitter_accounts", []):
            excluded.add(t.lower())

    return excluded


def scan_platform(conn, config, platform, max_candidates, dry_run):
    """Scan for DM candidates on a single platform."""
    excluded = get_excluded_authors(config, platform)

    candidates = conn.execute("""
        SELECT r.id as reply_id, r.post_id, r.platform, r.their_author, r.their_content,
               r.their_comment_url, r.depth,
               r.our_reply_content, r.our_reply_url,
               p.thread_title, p.our_content as our_post_content,
               p.thread_url, p.our_url,
               r.replied_at
        FROM replies r
        JOIN posts p ON r.post_id = p.id
        LEFT JOIN dms d ON d.reply_id = r.id AND d.platform = %s
        WHERE r.status = 'replied'
          AND r.platform = %s
          AND r.our_reply_content IS NOT NULL
          AND r.our_reply_content != ''
          AND d.id IS NULL
          AND r.replied_at >= NOW() - INTERVAL '%s days'
        ORDER BY r.replied_at DESC
    """, (platform, platform, MAX_AGE_DAYS)).fetchall()

    inserted = 0
    skipped_reasons = {}

    for row in candidates:
        if inserted >= max_candidates:
            break

        author = row["their_author"] or ""
        content = row["their_content"] or ""

        # Skip excluded authors
        if author.lower() in excluded:
            reason = "excluded_author"
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
            continue

        # Skip low-substance comments
        if word_count(content) < MIN_WORDS:
            reason = "too_short"
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
            continue

        # Skip if we've already DM'd this user in the last 30 days (any reply, any platform)
        recent_dm = conn.execute("""
            SELECT COUNT(*) FROM dms
            WHERE their_author = %s AND platform = %s
              AND (status = 'sent' OR status = 'pending')
              AND discovered_at >= NOW() - INTERVAL '30 days'
        """, (author, platform)).fetchone()

        if recent_dm[0] > 0:
            reason = "already_dmd_recently"
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
            continue

        # Build comment context for the DM
        context = f"Thread: {row['thread_title'] or 'N/A'}\n"
        context += f"Their comment: {content[:500]}\n"
        context += f"Our reply: {(row['our_reply_content'] or '')[:500]}"

        if dry_run:
            print(f"  [{platform}] CANDIDATE: {author} (reply #{row['reply_id']})")
            print(f"    Their comment: {content[:100]}...")
            print(f"    Our reply: {(row['our_reply_content'] or '')[:100]}...")
            print()
            inserted += 1
            continue

        conn.execute("""
            INSERT INTO dms (platform, reply_id, post_id, their_author, their_content,
                             comment_context, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            ON CONFLICT (platform, their_author, reply_id) DO NOTHING
        """, (platform, row["reply_id"], row["post_id"], author, content, context))
        conn.commit()
        inserted += 1
        print(f"  [{platform}] NEW DM candidate: {author} (reply #{row['reply_id']}): {content[:80]}...")

    if skipped_reasons:
        skip_summary = ", ".join(f"{k}={v}" for k, v in skipped_reasons.items())
        print(f"  [{platform}] Skipped: {skip_summary}")

    return inserted


def main():
    parser = argparse.ArgumentParser(description="Find users worth DMing based on comment engagement")
    parser.add_argument("--dry-run", action="store_true", help="Print candidates without inserting")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_CANDIDATES, help="Max candidates per platform")
    parser.add_argument("--platform", default="all", choices=PLATFORMS + ["all"],
                        help="Platform to scan (default: all)")
    args = parser.parse_args()

    config = load_config()
    dbmod.load_env()
    conn = dbmod.get_conn()

    platforms = PLATFORMS if args.platform == "all" else [args.platform]
    total = 0

    for platform in platforms:
        print(f"\nScanning {platform} for DM candidates...")
        count = scan_platform(conn, config, platform, args.max, args.dry_run)
        total += count

    conn.close()
    action = "found" if args.dry_run else "queued"
    print(f"\nDM scan complete: {total} candidates {action} across {', '.join(platforms)}")
    return total


if __name__ == "__main__":
    count = main()
    sys.exit(0 if count > 0 else 1)
