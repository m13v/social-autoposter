#!/usr/bin/env python3
"""Recover correct feed/update URLs for LinkedIn posts by scraping our own activity page.

Instead of visiting hundreds of individual profiles, this script:
1. Opens linkedin.com/in/YOUR_PROFILE/recent-activity/comments/
2. Scrolls through all comments loading more
3. For each comment, extracts the parent post's feed/update URL
4. Matches to DB posts by comparing our_content text
5. Updates our_url and thread_url with the correct URL

Uses Playwright with existing LinkedIn session cookies - no Claude API needed.

Usage:
    python3 scripts/recover_linkedin_urls.py
    python3 scripts/recover_linkedin_urls.py --dry-run
    python3 scripts/recover_linkedin_urls.py --limit 50
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def normalize_text(text):
    """Normalize text for fuzzy matching."""
    return re.sub(r'[^a-z0-9 ]', '', text.lower()).strip()


def scrape_comments_page(profile_slug, storage_state, max_scrolls=80):
    """Scrape all comments from our LinkedIn activity/comments page."""
    from playwright.sync_api import sync_playwright

    comments = []
    url = f"https://www.linkedin.com/in/{profile_slug}/recent-activity/comments/"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--window-position=100,100", "--window-size=1200,900"])
        context = browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1200, "height": 900},
        )
        page = context.new_page()

        print(f"Navigating to {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        # Scroll to load all comments
        prev_count = 0
        no_change_rounds = 0
        for scroll_num in range(max_scrolls):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)

            # Also click any "Show more" buttons
            try:
                show_more = page.query_selector_all('button:has-text("Show more")')
                for btn in show_more:
                    try:
                        btn.click()
                        page.wait_for_timeout(1500)
                    except Exception:
                        pass
            except Exception:
                pass

            # Count loaded items
            count = page.evaluate("""
                document.querySelectorAll(
                    '.profile-creator-shared-feed-update__container, ' +
                    '.occludable-update, ' +
                    '[data-urn*="activity"], ' +
                    '.feed-shared-update-v2'
                ).length
            """)

            if count == prev_count:
                no_change_rounds += 1
                if no_change_rounds >= 4:
                    print(f"  No new content after {scroll_num + 1} scrolls ({count} items). Done loading.")
                    break
            else:
                no_change_rounds = 0
                if (scroll_num + 1) % 10 == 0:
                    print(f"  Scroll {scroll_num + 1}: {count} items loaded...")

            prev_count = count

        # Extract all comments with their parent post URLs
        print("Extracting comments...")
        raw_comments = page.evaluate("""
            () => {
                const results = [];

                // Find all activity items on the page
                const items = document.querySelectorAll(
                    '.profile-creator-shared-feed-update__container, ' +
                    '.occludable-update, ' +
                    '.feed-shared-update-v2, ' +
                    '[data-urn*="activity"]'
                );

                items.forEach(item => {
                    // Extract any feed/update links
                    const links = item.querySelectorAll('a[href*="/feed/update/"]');
                    let feedUrl = null;
                    for (const link of links) {
                        const href = link.getAttribute('href') || '';
                        if (href.includes('/feed/update/')) {
                            // Clean URL - remove query params and fragments
                            feedUrl = href.split('?')[0].split('#')[0];
                            // Ensure full URL
                            if (feedUrl.startsWith('/')) feedUrl = 'https://www.linkedin.com' + feedUrl;
                            break;
                        }
                    }

                    // Also try data-urn attribute
                    if (!feedUrl) {
                        const urn = item.getAttribute('data-urn') || '';
                        const match = urn.match(/activity:(\\d+)/);
                        if (match) {
                            feedUrl = 'https://www.linkedin.com/feed/update/urn:li:activity:' + match[1] + '/';
                        }
                    }

                    // Get the comment text (our text)
                    const commentEl = item.querySelector(
                        '.feed-shared-update-v2__commentary, ' +
                        '.update-components-text, ' +
                        '.feed-shared-text__text-view, ' +
                        '.break-words'
                    );
                    const commentText = commentEl ? commentEl.innerText.trim() : '';

                    // Get all text as fallback
                    const allText = item.innerText.substring(0, 500);

                    if (feedUrl && (commentText || allText)) {
                        results.push({
                            feedUrl: feedUrl,
                            commentText: commentText.substring(0, 300),
                            allText: allText.substring(0, 300),
                        });
                    }
                });

                return results;
            }
        """)

        browser.close()

    print(f"Extracted {len(raw_comments)} comments from activity page")
    return raw_comments


def match_and_update(db, scraped_comments, dry_run=False, limit=None):
    """Match scraped comments to DB posts and update URLs."""
    # Get all LinkedIn posts with bad URLs
    posts = db.execute(
        "SELECT id, our_url, our_content FROM posts "
        "WHERE platform='linkedin' AND status='active' "
        "AND our_url IS NOT NULL AND our_url != '' "
        "AND our_url NOT LIKE '%%linkedin.com/feed/update/%%' "
        "ORDER BY id DESC"
    ).fetchall()

    if limit:
        posts = posts[:limit]

    print(f"\n{len(posts)} posts need URL recovery")
    print(f"{len(scraped_comments)} comments scraped from activity page\n")

    # Build normalized lookup from scraped comments
    scraped_lookup = []
    for item in scraped_comments:
        norm_comment = normalize_text(item.get("commentText", ""))
        norm_all = normalize_text(item.get("allText", ""))
        scraped_lookup.append({
            "feedUrl": item["feedUrl"],
            "norm_comment": norm_comment,
            "norm_all": norm_all,
        })

    matched = 0
    unmatched = 0

    for post in posts:
        db_id, our_url, our_content = post[0], post[1], post[2] or ""
        norm_content = normalize_text(our_content)

        if len(norm_content) < 20:
            unmatched += 1
            continue

        # Try to match by first 60 chars of normalized content
        prefix = norm_content[:60]
        best_match = None

        for item in scraped_lookup:
            if prefix in item["norm_comment"] or prefix in item["norm_all"]:
                best_match = item["feedUrl"]
                break

        if best_match:
            matched += 1
            if dry_run:
                print(f"  [{db_id}] WOULD UPDATE: {our_url[:50]}... -> {best_match}")
            else:
                db.execute(
                    "UPDATE posts SET our_url=%s, thread_url=%s WHERE id=%s",
                    [best_match, best_match, db_id],
                )
                print(f"  [{db_id}] UPDATED -> {best_match}")
        else:
            unmatched += 1

    if not dry_run:
        db.commit()

    return {"matched": matched, "unmatched": unmatched, "total": len(posts)}


def find_profile_slug(storage_state):
    """Try to find our LinkedIn profile slug from the activity page."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        # Get profile link from nav
        slug = page.evaluate("""
            () => {
                const link = document.querySelector('a[href*="/in/"].global-nav__primary-link-me-menu-trigger')
                    || document.querySelector('a[href*="/in/"][data-control-name="identity_welcome_message"]')
                    || document.querySelector('a[href*="/in/"].ember-view');
                if (link) {
                    const m = link.getAttribute('href').match(/\\/in\\/([^/]+)/);
                    return m ? m[1] : null;
                }
                return null;
            }
        """)

        browser.close()
        return slug


def main():
    parser = argparse.ArgumentParser(description="Recover LinkedIn post URLs from activity page")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without changing DB")
    parser.add_argument("--limit", type=int, help="Limit number of posts to process")
    parser.add_argument("--profile", type=str, help="LinkedIn profile slug (e.g., 'matthewdiakonov')")
    parser.add_argument("--max-scrolls", type=int, default=80, help="Max scrolls on activity page (default 80)")
    args = parser.parse_args()

    storage_state = "/Users/matthewdi/.claude/browser-sessions.json"
    if not os.path.exists(storage_state):
        print("ERROR: browser-sessions.json not found", file=sys.stderr)
        sys.exit(1)

    profile_slug = args.profile
    if not profile_slug:
        print("Detecting LinkedIn profile slug...")
        profile_slug = find_profile_slug(storage_state)
        if not profile_slug:
            print("ERROR: Could not detect profile slug. Use --profile flag.", file=sys.stderr)
            sys.exit(1)
    print(f"Using profile: {profile_slug}")

    # Scrape comments from our activity page
    scraped_comments = scrape_comments_page(profile_slug, storage_state, max_scrolls=args.max_scrolls)

    if not scraped_comments:
        print("No comments found on activity page. Check login session.")
        sys.exit(1)

    # Match to DB and update
    dbmod.load_env()
    db = dbmod.get_conn()
    result = match_and_update(db, scraped_comments, dry_run=args.dry_run, limit=args.limit)
    db.close()

    action = "Would update" if args.dry_run else "Updated"
    print(f"\n{action}: {result['matched']} / {result['total']} posts")
    print(f"Unmatched: {result['unmatched']}")


if __name__ == "__main__":
    main()
