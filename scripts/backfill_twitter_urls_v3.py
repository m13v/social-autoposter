#!/usr/bin/env python3
"""Backfill Twitter reply URLs via profile scraping + fxtwitter matching.

Strategy (much better than v2's 3.7% hit rate):
1. Scroll through our profile /with_replies to collect ALL our tweet URLs
2. For each URL, call fxtwitter API to get replying_to_status (parent tweet ID)
3. Build a map: parent_status_id -> our_reply_url
4. Match broken DB posts by their thread_url's status ID
5. Update matched posts, null out stats for unrecoverable ones

Usage:
    # Phase 1: Scrape profile and save URLs to file
    python3 scripts/backfill_twitter_urls_v3.py --scrape

    # Phase 2: Match and update DB (dry run)
    python3 scripts/backfill_twitter_urls_v3.py --match

    # Phase 2: Match and update DB (apply)
    python3 scripts/backfill_twitter_urls_v3.py --match --apply

    # Phase 3: Null out stats for remaining broken posts
    python3 scripts/backfill_twitter_urls_v3.py --cleanup --apply

    # All phases
    python3 scripts/backfill_twitter_urls_v3.py --scrape --match --cleanup --apply
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


def scrape_profile_urls():
    """Scroll through our profile /with_replies and collect all tweet URLs."""
    from playwright.sync_api import sync_playwright
    from twitter_browser import get_browser_and_page

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            url = f"https://x.com/{OUR_HANDLE}/with_replies"
            print(f"Navigating to {url}...", flush=True)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            all_urls = set()
            extract_js = f"""() => {{
                const links = new Set();
                document.querySelectorAll('a[href*="/{OUR_HANDLE}/status/"]').forEach(a => {{
                    const href = a.getAttribute('href');
                    if (href && /\\/{OUR_HANDLE}\\/status\\/\\d+$/.test(href))
                        links.add(href);
                }});
                return [...links];
            }}"""

            no_new_count = 0
            scroll_count = 0
            max_no_new = 15  # Stop after 15 scrolls with no new URLs

            while no_new_count < max_no_new:
                new_links = set(page.evaluate(extract_js))
                before = len(all_urls)
                all_urls.update(new_links)
                after = len(all_urls)

                scroll_count += 1
                if after > before:
                    no_new_count = 0
                    print(f"  Scroll {scroll_count}: {after} URLs (+{after - before})", flush=True)
                else:
                    no_new_count += 1
                    if no_new_count % 5 == 0:
                        print(f"  Scroll {scroll_count}: {after} URLs (no new x{no_new_count})", flush=True)

                # Scroll down
                page.evaluate("window.scrollBy(0, 3000)")
                page.wait_for_timeout(2000)

            print(f"\nDone scraping. Total unique URLs: {len(all_urls)}", flush=True)

            # Convert to full URLs and save
            full_urls = []
            for link in all_urls:
                if link.startswith("/"):
                    full_urls.append(f"https://x.com{link}")
                else:
                    full_urls.append(link)

            with open(PROFILE_URLS_FILE, "w") as f:
                json.dump(sorted(full_urls), f, indent=2)
            print(f"Saved to {PROFILE_URLS_FILE}", flush=True)

            return full_urls

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def load_profile_urls():
    """Load previously scraped profile URLs."""
    if not os.path.exists(PROFILE_URLS_FILE):
        print(f"No profile URLs file found at {PROFILE_URLS_FILE}. Run with --scrape first.", flush=True)
        return []
    with open(PROFILE_URLS_FILE) as f:
        return json.load(f)


def load_fxtwitter_cache():
    """Load cached fxtwitter lookups."""
    if os.path.exists(FXTWITTER_CACHE_FILE):
        with open(FXTWITTER_CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_fxtwitter_cache(cache):
    """Save fxtwitter cache."""
    with open(FXTWITTER_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def fxtwitter_lookup(status_id, cache):
    """Look up a tweet via fxtwitter API. Returns (replying_to_status, author, error)."""
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


def match_and_update(apply=False):
    """Match profile URLs to broken DB posts via fxtwitter API."""
    profile_urls = load_profile_urls()
    if not profile_urls:
        return

    print(f"Loaded {len(profile_urls)} profile URLs", flush=True)

    # Extract status IDs from profile URLs
    profile_status_ids = []
    for url in profile_urls:
        m = re.search(r"/status/(\d+)", url)
        if m:
            profile_status_ids.append((m.group(1), url))

    print(f"Found {len(profile_status_ids)} valid status IDs", flush=True)

    # Load DB broken posts
    dbmod.load_env()
    conn = dbmod.get_conn()
    broken_rows = conn.execute(
        "SELECT id, our_url, thread_url FROM posts "
        "WHERE platform='twitter' AND status='active' "
        "AND our_url = thread_url AND our_url IS NOT NULL "
        f"AND our_url NOT LIKE '%%/{OUR_HANDLE}/%%' "
        "ORDER BY id"
    ).fetchall()

    print(f"Found {len(broken_rows)} broken posts in DB", flush=True)

    # Build a map: parent_status_id -> [broken_post_ids]
    parent_to_broken = {}
    for row in broken_rows:
        db_id, our_url, thread_url = row[0], row[1], row[2]
        m = re.search(r"/status/(\d+)", thread_url or "")
        if m:
            parent_id = m.group(1)
            if parent_id not in parent_to_broken:
                parent_to_broken[parent_id] = []
            parent_to_broken[parent_id].append(db_id)

    print(f"Unique parent status IDs in broken posts: {len(parent_to_broken)}", flush=True)

    # Look up each profile URL via fxtwitter to find its parent
    cache = load_fxtwitter_cache()
    parent_to_reply_url = {}  # parent_status_id -> our_reply_url

    total = len(profile_status_ids)
    matched_count = 0
    errors = 0
    skipped = 0

    for i, (status_id, url) in enumerate(profile_status_ids):
        replying_to, author, error = fxtwitter_lookup(status_id, cache)

        if error:
            errors += 1
            if errors % 50 == 0:
                print(f"  [{i+1}/{total}] {errors} errors so far, latest: {error}", flush=True)
            time.sleep(0.2)
            continue

        if not replying_to:
            skipped += 1  # Original tweet, not a reply
            continue

        # Check if this parent is one of our broken posts
        if replying_to in parent_to_broken:
            parent_to_reply_url[replying_to] = url
            matched_count += 1

        if (i + 1) % 200 == 0:
            save_fxtwitter_cache(cache)
            print(f"  [{i+1}/{total}] Matched: {matched_count}, Errors: {errors}, Skipped(not reply): {skipped}", flush=True)

        time.sleep(0.1)  # Rate limit

    save_fxtwitter_cache(cache)
    print(f"\nfxtwitter lookup complete:", flush=True)
    print(f"  Total URLs checked: {total}", flush=True)
    print(f"  Matched to broken posts: {matched_count}", flush=True)
    print(f"  Errors: {errors}", flush=True)
    print(f"  Not replies (original tweets): {skipped}", flush=True)

    # Apply updates
    updated = 0
    for parent_id, reply_url in parent_to_reply_url.items():
        post_ids = parent_to_broken[parent_id]
        for post_id in post_ids:
            if apply:
                conn.execute(
                    "UPDATE posts SET our_url = %s, "
                    "upvotes = NULL, comments_count = NULL, views = NULL, "
                    "engagement_updated_at = NULL, scan_no_change_count = 0 "
                    "WHERE id = %s",
                    [reply_url, post_id],
                )
                conn.commit()
            updated += 1
            print(f"  {'UPDATED' if apply else 'WOULD UPDATE'} post {post_id}: {reply_url}", flush=True)

    print(f"\n{'Applied' if apply else 'Would apply'} {updated} updates", flush=True)
    if not apply and updated > 0:
        print("Run with --apply to commit changes.", flush=True)

    conn.close()
    return updated


def cleanup_broken_stats(apply=False):
    """Null out stats for remaining broken posts that can't be recovered."""
    dbmod.load_env()
    conn = dbmod.get_conn()

    # Count remaining broken posts
    remaining = conn.execute(
        "SELECT COUNT(*) FROM posts "
        "WHERE platform='twitter' AND status='active' "
        "AND our_url = thread_url AND our_url IS NOT NULL "
        f"AND our_url NOT LIKE '%%/{OUR_HANDLE}/%%'"
    ).fetchone()[0]

    print(f"\nRemaining broken posts after matching: {remaining}", flush=True)

    if remaining == 0:
        print("Nothing to clean up.", flush=True)
        conn.close()
        return

    # For these, set our_url = NULL (we don't know the real URL)
    # and null out the wrong stats so they don't pollute reports
    if apply:
        conn.execute(
            "UPDATE posts SET our_url = NULL, "
            "upvotes = NULL, comments_count = NULL, views = NULL, "
            "engagement_updated_at = NULL, scan_no_change_count = 0 "
            "WHERE platform='twitter' AND status='active' "
            "AND our_url = thread_url AND our_url IS NOT NULL "
            f"AND our_url NOT LIKE '%%/{OUR_HANDLE}/%%'"
        )
        conn.commit()
        print(f"Cleaned up {remaining} posts: set our_url=NULL, nulled stats", flush=True)
    else:
        print(f"Would clean up {remaining} posts. Run with --apply to commit.", flush=True)

    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scrape", action="store_true", help="Phase 1: Scrape profile URLs")
    parser.add_argument("--match", action="store_true", help="Phase 2: Match and update DB")
    parser.add_argument("--cleanup", action="store_true", help="Phase 3: Null out unrecoverable stats")
    parser.add_argument("--apply", action="store_true", help="Actually apply DB changes")
    args = parser.parse_args()

    if not any([args.scrape, args.match, args.cleanup]):
        print("Specify at least one phase: --scrape, --match, --cleanup")
        return

    if args.scrape:
        print("=" * 60)
        print("PHASE 1: Scraping profile URLs")
        print("=" * 60)
        scrape_profile_urls()

    if args.match:
        print("=" * 60)
        print("PHASE 2: Matching URLs to broken posts via fxtwitter")
        print("=" * 60)
        match_and_update(apply=args.apply)

    if args.cleanup:
        print("=" * 60)
        print("PHASE 3: Cleaning up unrecoverable broken posts")
        print("=" * 60)
        cleanup_broken_stats(apply=args.apply)


if __name__ == "__main__":
    main()
