#!/usr/bin/env python3
"""
Connect to running Twitter Chrome via CDP, read tweets, post replies, log to DB.
"""
import sys, os, time, json, psycopg2
from playwright.sync_api import sync_playwright

DB_URL = None
with open(os.path.expanduser('~/social-autoposter/.env')) as f:
    for line in f:
        if line.startswith('DATABASE_URL='):
            DB_URL = line.strip().split('=', 1)[1].strip('"').strip("'")

CDP_PORT = sys.argv[1] if len(sys.argv) > 1 else '55363'

TWEETS = [
    {
        "url": "https://x.com/silva_ngozi/status/2042025663754822096",
        "author": "silva_ngozi",
        "octolens_id": 162091429,
        "keyword": "manus ai",
        "reply": "if you're hitting credit limits on manus you might want to check out fazm, it runs locally on your mac so there's no usage cap. open source too so you can just grab it from github. I switched from cloud based agents to local ones and it's way less stressful not watching credits drain",
        "project_name": "Fazm"
    },
    {
        "url": "https://x.com/antonioventre_/status/2042005321044901941",
        "author": "antonioventre_",
        "octolens_id": 162078806,
        "keyword": "manus ai",
        "reply": "this is exactly why we built our agent to run fully local on your machine. no data leaves your computer, no one rewrites your copy or redesigns your visuals. the moment you hand control to a cloud platform they optimize for their interests not yours",
        "project_name": "Fazm"
    }
]

def post_reply(page, tweet):
    url = tweet["url"]
    reply_text = tweet["reply"]

    print(f"\nNavigating to {url}")
    page.goto(url, wait_until='domcontentloaded', timeout=20000)
    page.wait_for_timeout(4000)

    # Read thread context
    tweets_data = page.evaluate('''() => {
        const tweets = document.querySelectorAll('[data-testid="tweet"]');
        const results = [];
        for (let i = 0; i < Math.min(tweets.length, 5); i++) {
            const t = tweets[i];
            const author = t.querySelector('[data-testid="User-Name"]')?.textContent || '';
            const text = t.querySelector('[data-testid="tweetText"]')?.textContent || '';
            results.push({ author, text });
        }
        return results;
    }''')

    print("Thread context:")
    for i, t in enumerate(tweets_data):
        print(f"  [{i}] {t['author']}: {t['text'][:120]}")

    if not tweets_data:
        print("ERROR: Could not load tweet content. Skipping.")
        return None

    thread_content = tweets_data[0]['text'] if tweets_data else ''
    thread_title = thread_content[:100]

    # Click reply on the main tweet
    reply_btn = page.query_selector('[data-testid="tweet"] [data-testid="reply"]')
    if not reply_btn:
        print("ERROR: No reply button found. Skipping.")
        return None

    reply_btn.click()
    page.wait_for_timeout(2000)

    # Type reply
    reply_box = page.query_selector('[data-testid="tweetTextarea_0"]')
    if not reply_box:
        print("ERROR: No reply text box. Skipping.")
        return None

    reply_box.click()
    page.wait_for_timeout(500)
    page.keyboard.type(reply_text, delay=15)
    page.wait_for_timeout(1000)

    # Post
    post_btn = page.query_selector('[data-testid="tweetButton"], [data-testid="tweetButtonInline"]')
    if not post_btn:
        print("ERROR: No post button. Skipping.")
        return None

    post_btn.click()
    page.wait_for_timeout(3000)

    print(f"Reply posted to {url}")
    return {
        "thread_content": thread_content[:500],
        "thread_title": thread_title
    }

def main():
    print(f"Connecting to Chrome CDP on port {CDP_PORT}")
    conn = psycopg2.connect(DB_URL)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f'http://127.0.0.1:{CDP_PORT}')
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        for tweet in TWEETS:
            page = context.new_page()
            try:
                result = post_reply(page, tweet)
                if result:
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
                            thread_title, thread_content, our_url, our_content, our_account,
                            source_summary, project_name, status, posted_at)
                        VALUES ('twitter', %s, %s, %s, %s, %s, %s, %s, '@m13v_', %s, %s, 'active', NOW())
                        RETURNING id
                    """, (
                        tweet['url'],
                        tweet['author'],
                        tweet['author'],
                        result['thread_title'],
                        result['thread_content'],
                        tweet['url'],
                        tweet['reply'],
                        f"octolens: {tweet['keyword']}",
                        tweet['project_name']
                    ))
                    post_id = cur.fetchone()[0]
                    conn.commit()
                    print(f"Logged to DB: id={post_id}")
                else:
                    print(f"SKIPPED: {tweet['url']}")
            except Exception as e:
                print(f"Error: {e}")
                conn.rollback()
            finally:
                page.close()
            time.sleep(2)

        # Don't close the browser - it belongs to the other agent
        browser.close()  # disconnects CDP, doesn't close browser

    conn.close()
    print("\nDone.")

if __name__ == '__main__':
    main()
