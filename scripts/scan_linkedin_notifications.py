#!/usr/bin/env python3
"""Scan LinkedIn notifications and insert new replies into the database.

Replaces browser-based Phase A LinkedIn discovery with a single JS call
via the linkedin-agent browser. No Claude LLM tokens needed for discovery.

Usage (standalone test):
    python3 scripts/scan_linkedin_notifications.py --json-file /tmp/notifs.json

In the pipeline, engage-linkedin.sh calls this after running the JS extractor
via mcp__linkedin-agent__browser_run_code.
"""

import argparse
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def get_existing_comment_ids(conn):
    """Get set of LinkedIn comment URNs already tracked in replies table."""
    rows = conn.execute(
        "SELECT their_comment_id FROM replies WHERE platform='linkedin'"
    ).fetchall()
    return {
        (row[0] if isinstance(row, (list, tuple)) else row["their_comment_id"])
        for row in rows
    }


def get_existing_author_post_pairs(conn):
    """Get set of (author, post_url) pairs already engaged."""
    rows = conn.execute(
        """SELECT DISTINCT r.their_author || '|||' || p.our_url
           FROM replies r JOIN posts p ON r.post_id = p.id
           WHERE r.platform='linkedin'
             AND r.status IN ('replied', 'pending', 'processing')"""
    ).fetchall()
    return {
        (row[0] if isinstance(row, (list, tuple)) else row[0])
        for row in rows
    }


def get_our_posts(conn):
    """Get our LinkedIn posts. Returns dict of activity_id -> post row."""
    rows = conn.execute(
        "SELECT id, our_url, thread_url, thread_title, project_name "
        "FROM posts WHERE platform='linkedin' AND status='active'"
    ).fetchall()
    posts = {}
    for row in rows:
        url = row[1] if isinstance(row, (list, tuple)) else row["our_url"]
        if not url:
            continue
        # Extract activity ID from URL
        m = re.search(r'activity[:%]3A(\d+)', url)
        if m:
            posts[m.group(1)] = row
        # Also try ugcPost
        m = re.search(r'ugcPost[:%]3A(\d+)', url)
        if m:
            posts[m.group(1)] = row
    return posts


def guess_project(text, config):
    """Guess which project a notification relates to."""
    projects = config.get("projects", [])
    text_lower = text.lower()
    for p in projects:
        name = p.get("name", "")
        topics = p.get("linkedin_topics", []) + p.get("topics", [])
        for topic in topics:
            if topic.lower() in text_lower:
                return name
        if name.lower() in text_lower:
            return name
    return config.get("default_project", "General")


def build_comment_url(activity_id, comment_urn):
    """Build a LinkedIn permalink URL for a comment."""
    if not activity_id:
        return ""
    base = f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}"
    if comment_urn:
        encoded = urllib.parse.quote(comment_urn, safe="")
        return f"{base}?commentUrn={encoded}"
    return base


def process_notifications(notifications, conn, config):
    """Process notifications and insert new ones into the DB."""
    exclusions = config.get("exclusions", {})
    excluded_authors = {a.lower() for a in exclusions.get("authors", [])}
    excluded_profiles = {p.lower() for p in exclusions.get("linkedin_profiles", [])}
    our_names = {"matthew diakonov", "m13v", "matt diakonov"}

    existing_ids = get_existing_comment_ids(conn)
    author_post_pairs = get_existing_author_post_pairs(conn)
    our_posts = get_our_posts(conn)

    stats = {
        "new": 0,
        "already_tracked": 0,
        "excluded_author": 0,
        "own_account": 0,
        "author_already_engaged": 0,
        "no_comment_urn": 0,
    }

    for notif in notifications:
        comment_urn = notif.get("commentUrn", "")
        author_name = notif.get("authorName", "")
        activity_id = notif.get("activityId", "")
        comment_text = notif.get("commentText", "")
        post_content = notif.get("postContent", "")
        nav_url = notif.get("navigationUrl", "")
        headline = notif.get("headline", "")
        profile_url = notif.get("profileUrl", "")
        notif_type = notif.get("type", "")

        # Skip if no comment URN (can't track it)
        if not comment_urn:
            stats["no_comment_urn"] += 1
            continue

        # Skip already tracked
        if comment_urn in existing_ids:
            stats["already_tracked"] += 1
            continue

        # Skip own account
        if author_name.lower() in our_names:
            stats["own_account"] += 1
            continue

        # Skip excluded authors
        author_lower = author_name.lower()
        profile_lower = profile_url.lstrip("/").lower()
        if author_lower in excluded_authors or profile_lower in excluded_profiles:
            stats["excluded_author"] += 1
            continue

        # Find matching post
        post_id = None
        post_url = ""

        if activity_id and activity_id in our_posts:
            post_row = our_posts[activity_id]
            post_id = post_row[0] if isinstance(post_row, (list, tuple)) else post_row["id"]
            post_url = post_row[1] if isinstance(post_row, (list, tuple)) else post_row["our_url"]

        # Check author+post dedup
        if post_url:
            pair_key = f"{author_name}|||{post_url}"
            if pair_key in author_post_pairs:
                stats["author_already_engaged"] += 1
                print(f"  SKIP (author already engaged): {author_name} on activity {activity_id}")
                continue

        # If no matching post, create a stub
        if not post_id:
            combined_text = f"{comment_text} {post_content}"
            project = guess_project(combined_text, config)
            feed_url = build_comment_url(activity_id, "")
            if not feed_url:
                feed_url = f"https://www.linkedin.com{nav_url}" if nav_url else ""

            cur = conn.execute(
                """INSERT INTO posts (platform, thread_url, thread_author, thread_title,
                   our_url, our_content, our_account, project_name, status, posted_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()) RETURNING id""",
                (
                    "linkedin",
                    feed_url,
                    author_name,
                    (post_content or headline)[:100],
                    feed_url,
                    "(notification - no original post)",
                    "m13v",
                    project,
                    "active",
                ),
            )
            post_id = cur.fetchone()[0]
            conn.commit()

        # Build comment permalink
        comment_url = build_comment_url(activity_id, comment_urn)
        if not comment_url and nav_url:
            comment_url = f"https://www.linkedin.com{nav_url}"

        # Determine depth based on notification type
        depth = 1
        if notif_type == "REPLIED_TO_YOUR_COMMENT":
            depth = 2  # reply to our comment

        # Insert the reply
        conn.execute(
            """INSERT INTO replies (post_id, platform, their_comment_id, their_author,
               their_content, their_comment_url, depth, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                post_id,
                "linkedin",
                comment_urn,
                author_name,
                comment_text or headline,
                comment_url,
                depth,
                "pending",
            ),
        )
        conn.commit()
        stats["new"] += 1
        existing_ids.add(comment_urn)  # prevent dupes within this run
        print(f"  NEW: {author_name}: {(comment_text or headline)[:80]}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Process LinkedIn notification data and insert into DB"
    )
    parser.add_argument(
        "--json-file",
        required=True,
        help="Path to JSON file with notification data from JS extractor",
    )
    args = parser.parse_args()

    with open(args.json_file) as f:
        data = json.load(f)

    if "error" in data:
        print(f"ERROR from JS extractor: {data['error']}", file=sys.stderr)
        sys.exit(1)

    notifications = data.get("notifications", [])
    print(f"Processing {len(notifications)} actionable notifications...")

    config = load_config()
    conn = dbmod.get_conn()
    stats = process_notifications(notifications, conn, config)
    conn.close()

    print(f"\nSummary: {stats['new']} new, "
          f"{stats['already_tracked']} already tracked, "
          f"{stats['excluded_author']} excluded, "
          f"{stats['own_account']} own account, "
          f"{stats['author_already_engaged']} author already engaged, "
          f"{stats['no_comment_urn']} no comment URN")


if __name__ == "__main__":
    main()
