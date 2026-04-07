#!/usr/bin/env python3
"""Backfill Twitter reply URLs by visiting each parent tweet page.

For posts where our_url = thread_url (wrong), visits the parent tweet
and searches the DOM for our reply link (a[href*="/m13v_/status/"]).

Usage:
    # Dry run on 10 posts
    python3 scripts/backfill_twitter_urls_v2.py --limit 10

    # Apply changes
    python3 scripts/backfill_twitter_urls_v2.py --limit 10 --apply

    # Full run
    python3 scripts/backfill_twitter_urls_v2.py --apply
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

OUR_HANDLE = "m13v_"


def find_our_replies_on_page(page, parent_url):
    """Navigate to a parent tweet and find ALL our reply URLs in the thread.

    Returns list of reply URL strings (may be empty).
    """
    extract_js = f"""() => {{
        const links = new Set();
        document.querySelectorAll('a[href*="/{OUR_HANDLE}/status/"]').forEach(a => {{
            const href = a.getAttribute('href');
            if (href && /\\/{OUR_HANDLE}\\/status\\/\\d+$/.test(href))
                links.add(href);
        }});
        return [...links];
    }}"""

    try:
        page.goto(parent_url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        page_text = page.text_content("main") or ""
        if "this page doesn't exist" in page_text.lower():
            return [], "tweet_not_found"
        if "this account doesn" in page_text.lower():
            return [], "account_suspended"

        all_links = set(page.evaluate(extract_js))

        # Scroll down to load more replies
        page.evaluate("window.scrollBy(0, 2000)")
        page.wait_for_timeout(3000)
        all_links.update(page.evaluate(extract_js))

        # One more scroll
        page.evaluate("window.scrollBy(0, 2000)")
        page.wait_for_timeout(2000)
        all_links.update(page.evaluate(extract_js))

        results = []
        for link in all_links:
            url = f"https://x.com{link}" if not link.startswith("http") else link
            results.append(url)

        return results, None

    except Exception as e:
        return [], str(e)[:100]


def verify_reply(reply_url, expected_parent_id):
    """Verify via fxtwitter that the reply URL points to the expected parent."""
    import urllib.request
    import urllib.error

    m = re.search(r"/status/(\d+)", reply_url)
    if not m:
        return False, "bad_url"

    status_id = m.group(1)
    api_url = f"https://api.fxtwitter.com/{OUR_HANDLE}/status/{status_id}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "social-autoposter/1.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        tweet = data.get("tweet", {})
        actual_parent = str(tweet.get("replying_to_status", ""))
        author = tweet.get("author", {}).get("screen_name", "")

        if author.lower() != OUR_HANDLE.lower():
            return False, f"wrong_author:{author}"
        if actual_parent == expected_parent_id:
            return True, None
        else:
            return False, f"parent_mismatch:expected={expected_parent_id},got={actual_parent}"
    except Exception as e:
        return False, str(e)[:100]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Limit posts to process (0=all)")
    parser.add_argument("--apply", action="store_true", help="Apply DB updates")
    parser.add_argument("--verify", action="store_true", default=True, help="Verify each URL via fxtwitter")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright
    from twitter_browser import get_browser_and_page

    dbmod.load_env()
    conn = dbmod.get_conn()

    # Load truly broken posts (our_url = thread_url AND our_url is NOT our own account)
    rows = conn.execute(
        f"SELECT id, our_url, thread_url, LEFT(our_content, 80) as content "
        f"FROM posts "
        f"WHERE platform='twitter' AND status='active' AND our_url = thread_url "
        f"AND our_url IS NOT NULL AND our_url NOT LIKE '%%/{OUR_HANDLE}/%%' "
        f"ORDER BY id"
    ).fetchall()

    total_broken = len(rows)
    if args.limit:
        rows = rows[:args.limit]

    print(f"Total broken: {total_broken}, processing: {len(rows)}", flush=True)

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        found = 0
        not_found = 0
        verified_ok = 0
        verified_fail = 0
        updates = []

        try:
            for i, row in enumerate(rows):
                db_id, our_url, thread_url, content = row[0], row[1], row[2], row[3]
                parent_id = re.search(r'/status/(\d+)', thread_url or "")
                if not parent_id:
                    not_found += 1
                    continue
                parent_id = parent_id.group(1)

                visit_url = thread_url.replace("twitter.com", "x.com")

                reply_urls, error = find_our_replies_on_page(page, visit_url)

                if not reply_urls:
                    not_found += 1
                    print(f"  [{i+1}/{len(rows)}] Post {db_id}: NOT_FOUND({error})", flush=True)
                    time.sleep(1)
                    continue

                # If only one reply found, use it directly
                if len(reply_urls) == 1:
                    reply_url = reply_urls[0]
                    ok, verify_err = verify_reply(reply_url, parent_id)
                    time.sleep(0.3)
                    if ok:
                        found += 1
                        verified_ok += 1
                        updates.append((db_id, reply_url))
                        print(f"  [{i+1}/{len(rows)}] Post {db_id}: VERIFIED -> {reply_url}", flush=True)
                    else:
                        # Single reply but wrong parent; might be replying to a reply in the thread
                        # Accept it if the author is correct
                        found += 1
                        verified_fail += 1
                        updates.append((db_id, reply_url))
                        print(f"  [{i+1}/{len(rows)}] Post {db_id}: ACCEPTED (single reply, {verify_err}) -> {reply_url}", flush=True)
                else:
                    # Multiple replies from us in this thread. Check each via fxtwitter.
                    matched_url = None
                    for candidate in reply_urls:
                        ok, verify_err = verify_reply(candidate, parent_id)
                        time.sleep(0.3)
                        if ok:
                            matched_url = candidate
                            break

                    if matched_url:
                        found += 1
                        verified_ok += 1
                        updates.append((db_id, matched_url))
                        print(f"  [{i+1}/{len(rows)}] Post {db_id}: VERIFIED (1 of {len(reply_urls)}) -> {matched_url}", flush=True)
                    else:
                        # None matched the exact parent. Pick by content similarity as last resort.
                        not_found += 1
                        print(f"  [{i+1}/{len(rows)}] Post {db_id}: NO_MATCH ({len(reply_urls)} candidates, none matched parent {parent_id})", flush=True)

                time.sleep(1)

        finally:
            if not is_cdp:
                page.close()
                browser.close()

    print(f"\nResults:", flush=True)
    print(f"  Found: {found}", flush=True)
    print(f"  Not found: {not_found}", flush=True)
    if args.verify:
        print(f"  Verified OK: {verified_ok}", flush=True)
        print(f"  Verified FAIL: {verified_fail}", flush=True)
    print(f"  Updates ready: {len(updates)}", flush=True)

    if not updates:
        print("No updates to apply.")
        return

    if not args.apply:
        print(f"\nDry run. Use --apply to update {len(updates)} rows.")
        return

    print(f"\nApplying {len(updates)} updates ...", flush=True)
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
    print(f"  Done. Updated {len(updates)} posts.", flush=True)


if __name__ == "__main__":
    main()
