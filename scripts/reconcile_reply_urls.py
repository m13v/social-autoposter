#!/usr/bin/env python3
"""Reconcile missing our_url values in the posts table.

Fetches our recent tweets from the Twitter API and matches them to DB posts
by conversation_id (the tweet we replied to). Updates our_url for any matches.

Usage:
    python3 scripts/reconcile_reply_urls.py
    python3 scripts/reconcile_reply_urls.py --hours 6
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod
import twitter_api


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=4, help="Look back N hours")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = dbmod.get_conn()

    # Get posts missing our_url
    rows = conn.execute(
        """SELECT id, thread_url FROM posts
           WHERE platform='twitter' AND our_url IS NULL
             AND posted_at > NOW() - INTERVAL '%s hours'
           ORDER BY posted_at DESC""",
        (args.hours,),
    ).fetchall()

    if not rows:
        print("No posts missing our_url")
        return

    # Build a map: parent_tweet_id -> post_id
    missing = {}
    for row in rows:
        post_id = row[0] if isinstance(row, (list, tuple)) else row["id"]
        thread_url = row[1] if isinstance(row, (list, tuple)) else row["thread_url"]
        if not thread_url or "x.com" not in thread_url:
            continue
        parts = thread_url.rstrip("/").split("/")
        if parts:
            parent_id = parts[-1]
            missing[parent_id] = post_id

    print(f"Found {len(missing)} Twitter posts missing our_url (last {args.hours}h)")

    if not missing:
        return

    # Fetch our recent tweets from the API
    client = twitter_api.get_read_client()
    me = twitter_api.get_me()

    all_tweets = []
    pagination_token = None
    for _ in range(5):  # up to 500 tweets
        resp = client.get_users_tweets(
            me.id,
            max_results=100,
            tweet_fields=["id", "conversation_id", "referenced_tweets", "created_at"],
            exclude=["retweets"],
            pagination_token=pagination_token,
        )
        if resp.data:
            all_tweets.extend(resp.data)
        if not resp.meta or "next_token" not in resp.meta:
            break
        pagination_token = resp.meta["next_token"]

    print(f"Fetched {len(all_tweets)} tweets from API")

    # Match tweets to posts
    updated = 0
    for tweet in all_tweets:
        if not tweet.referenced_tweets:
            continue
        for ref in tweet.referenced_tweets:
            if ref.type == "replied_to":
                parent_id = str(ref.id)
                if parent_id in missing:
                    post_id = missing[parent_id]
                    our_url = f"https://x.com/{me.username}/status/{tweet.id}"
                    if args.dry_run:
                        print(f"  [DRY RUN] post {post_id}: {our_url}")
                    else:
                        conn.execute(
                            "UPDATE posts SET our_url = %s WHERE id = %s",
                            (our_url, post_id),
                        )
                        print(f"  Updated post {post_id}: {our_url}")
                    updated += 1
                    del missing[parent_id]

    if not args.dry_run:
        conn.commit()
    conn.close()

    print(f"Reconciled {updated}/{len(rows)} posts")
    if missing:
        print(f"  {len(missing)} still unmatched (API may not have indexed them yet)")


if __name__ == "__main__":
    main()
