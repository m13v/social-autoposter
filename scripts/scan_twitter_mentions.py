#!/usr/bin/env python3
"""Scan Twitter mentions via API and insert new replies into the database.

Replaces browser-based Phase A mention discovery with API calls.
No LLM, no browser needed — pure Python + tweepy.

Usage:
    python3 scripts/scan_twitter_mentions.py
    python3 scripts/scan_twitter_mentions.py --max 50
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod
import twitter_api

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")
MIN_WORDS = 3


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def word_count(text):
    return len(text.split()) if text else 0


def get_existing_reply_ids(conn):
    """Get set of tweet IDs already tracked in replies table."""
    rows = conn.execute(
        "SELECT their_comment_id FROM replies WHERE platform='x'"
    ).fetchall()
    return {(row[0] if isinstance(row, (list, tuple)) else row["their_comment_id"]) for row in rows}


def get_our_posts(conn):
    """Get our twitter posts for matching. Returns dict of tweet_id -> post row."""
    rows = conn.execute(
        "SELECT id, our_url, our_content, thread_url, project_name FROM posts WHERE platform='twitter' AND status='active'"
    ).fetchall()
    posts = {}
    for row in rows:
        if isinstance(row, (list, tuple)):
            url = row[1]
        else:
            url = row["our_url"]
        # Extract tweet ID from URL
        if not url:
            continue
        parts = url.rstrip("/").split("/")
        if parts:
            tweet_id = parts[-1]
            posts[tweet_id] = row
    return posts


def guess_project(text, config):
    """Guess which project a mention relates to based on topic keywords."""
    projects = config.get("projects", [])
    text_lower = text.lower()
    for p in projects:
        name = p.get("name", "")
        topics = p.get("twitter_topics", []) + p.get("topics", [])
        for topic in topics:
            if topic.lower() in text_lower:
                return name
        if name.lower() in text_lower:
            return name
    return config.get("default_project", "General")


def main():
    parser = argparse.ArgumentParser(description="Scan Twitter mentions via API")
    parser.add_argument("--max", type=int, default=50, help="Max mentions to fetch")
    args = parser.parse_args()

    config = load_config()
    exclusions = config.get("exclusions", {})
    excluded_accounts = {a.lower() for a in exclusions.get("twitter_accounts", [])}
    excluded_accounts.add("m13v_")

    conn = dbmod.get_conn()
    existing_ids = get_existing_reply_ids(conn)
    our_posts = get_our_posts(conn)

    # Get our user ID
    me = twitter_api.get_me()
    my_user_id = str(me.id)

    # Fetch mentions
    mentions = twitter_api.get_mentions(me.id, max_results=args.max)

    new_count = 0
    skipped_count = 0

    for mention in mentions:
        tweet_id = mention["id"]

        # Skip already tracked
        if tweet_id in existing_ids:
            skipped_count += 1
            continue

        # Skip excluded authors
        if mention["author_username"].lower() in excluded_accounts:
            skipped_count += 1
            continue

        # Skip our own tweets
        if mention["author_id"] == my_user_id:
            skipped_count += 1
            continue

        # Skip very short replies (single words, emojis)
        if word_count(mention["text"]) < MIN_WORDS:
            skipped_count += 1
            continue

        # Find matching post
        replied_to = mention.get("replied_to_id")
        post_id = None

        if replied_to and replied_to in our_posts:
            post_row = our_posts[replied_to]
            post_id = post_row[0] if isinstance(post_row, (list, tuple)) else post_row["id"]
        else:
            # Check conversation_id
            conv_id = mention.get("conversation_id", "")
            if conv_id in our_posts:
                post_row = our_posts[conv_id]
                post_id = post_row[0] if isinstance(post_row, (list, tuple)) else post_row["id"]

        # If no matching post, create a stub post entry
        if not post_id:
            project = guess_project(mention["text"], config)
            tweet_url = mention["url"]
            cur = conn.execute(
                """INSERT INTO posts (platform, thread_url, thread_author, thread_title,
                   our_url, our_content, our_account, project_name, status, posted_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()) RETURNING id""",
                ("twitter", tweet_url, mention["author_username"],
                 mention["text"][:100], tweet_url, "(mention - no original post)",
                 "m13v_", project, "active")
            )
            post_id = cur.fetchone()[0]
            conn.commit()

        # Insert the reply
        conn.execute(
            """INSERT INTO replies (post_id, platform, their_comment_id, their_author,
               their_content, their_comment_url, depth, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (post_id, "x", tweet_id, mention["author_username"],
             mention["text"], mention["url"], 1, "pending")
        )
        conn.commit()
        new_count += 1
        print(f"  NEW: @{mention['author_username']}: {mention['text'][:80]}")

    conn.close()

    print(f"\nSummary: {new_count} new mentions inserted, {skipped_count} skipped "
          f"(already tracked/excluded/too short)")


if __name__ == "__main__":
    main()
