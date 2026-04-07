#!/usr/bin/env python3
"""Backfill correct reply URLs for Twitter posts.

The posting pipeline was storing the parent tweet URL as our_url instead of
our actual reply URL. This script fixes that by:

1. Scrolling through x.com/m13v_/with_replies to collect all reply URLs
2. Calling fxtwitter API for each to get the parent tweet ID (replying_to_status)
3. Matching parent IDs to thread_url in the DB
4. Updating our_url to the real reply URL + nulling stale stats

Usage:
    # Dry run (default): show what would change, don't update DB
    python3 scripts/backfill_twitter_urls.py

    # Limit scrape scrolls (for testing)
    python3 scripts/backfill_twitter_urls.py --max-scrolls 5

    # Actually apply changes
    python3 scripts/backfill_twitter_urls.py --apply
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


def scrape_reply_urls(max_scrolls=300):
    """Scroll through profile with_replies page and collect all reply URLs."""
    from playwright.sync_api import sync_playwright
    from twitter_browser import get_browser_and_page

    extract_js = f"""() => {{
        const results = [];
        document.querySelectorAll('a[href*="/{OUR_HANDLE}/status/"]').forEach(a => {{
            const href = a.getAttribute('href');
            if (href && /\\/{OUR_HANDLE}\\/status\\/\\d+$/.test(href) && !results.includes(href))
                results.push(href);
        }});
        return results;
    }}"""

    all_urls = set()

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)
        try:
            page.goto(f"https://x.com/{OUR_HANDLE}/with_replies", wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            # Initial extract
            for url in page.evaluate(extract_js):
                all_urls.add(url)

            prev_height = 0
            same_count = 0
            scroll_count = 0

            while same_count < 5 and scroll_count < max_scrolls:
                cur_height = page.evaluate("document.body.scrollHeight")
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)

                for url in page.evaluate(extract_js):
                    all_urls.add(url)

                if cur_height == prev_height:
                    same_count += 1
                else:
                    same_count = 0
                prev_height = cur_height
                scroll_count += 1

                if scroll_count % 20 == 0:
                    print(f"  Scrolled {scroll_count}x, found {len(all_urls)} reply URLs so far...", flush=True)

        finally:
            if not is_cdp:
                page.close()
                browser.close()

    # Normalize to full URLs
    results = set()
    for url in all_urls:
        if not url.startswith("http"):
            url = f"https://x.com{url}"
        results.add(url)

    return results


def fetch_parent_id(reply_url):
    """Call fxtwitter to get the parent tweet's status ID for a reply."""
    m = re.search(r"/status/(\d+)", reply_url)
    if not m:
        return None
    status_id = m.group(1)

    api_url = f"https://api.fxtwitter.com/{OUR_HANDLE}/status/{status_id}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "social-autoposter/1.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        tweet = data.get("tweet", {})
        parent_id = tweet.get("replying_to_status")
        return str(parent_id) if parent_id else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # Tweet deleted
        return None
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Backfill correct Twitter reply URLs")
    parser.add_argument("--max-scrolls", type=int, default=300, help="Max scroll iterations on profile page")
    parser.add_argument("--apply", action="store_true", help="Actually update the database (default: dry run)")
    parser.add_argument("--limit", type=int, default=0, help="Limit fxtwitter lookups (0=all, for testing)")
    args = parser.parse_args()

    print(f"Step 1: Scraping reply URLs from x.com/{OUR_HANDLE}/with_replies ...", flush=True)
    reply_urls = scrape_reply_urls(max_scrolls=args.max_scrolls)
    print(f"  Found {len(reply_urls)} reply URLs from profile page", flush=True)

    print(f"\nStep 2: Looking up parent tweet IDs via fxtwitter API ...", flush=True)
    # reply_url -> parent_status_id
    reply_to_parent = {}
    total = len(reply_urls)
    if args.limit:
        reply_urls = list(reply_urls)[:args.limit]
        total = len(reply_urls)

    for i, reply_url in enumerate(reply_urls):
        parent_id = fetch_parent_id(reply_url)
        if parent_id:
            reply_to_parent[reply_url] = parent_id
        if (i + 1) % 50 == 0:
            print(f"  Checked {i + 1}/{total}, {len(reply_to_parent)} have parent IDs ...", flush=True)
        time.sleep(0.3)

    print(f"  Resolved {len(reply_to_parent)} reply -> parent mappings", flush=True)

    print(f"\nStep 3: Matching to database ...", flush=True)
    dbmod.load_env()
    conn = dbmod.get_conn()

    # Load all broken twitter posts (our_url = thread_url)
    broken = conn.execute(
        "SELECT id, our_url, thread_url FROM posts "
        "WHERE platform='twitter' AND status='active' AND our_url = thread_url "
        "AND our_url IS NOT NULL"
    ).fetchall()

    # Build lookup: parent_status_id -> [db rows]
    db_by_parent_id = {}
    for row in broken:
        db_id, our_url, thread_url = row[0], row[1], row[2]
        m = re.search(r"/status/(\d+)", thread_url or "")
        if m:
            parent_id = m.group(1)
            db_by_parent_id.setdefault(parent_id, []).append(db_id)

    # Match
    matched = 0
    unmatched_reply = 0
    updates = []  # (db_id, new_reply_url)

    for reply_url, parent_id in reply_to_parent.items():
        db_ids = db_by_parent_id.get(parent_id, [])
        if db_ids:
            # If multiple DB rows for the same parent, match the first unmatched one
            db_id = db_ids[0]
            updates.append((db_id, reply_url))
            db_ids.pop(0)
            matched += 1
        else:
            unmatched_reply += 1

    print(f"  Broken posts in DB: {len(broken)}", flush=True)
    print(f"  Matched: {matched}", flush=True)
    print(f"  Unmatched (reply has no DB entry): {unmatched_reply}", flush=True)
    print(f"  Remaining broken (no reply found on profile): {len(broken) - matched}", flush=True)

    if not updates:
        print("\nNo updates to apply.")
        return

    # Show sample
    print(f"\nSample updates (first 5):")
    for db_id, reply_url in updates[:5]:
        print(f"  Post {db_id}: our_url -> {reply_url}")

    if not args.apply:
        print(f"\nDry run complete. Run with --apply to update {matched} rows.")
        return

    print(f"\nStep 4: Applying {matched} updates ...", flush=True)
    for db_id, reply_url in updates:
        conn.execute(
            "UPDATE posts SET our_url = %s, "
            "upvotes = NULL, comments_count = NULL, views = NULL, "
            "engagement_updated_at = NULL, scan_no_change_count = 0 "
            "WHERE id = %s",
            [reply_url, db_id],
        )
    conn.commit()
    conn.close()
    print(f"  Done. Updated {matched} posts. Stats will refresh on next audit run.", flush=True)


if __name__ == "__main__":
    main()
