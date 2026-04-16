#!/usr/bin/env python3
"""Backfill missing Reddit our_url values by scraping the user's comment history.

Uses the reddit browser profile (persistent, logged in as Deep_Ad1959) to navigate
old.reddit.com/user/Deep_Ad1959/comments/ and paginate through all comments,
extracting permalinks and matching them to DB posts by thread_url + content similarity.
"""

import json
import os
import re
import sys
import time

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from scripts.db import get_conn

PROFILE_DIR = os.path.expanduser("~/.claude/browser-profiles/reddit")
VIEWPORT = {"width": 911, "height": 1016}
USERNAME = "Deep_Ad1959"


def scrape_comments_page(page):
    """Extract all comment permalinks and snippets from the current old.reddit.com user comments page.

    Returns list of dicts: {permalink, thread_url, body_snippet}
    """
    return page.evaluate("""() => {
        const comments = [];
        // Each comment on old reddit user page is in a .thing.comment
        const things = document.querySelectorAll('.thing.comment');
        for (const thing of things) {
            const permalink_el = thing.querySelector('a.bylink[href*="/comments/"]');
            const parent_link = thing.querySelector('a.title');
            const body_el = thing.querySelector('.md');

            if (!permalink_el) continue;

            const permalink = permalink_el.href;
            const thread_url = parent_link ? parent_link.href : '';
            const body = body_el ? body_el.textContent.trim().substring(0, 200) : '';

            comments.push({
                permalink: permalink,
                thread_url: thread_url,
                body_snippet: body
            });
        }
        return comments;
    }""")


def get_next_page_url(page):
    """Get the 'next' page URL from old.reddit.com pagination."""
    return page.evaluate("""() => {
        const next = document.querySelector('.next-button a');
        return next ? next.href : null;
    }""")


def normalize_thread_url(url):
    """Normalize a Reddit thread URL for matching."""
    if not url:
        return ""
    # Strip query params
    url = re.sub(r'\?.*$', '', url)
    # Normalize domain
    url = re.sub(r'https?://(www\.|old\.)?reddit\.com', '', url)
    # Remove trailing slash
    url = url.rstrip('/')
    # Extract just the thread path (up to and including the thread slug)
    m = re.match(r'(/r/[^/]+/comments/[^/]+(?:/[^/]+)?)', url)
    if m:
        return m.group(1).lower()
    return url.lower()


def content_similarity(a, b):
    """Simple word-overlap similarity between two strings."""
    if not a or not b:
        return 0.0
    words_a = set(a.lower().split()[:20])
    words_b = set(b.lower().split()[:20])
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    return len(intersection) / min(len(words_a), len(words_b))


def main():
    from playwright.sync_api import sync_playwright

    # Get posts missing URLs from DB
    db = get_conn()
    cur = db.execute("""
        SELECT id, thread_url, our_content
        FROM posts
        WHERE platform = 'reddit'
          AND status = 'active'
          AND (our_url IS NULL OR our_url = '' OR our_url NOT LIKE 'http%%')
    """)
    missing = cur.fetchall()
    print(f"Found {len(missing)} Reddit posts missing URLs")

    if not missing:
        print("Nothing to backfill!")
        return

    # Build lookup by normalized thread URL
    by_thread = {}
    for row in missing:
        post_id, thread_url, content = row[0], row[1], row[2]
        key = normalize_thread_url(thread_url)
        if key not in by_thread:
            by_thread[key] = []
        by_thread[key].append({
            'id': post_id,
            'thread_url': thread_url,
            'content': content or ''
        })

    print(f"Unique thread URLs to match: {len(by_thread)}")

    # Launch browser with persistent profile
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            viewport=VIEWPORT,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        url = f"https://old.reddit.com/user/{USERNAME}/comments/"
        all_comments = []
        page_num = 0
        matched = 0

        while url:
            page_num += 1
            print(f"\nPage {page_num}: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            comments = scrape_comments_page(page)
            if not comments:
                print("  No comments found on page, stopping.")
                break

            print(f"  Found {len(comments)} comments")
            all_comments.extend(comments)

            # Try to match each comment
            for c in comments:
                norm = normalize_thread_url(c['thread_url'])
                candidates = by_thread.get(norm, [])
                if not candidates:
                    continue

                # Find best match by content similarity
                best_match = None
                best_score = 0.3  # minimum threshold
                for cand in candidates:
                    score = content_similarity(c['body_snippet'], cand['content'])
                    if score > best_score:
                        best_score = score
                        best_match = cand

                if best_match:
                    permalink = c['permalink']
                    if not permalink.startswith('http'):
                        permalink = 'https://old.reddit.com' + permalink

                    print(f"  MATCH id={best_match['id']} score={best_score:.2f} -> {permalink}")
                    db.execute("UPDATE posts SET our_url = %s WHERE id = %s", (permalink, best_match['id']))
                    matched += 1
                    # Remove matched candidate
                    candidates.remove(best_match)
                    if not candidates:
                        del by_thread[norm]

            db.commit()

            # Get next page
            next_url = get_next_page_url(page)
            if next_url and next_url != url:
                url = next_url
                time.sleep(1)
            else:
                print("\nNo more pages.")
                break

        context.close()

    print(f"\nDone! Scraped {len(all_comments)} comments across {page_num} pages.")
    print(f"Matched and updated {matched} posts.")

    # Check remaining
    cur2 = db.execute("""
        SELECT COUNT(*) FROM posts
        WHERE platform = 'reddit' AND status = 'active'
          AND (our_url IS NULL OR our_url = '' OR our_url NOT LIKE 'http%%')
    """)
    remaining = cur2.fetchone()[0]
    print(f"Still missing URLs: {remaining}")
    db.close()


if __name__ == "__main__":
    main()
