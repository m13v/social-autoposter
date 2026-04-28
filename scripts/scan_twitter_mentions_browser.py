#!/usr/bin/env python3
"""Scan Twitter notifications via the browser (no API cost) and insert new replies.

Browser-based replacement for the old API-powered scan_twitter_mentions.py.
Consumes JSON from `twitter_browser.py notifications [scroll] [tab]` which
defaults to the /notifications (All) tab so we catch nested replies where the
@-tag was dropped. Pass tab="mentions" to restrict to explicit @-mentions only.
Companion: scan_twitter_thread_followups.py revisits our recent replies to
pick up depth-2+ follow-ups that never surface in notifications at all.

Usage:
    python3 scripts/twitter_browser.py notifications 8 all > /tmp/twitter_notifs.json
    python3 scripts/scan_twitter_mentions_browser.py --json-file /tmp/twitter_notifs.json
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")
MIN_WORDS = 3
OUR_HANDLE = "m13v_"


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def word_count(text):
    return len(text.split()) if text else 0


def get_existing_reply_ids(conn):
    rows = conn.execute(
        "SELECT their_comment_id FROM replies WHERE platform='x'"
    ).fetchall()
    return {
        (row[0] if isinstance(row, (list, tuple)) else row["their_comment_id"])
        for row in rows
    }


def get_our_posts(conn):
    """Map tweet_id (last URL segment) -> post row for our active twitter posts."""
    rows = conn.execute(
        "SELECT id, our_url, our_content, thread_url, project_name "
        "FROM posts WHERE platform='twitter' AND status='active'"
    ).fetchall()
    posts = {}
    for row in rows:
        url = row[1] if isinstance(row, (list, tuple)) else row["our_url"]
        if not url:
            continue
        m = re.search(r"/status/(\d+)", url)
        if m:
            posts[m.group(1)] = row
    return posts


def guess_project(text, config):
    projects = config.get("projects", [])
    text_lower = (text or "").lower()
    for p in projects:
        name = p.get("name", "")
        # Phase 1 unified seed list, with legacy fields as fallback for
        # pre-migration safety.
        topics = (
            p.get("search_topics", [])
            or (p.get("twitter_topics", []) + p.get("topics", []))
        )
        for topic in topics:
            if topic.lower() in text_lower:
                return name
        if name.lower() in text_lower:
            return name
    return config.get("default_project", "General")


def most_recent_active_project(conn):
    """Project_name of the most recent active twitter post we made.

    Used as a fallback for replies-to-us where the notification feed doesn't
    expose the parent tweet ID, so we can't identify *which* of our posts
    the mention is under. Recency is a much stronger signal than
    keyword-matching a 3-word reply body.
    """
    row = conn.execute(
        "SELECT project_name FROM posts "
        "WHERE platform='twitter' AND status='active' "
        "AND project_name IS NOT NULL AND project_name <> '' "
        "AND our_content <> '(mention - no original post)' "
        "ORDER BY posted_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return row[0] if isinstance(row, (list, tuple)) else row["project_name"]


def process_notifications(notifications, conn, config):
    exclusions = config.get("exclusions", {})
    excluded_accounts = {a.lower() for a in exclusions.get("twitter_accounts", [])}
    excluded_accounts.add(OUR_HANDLE.lower())

    existing_ids = get_existing_reply_ids(conn)
    our_posts = get_our_posts(conn)
    recent_project = most_recent_active_project(conn)

    stats = {
        "new": 0,
        "already_tracked": 0,
        "excluded_author": 0,
        "own_account": 0,
        "too_short": 0,
        "no_tweet_id": 0,
    }

    for n in notifications:
        tweet_id = n.get("tweet_id", "")
        handle = (n.get("handle") or "").lstrip("@")
        text = n.get("text") or ""
        tweet_url = n.get("tweet_url") or (
            f"https://x.com/{handle}/status/{tweet_id}" if handle and tweet_id else ""
        )
        replying_to = (n.get("replying_to") or "").lstrip("@").lower()

        if not tweet_id:
            stats["no_tweet_id"] += 1
            continue

        if tweet_id in existing_ids:
            stats["already_tracked"] += 1
            continue

        if handle.lower() in excluded_accounts:
            stats["own_account" if handle.lower() == OUR_HANDLE.lower() else "excluded_author"] += 1
            continue

        if word_count(text) < MIN_WORDS:
            stats["too_short"] += 1
            continue

        # Try to match to one of our posts: replying_to field hints it's a
        # reply under one of our tweets; otherwise fall back to stub post.
        post_id = None
        post_row = None
        is_reply_to_us = replying_to == OUR_HANDLE.lower() and bool(our_posts)
        # Note: notifications don't expose conversation_id, so we can't link to
        # the specific parent tweet. We still attribute project_name to the
        # right project below by inheriting from our most recent active post.

        if not post_id:
            # Reply-to-us: short reply text is unreliable for keyword matching;
            # inherit the project of our most recent active post instead.
            # Other mentions: fall back to keyword-matching the mention text.
            if is_reply_to_us and recent_project:
                project = recent_project
            else:
                project = guess_project(text, config)
            cur = conn.execute(
                """INSERT INTO posts (platform, thread_url, thread_author, thread_title,
                   our_url, our_content, our_account, project_name, status, posted_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()) RETURNING id""",
                (
                    "twitter",
                    tweet_url,
                    handle,
                    text[:100],
                    tweet_url,
                    "(mention - no original post)",
                    OUR_HANDLE,
                    project,
                    "active",
                ),
            )
            post_id = cur.fetchone()[0]
            conn.commit()

        conn.execute(
            """INSERT INTO replies (post_id, platform, their_comment_id, their_author,
               their_content, their_comment_url, depth, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (post_id, "x", tweet_id, handle, text, tweet_url, 1, "pending"),
        )
        conn.commit()
        stats["new"] += 1
        existing_ids.add(tweet_id)
        print(f"  NEW: @{handle}: {text[:80]}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Process Twitter notification data from browser scanner"
    )
    parser.add_argument(
        "--json-file",
        required=True,
        help="Path to JSON from twitter_browser.py notifications",
    )
    args = parser.parse_args()

    with open(args.json_file) as f:
        data = json.load(f)

    if isinstance(data, dict) and data.get("error"):
        print(f"ERROR from extractor: {data['error']}", file=sys.stderr)
        sys.exit(1)

    notifications = data.get("notifications", []) if isinstance(data, dict) else data
    print(f"Processing {len(notifications)} mentions...")

    config = load_config()
    conn = dbmod.get_conn()
    stats = process_notifications(notifications, conn, config)
    conn.close()

    print(
        f"\nSummary: {stats['new']} new, "
        f"{stats['already_tracked']} already tracked, "
        f"{stats['excluded_author']} excluded, "
        f"{stats['own_account']} own account, "
        f"{stats['too_short']} too short, "
        f"{stats['no_tweet_id']} no tweet_id"
    )


if __name__ == "__main__":
    main()
