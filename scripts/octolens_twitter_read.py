#!/usr/bin/env python3
import sys, json
from playwright.sync_api import sync_playwright

urls = sys.argv[1:]
if not urls:
    print("Usage: python3 octolens_twitter_read.py <url1> <url2> ..."); sys.exit(1)

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        '/Users/matthewdi/.claude/browser-profiles/twitter',
        headless=False,
        viewport={'width': 911, 'height': 1016},
        args=['--window-position=3042,-1032', '--window-size=911,1016']
    )

    for url in urls:
        page = browser.new_page()
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=15000)
            page.wait_for_timeout(4000)

            content = page.evaluate('''() => {
                const tweets = document.querySelectorAll('[data-testid="tweet"]');
                const results = [];
                for (let i = 0; i < Math.min(tweets.length, 10); i++) {
                    const t = tweets[i];
                    const author = t.querySelector('[data-testid="User-Name"]')?.textContent || '';
                    const text = t.querySelector('[data-testid="tweetText"]')?.textContent || '';
                    const likes = t.querySelector('[data-testid="like"]')?.getAttribute('aria-label') || '';
                    const replies = t.querySelector('[data-testid="reply"]')?.getAttribute('aria-label') || '';
                    results.push({ author, text, likes, replies });
                }
                return results;
            }''')

            print(f"\n=== THREAD: {url} ===")
            for i, t in enumerate(content):
                print(f"[{i}] {t['author']}")
                print(f"    {t['text']}")
                print(f"    Likes: {t['likes']} | Replies: {t['replies']}")
        except Exception as e:
            print(f"Error loading {url}: {e}")
        page.close()

    browser.close()
