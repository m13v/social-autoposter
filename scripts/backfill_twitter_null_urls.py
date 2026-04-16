#!/usr/bin/env python3
"""Backfill Twitter posts where our_url is NULL by matching scraped profile URLs.

Uses the profile URLs scraped by backfill_twitter_urls_v3.py (Phase 1).
For each scraped URL, calls fxtwitter API to get the parent tweet's status ID,
then matches against DB posts where our_url IS NULL by thread_url's status ID.

Usage:
    # Dry run (requires Phase 1 scrape to have run first)
    python3 scripts/backfill_twitter_null_urls.py

    # Apply changes
    python3 scripts/backfill_twitter_null_urls.py --apply
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

OUR_HANDLE = "m13v_"
PROFILE_URLS_FILE = "/tmp/twitter_profile_urls.json"
FXTWITTER_CACHE_FILE = "/tmp/twitter_fxtwitter_cache.json"


def load_profile_urls():
    if not os.path.exists(PROFILE_URLS_FILE):
        print(f"No profile URLs file at {PROFILE_URLS_FILE}. Run backfill_twitter_urls_v3.py --scrape first.")
        return []
    with open(PROFILE_URLS_FILE) as f:
        return json.load(f)


def load_cache():
    if os.path.exists(FXTWITTER_CACHE_FILE):
        with open(FXTWITTER_CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(FXTWITTER_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def fxtwitter_lookup(status_id, cache):
    if status_id in cache:
        c = cache[status_id]
        return c.get("replying_to"), c.get("author"), c.get("error")

    api_url = f"https://api.fxtwitter.com/{OUR_HANDLE}/status/{status_id}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "social-autoposter/1.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        tweet = data.get("tweet", {})
        replying_to = str(tweet.get("replying_to_status", "")) or None
        author = tweet.get("author", {}).get("screen_name", "")

        cache[status_id] = {"replying_to": replying_to, "author": author, "error": None}
        return replying_to, author, None
    except urllib.error.HTTPError as e:
        err = f"HTTP {e.code}"
        cache[status_id] = {"replying_to": None, "author": None, "error": err}
        return None, None, err
    except Exception as e:
        err = str(e)[:100]
        cache[status_id] = {"replying_to": None, "author": None, "error": err}
        return None, None, err


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply DB changes")
    args = parser.parse_args()

    profile_urls = load_profile_urls()
    if not profile_urls:
        return

    # Extract status IDs from profile URLs
    profile_status_ids = []
    for url in profile_urls:
        m = re.search(r"/status/(\d+)", url)
        if m:
            profile_status_ids.append((m.group(1), url))

    print(f"Loaded {len(profile_status_ids)} profile URLs", flush=True)

    # Load DB posts with NULL our_url
    dbmod.load_env()
    conn = dbmod.get_conn()
    null_rows = conn.execute(
        "SELECT id, thread_url FROM posts "
        "WHERE platform='twitter' AND status='active' "
        "AND our_url IS NULL "
        "ORDER BY id"
    ).fetchall()

    print(f"Found {len(null_rows)} posts with NULL our_url", flush=True)

    if not null_rows:
        conn.close()
        return

    # Build map: parent_status_id -> [post_ids]
    parent_to_posts = {}
    for row in null_rows:
        db_id, thread_url = row[0], row[1]
        m = re.search(r"/status/(\d+)", thread_url or "")
        if m:
            parent_id = m.group(1)
            if parent_id not in parent_to_posts:
                parent_to_posts[parent_id] = []
            parent_to_posts[parent_id].append(db_id)

    print(f"Unique parent status IDs in NULL posts: {len(parent_to_posts)}", flush=True)

    # Look up each profile URL via fxtwitter to find its parent
    cache = load_cache()
    matched = 0
    errors = 0
    skipped = 0

    for i, (status_id, url) in enumerate(profile_status_ids):
        replying_to, author, error = fxtwitter_lookup(status_id, cache)

        if error:
            errors += 1
            if errors % 50 == 0:
                print(f"  [{i+1}/{len(profile_status_ids)}] {errors} errors so far", flush=True)
            time.sleep(0.2)
            continue

        if not replying_to:
            skipped += 1
            continue

        if replying_to in parent_to_posts:
            post_ids = parent_to_posts[replying_to]
            for post_id in post_ids:
                if args.apply:
                    conn.execute(
                        "UPDATE posts SET our_url = %s WHERE id = %s",
                        [url, post_id],
                    )
                    conn.commit()
                matched += 1
                print(f"  {'UPDATED' if args.apply else 'WOULD UPDATE'} post {post_id}: {url}", flush=True)
            # Remove matched parent so we don't double-match
            del parent_to_posts[replying_to]

        if (i + 1) % 200 == 0:
            save_cache(cache)
            print(f"  [{i+1}/{len(profile_status_ids)}] Matched: {matched}, Errors: {errors}, Skipped: {skipped}", flush=True)

        time.sleep(0.1)

    save_cache(cache)

    print(f"\nResults:", flush=True)
    print(f"  Profile URLs checked: {len(profile_status_ids)}", flush=True)
    print(f"  Matched to NULL posts: {matched}", flush=True)
    print(f"  Errors: {errors}", flush=True)
    print(f"  Not replies: {skipped}", flush=True)
    print(f"  Remaining NULL: {len(null_rows) - matched}", flush=True)

    conn.close()


if __name__ == "__main__":
    main()
