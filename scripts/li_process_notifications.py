#!/usr/bin/env python3
"""Process LinkedIn notifications captured via browser_run_code.

Reads /tmp/li_notifications.json (list of {type, author, href, activity_id,
comment_urn, snippet}) and inserts new rows into the replies table for any
notification not already tracked.
"""

import json
import os
import sys
import psycopg2

DB_URL = os.environ["DATABASE_URL"]

EXCLUDED_AUTHORS = {"louis030195", "louis3195"}
OWN_NAMES = {"Matthew Diakonov", "m13v"}

NOTIFS_FILE = "/tmp/li_notifications.json"
EXISTING_COMMENTS_FILE = "/tmp/li_existing_comments.txt"
EXISTING_PAIRS_FILE = "/tmp/li_existing_pairs.txt"
POSTS_FILE = "/tmp/li_posts.txt"


def load_lines(path):
    with open(path) as f:
        return {line.strip() for line in f if line.strip()}


def load_posts():
    posts = []
    with open(POSTS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            pid, our_url = line.split("|", 1)
            posts.append((int(pid), our_url))
    return posts


def find_post_by_activity(posts, activity_id):
    if not activity_id:
        return None
    for pid, our_url in posts:
        if activity_id in our_url:
            return (pid, our_url)
    return None


def main():
    notifs = json.load(open(NOTIFS_FILE))
    existing_comments = load_lines(EXISTING_COMMENTS_FILE)
    existing_pairs = load_lines(EXISTING_PAIRS_FILE)
    posts = load_posts()

    counts = {
        "new": 0,
        "already_tracked": 0,
        "author_already_engaged": 0,
        "excluded": 0,
        "own_account": 0,
        "no_comment_urn": 0,
        "post_created": 0,
    }

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    for n in notifs:
        author = n.get("author") or ""
        comment_urn = n.get("comment_urn")
        activity_id = n.get("activity_id")
        href = n.get("href")
        snippet = (n.get("snippet") or "").strip()

        if not comment_urn or not activity_id:
            counts["no_comment_urn"] += 1
            continue
        if author in OWN_NAMES:
            counts["own_account"] += 1
            continue
        if any(ex.lower() in author.lower() for ex in EXCLUDED_AUTHORS):
            counts["excluded"] += 1
            continue
        if comment_urn in existing_comments:
            counts["already_tracked"] += 1
            continue

        # Find or create post
        match = find_post_by_activity(posts, activity_id)
        if match:
            post_id, our_url = match
        else:
            our_url = f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/"
            cur.execute(
                """
                INSERT INTO posts
                  (platform, thread_url, thread_author, thread_author_handle,
                   thread_title, thread_content, our_url, our_content, our_account,
                   source_summary, project_name, engagement_style, feedback_report_used,
                   status, posted_at)
                VALUES
                  ('linkedin', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   FALSE, 'active', NOW())
                RETURNING id
                """,
                (
                    our_url,                 # thread_url
                    author,                  # thread_author (best we have)
                    "",                      # thread_author_handle
                    "",                      # thread_title
                    snippet[:500],           # thread_content (best we have)
                    our_url,                 # our_url
                    "",                      # our_content (unknown, discovery only)
                    "matthew-autoposter",    # our_account
                    "discovered_from_notifications",  # source_summary
                    "general",               # project_name (topics empty in config)
                    "discovery",             # engagement_style
                ),
            )
            post_id = cur.fetchone()[0]
            posts.append((post_id, our_url))
            counts["post_created"] += 1

        pair_key = f"{author}|||{our_url}"
        if pair_key in existing_pairs:
            counts["author_already_engaged"] += 1
            continue

        cur.execute(
            """
            INSERT INTO replies
              (post_id, platform, their_comment_id, their_author, their_content,
               their_comment_url, depth, status)
            VALUES (%s, 'linkedin', %s, %s, %s, %s, 1, 'pending')
            """,
            (post_id, comment_urn, author, snippet, href),
        )
        existing_comments.add(comment_urn)
        existing_pairs.add(pair_key)
        counts["new"] += 1

    conn.commit()
    cur.close()
    conn.close()

    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
