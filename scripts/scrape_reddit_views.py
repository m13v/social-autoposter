#!/usr/bin/env python3
"""Scrape Reddit view counts from user profile via Playwright browser.

Reddit doesn't expose view counts through their public API, but they are
visible on the profile page when logged in. This script uses Playwright
to scroll through the profile, extract view counts for each post/comment,
and update the `views` column in the database.

Requires: playwright (pip install playwright && playwright install chromium)
Requires: logged-in Reddit session in the browser's persistent context

Usage:
    python3 scripts/scrape_reddit_views.py [--quiet] [--json]
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")
# Chrome user data dir for persistent login
CHROME_USER_DATA = os.path.expanduser("~/Library/Application Support/Google/Chrome")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def extract_ids(url):
    """Extract (post_id, comment_id) from any reddit URL format."""
    url = re.sub(r"https?://(old|www|new)\.reddit\.com", "", url)
    url = re.sub(r"\?.*$", "", url).rstrip("/")

    # New format: /r/sub/comments/POST_ID/comment/COMMENT_ID
    m = re.search(r"/comments/([a-z0-9]+)/comment/([a-z0-9]+)", url)
    if m:
        return (m.group(1), m.group(2))

    # Old format: /r/sub/comments/POST_ID/slug/COMMENT_ID
    m = re.search(r"/comments/([a-z0-9]+)/[^/]+/([a-z0-9]+)", url)
    if m:
        return (m.group(1), m.group(2))

    # Post only: /r/sub/comments/POST_ID/...
    m = re.search(r"/comments/([a-z0-9]+)", url)
    if m:
        return (m.group(1), None)

    return (None, None)


def scrape_views_playwright(username, quiet=False):
    """Scroll through Reddit profile and extract view counts using Playwright."""
    from playwright.sync_api import sync_playwright

    profile_url = f"https://www.reddit.com/user/{username}/"

    all_results = {}

    with sync_playwright() as p:
        # Use persistent context to reuse logged-in session
        browser = p.chromium.launch_persistent_context(
            user_data_dir=os.path.join(CHROME_USER_DATA, "Default"),
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.new_page()

        if not quiet:
            print(f"Navigating to {profile_url}")
        page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        def extract_current():
            return page.evaluate("""() => {
                const results = [];
                const articles = document.querySelectorAll('article');
                articles.forEach(article => {
                    const links = article.querySelectorAll('a[href*="/comments/"]');
                    let url = null;
                    for (const link of links) {
                        const href = link.getAttribute('href');
                        if (href && href.includes('/comments/')) {
                            if (!url || href.includes('/comment/')) url = href;
                        }
                    }
                    let views = null;
                    const allEls = article.querySelectorAll('*');
                    for (const el of allEls) {
                        const text = el.textContent.trim();
                        const match = text.match(/^([\\d,]+)\\s+views?$/);
                        if (match) { views = parseInt(match[1].replace(/,/g, '')); break; }
                    }
                    if (url) {
                        const fullUrl = url.startsWith('http') ? url : 'https://www.reddit.com' + url;
                        results.push({ url: fullUrl, views });
                    }
                });
                return results;
            }""")

        # Collect initial items
        items = extract_current()
        for item in items:
            all_results[item["url"]] = item["views"]

        previous_height = 0
        same_height_count = 0
        scroll_count = 0

        while same_height_count < 4 and scroll_count < 300:
            current_height = page.evaluate("() => document.body.scrollHeight")
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)

            items = extract_current()
            for item in items:
                all_results[item["url"]] = item["views"]

            if current_height == previous_height:
                same_height_count += 1
            else:
                same_height_count = 0
            previous_height = current_height
            scroll_count += 1

        browser.close()

    if not quiet:
        print(f"Scraped {len(all_results)} items in {scroll_count} scrolls")

    return all_results


def update_views(db, scraped_views, quiet=False):
    """Match scraped view data to DB posts and update."""
    # Build lookups by comment_id and post_id
    views_by_comment = {}
    views_by_post = {}
    for url, views in scraped_views.items():
        if views is None:
            continue
        post_id, comment_id = extract_ids(url)
        if comment_id:
            views_by_comment[comment_id] = views
        elif post_id:
            views_by_post[post_id] = views

    posts = db.execute(
        "SELECT id, our_url FROM posts "
        "WHERE platform='reddit' AND status='active' AND our_url IS NOT NULL"
    ).fetchall()

    matched = 0
    unmatched = 0

    for post in posts:
        db_id, our_url = post[0], post[1]
        post_id, comment_id = extract_ids(our_url)

        views = None
        if comment_id and comment_id in views_by_comment:
            views = views_by_comment[comment_id]
        elif post_id and post_id in views_by_post:
            views = views_by_post[post_id]

        if views is not None:
            db.execute(
                "UPDATE posts SET views=%s, engagement_updated_at=NOW() WHERE id=%s",
                [views, db_id],
            )
            matched += 1
        else:
            unmatched += 1

    db.commit()
    return {"matched": matched, "unmatched": unmatched, "scraped_total": len(scraped_views),
            "with_views": len(views_by_comment) + len(views_by_post)}


def main():
    parser = argparse.ArgumentParser(description="Scrape Reddit view counts from profile")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    config = load_config()
    username = config.get("accounts", {}).get("reddit", {}).get("username", "")
    if not username:
        print("ERROR: No Reddit username in config.json", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        print(f"Scraping views for u/{username}...")

    scraped_views = scrape_views_playwright(username, quiet=args.quiet)

    dbmod.load_env()
    db = dbmod.get_conn()
    result = update_views(db, scraped_views, quiet=args.quiet)
    db.close()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\nReddit Views: {result['scraped_total']} items scraped, "
              f"{result['with_views']} had views, "
              f"{result['matched']} DB posts updated, "
              f"{result['unmatched']} unmatched")


if __name__ == "__main__":
    main()
