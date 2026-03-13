#!/usr/bin/env python3
"""Standalone poster — posts Reddit comments with minimal token usage.

Uses:
- find_threads.py for thread discovery (zero LLM tokens)
- Claude API for picking thread + drafting comment (one API call)
- Playwright Python for posting (zero LLM tokens)
- Direct SQL for logging (zero LLM tokens)

Usage:
    python3 scripts/standalone_poster.py --count 5 --force
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

# Add scripts dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")
ENV_PATH = os.path.expanduser("~/social-autoposter/.env")
PROMPT_DB_PATH = os.path.expanduser("~/claude-prompt-db/prompts.db")


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_env():
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())


def find_threads(force=False):
    """Run find_threads.py and return parsed JSON."""
    cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "find_threads.py"),
           "--include-moltbook"]
    if force:
        cmd.append("--force")
    result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    if result.returncode != 0:
        print(f"find_threads.py failed: {result.stderr}", file=sys.stderr)
        return None
    return json.loads(result.stdout)


def get_recent_posts(conn):
    """Get recent post content to avoid repeating angles."""
    cur = conn.execute(
        "SELECT our_content FROM posts ORDER BY id DESC LIMIT 5"
    )
    return [row[0] for row in cur.fetchall() if row[0]]


def pick_and_draft(threads, recent_posts, config, model="claude-sonnet-4-5-20250514"):
    """Single Claude API call to pick thread + draft comment."""
    import anthropic

    client = anthropic.Anthropic()

    # Build compact thread list
    thread_summaries = []
    for i, t in enumerate(threads[:15]):  # cap at 15 to save tokens
        thread_summaries.append(
            f"[{i}] r/{t.get('subreddit', t.get('platform', '?'))} | "
            f"{t['title'][:100]} | score:{t.get('score', 0)} comments:{t.get('num_comments', 0)} | "
            f"age:{t.get('age_hours', '?')}h\n"
            f"   {(t.get('selftext', '') or t.get('content', ''))[:300]}"
        )

    recent_str = "\n---\n".join(recent_posts[:5]) if recent_posts else "(none)"

    prompt = f"""Pick the best thread to comment on and draft a Reddit comment.

CONTENT ANGLE: {config.get('content_angle', '')}

THREADS:
{chr(10).join(thread_summaries)}

RECENT COMMENTS (don't repeat these angles):
{recent_str}

RULES:
- 2-3 sentences, first person, casual tone
- No em dashes. No markdown formatting. No lists.
- Write like texting a coworker. Sentence fragments fine.
- Include specific details from the content angle
- No product links
- If nothing fits naturally, respond with just "SKIP"

Respond in this exact JSON format:
{{"thread_index": 0, "comment": "your comment text here", "reason": "why this thread"}}
Or if nothing fits: {{"skip": true, "reason": "why"}}"""

    response = client.messages.create(
        model=model,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    # Extract JSON from response
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_tokens": getattr(response.usage, 'cache_read_input_tokens', 0) or 0,
        "cache_creation_tokens": getattr(response.usage, 'cache_creation_input_tokens', 0) or 0,
        "model": model,
    }

    return json.loads(text), usage, prompt, response.content[0].text


def post_to_reddit(thread_url, comment_text):
    """Post comment via Playwright Python (no LLM needed)."""
    from playwright.sync_api import sync_playwright

    # Convert to old.reddit.com
    url = thread_url.replace("www.reddit.com", "old.reddit.com")
    if "old.reddit.com" not in url:
        url = url.replace("reddit.com", "old.reddit.com")

    permalink = None

    with sync_playwright() as p:
        # Use existing Chrome profile for auth
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
        )
        # Use a persistent context to get logged-in cookies
        context = browser.new_context(
            storage_state=get_reddit_storage_state()
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        # Find the main comment textarea (top-level reply to post)
        textarea = page.locator('textarea[name="text"]').first
        textarea.click()
        textarea.fill(comment_text)
        time.sleep(0.5)

        # Click save button (the first one, which is the main reply)
        save_btn = textarea.locator("xpath=ancestor::form//button[contains(text(),'save')]")
        if save_btn.count() == 0:
            # Try alternative selector for old reddit
            save_btn = page.locator("form.usertext button.save").first
        save_btn.click()
        time.sleep(3)

        # Find our comment permalink
        # Look for the comment we just posted (most recent by our username)
        try:
            our_comment = page.locator("a.author:text('Deep_Ad1959')").first
            comment_thing = our_comment.locator("xpath=ancestor::div[contains(@class,'thing')]").first
            perm_link = comment_thing.locator("a.bylink").first
            permalink = perm_link.get_attribute("href")
        except Exception:
            # Fallback: just get any permalink near the bottom
            pass

        context.close()
        browser.close()

    return permalink


def get_reddit_storage_state():
    """Get or create Reddit storage state file."""
    state_path = os.path.expanduser("~/social-autoposter/.reddit-storage-state.json")
    if os.path.exists(state_path):
        return state_path

    # If no storage state, we need to login first
    print("No Reddit storage state found. Running login flow...")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://old.reddit.com/login", wait_until="domcontentloaded")
        print("Please log in to Reddit in the browser window...")
        print("Press Enter here when done...")
        input()
        context.storage_state(path=state_path)
        context.close()
        browser.close()

    return state_path


def log_to_prompt_db(session_id, turn_index, prompt, response_text, usage, model):
    """Log API call to prompt-db (same DB as Claude Code conversations)."""
    try:
        conn = sqlite3.connect(PROMPT_DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        now = datetime.now(timezone.utc).isoformat()
        turn_uuid = str(uuid.uuid4())

        # Upsert session
        conn.execute(
            """INSERT INTO sessions (session_id, project_slug, project_path, first_timestamp, last_timestamp, turn_count)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET last_timestamp=?, turn_count=turn_count+1""",
            (session_id, "standalone-poster", os.path.expanduser("~/social-autoposter"),
             now, now, 1, now)
        )

        # Insert turn
        conn.execute(
            """INSERT OR IGNORE INTO turns (
                uuid, session_id, turn_index, user_prompt, user_prompt_length,
                assistant_response, assistant_response_length, timestamp,
                project_slug, project_path, model,
                input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                backfilled_at, source_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (turn_uuid, session_id, turn_index,
             prompt, len(prompt),
             response_text, len(response_text),
             now,
             "standalone-poster", os.path.expanduser("~/social-autoposter"), model,
             usage.get("input_tokens", 0), usage.get("output_tokens", 0),
             usage.get("cache_read_tokens", 0), usage.get("cache_creation_tokens", 0),
             now, "standalone_poster.py")
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  Warning: could not log to prompt-db: {e}", file=sys.stderr)


def log_to_db(conn, platform, thread, our_url, our_content, account):
    """Log post to database."""
    conn.execute(
        """INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
           thread_title, thread_content, our_url, our_content, our_account,
           source_summary, status, posted_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', NOW())""",
        (platform, thread['url'], thread.get('author', ''), thread.get('author', ''),
         thread['title'], (thread.get('selftext', '') or thread.get('content', ''))[:500],
         our_url or '', our_content, account,
         'standalone-poster-test')
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Standalone poster — minimal token usage")
    parser.add_argument("--count", type=int, default=5, help="Number of comments to post")
    parser.add_argument("--force", action="store_true", help="Skip rate limit check")
    parser.add_argument("--dry-run", action="store_true", help="Draft but don't post")
    parser.add_argument("--model", default="claude-sonnet-4-5-20250514", help="Model to use")
    args = parser.parse_args()

    load_env()
    config = load_config()
    conn = dbmod.get_conn()
    account = config["accounts"]["reddit"]["username"]

    # Track totals
    total_usage = {"input_tokens": 0, "output_tokens": 0,
                   "cache_read_tokens": 0, "cache_creation_tokens": 0}
    posts_made = 0
    skipped = 0
    start_time = time.time()
    session_id = f"standalone-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    turn_index = 0

    print(f"=== Standalone Poster ===")
    print(f"Target: {args.count} comments | Model: {args.model} | Dry run: {args.dry_run}")
    print()

    # Step 1: Find threads (zero LLM tokens)
    print("[1/3] Finding threads via API...")
    t0 = time.time()
    data = find_threads(force=args.force)
    if not data:
        print("ERROR: Could not find threads")
        return
    threads = data.get("threads", [])
    print(f"  Found {len(threads)} candidates in {time.time()-t0:.1f}s")
    print(f"  Posts today: {data.get('posts_today', '?')}")

    if not threads:
        print("No candidate threads found. Exiting.")
        return

    # Get recent posts for dedup
    recent_posts = get_recent_posts(conn)
    posted_urls = set()

    for i in range(args.count):
        print(f"\n--- Comment {i+1}/{args.count} ---")

        # Filter out already-picked threads
        available = [t for t in threads if t['url'] not in posted_urls]
        if not available:
            print("No more available threads. Stopping.")
            break

        # Step 2: Pick + draft (one API call)
        print("[2/3] Picking thread + drafting comment...")
        t0 = time.time()
        try:
            result, usage, raw_prompt, raw_response = pick_and_draft(available, recent_posts, config, model=args.model)
        except Exception as e:
            print(f"  ERROR: {e}")
            skipped += 1
            continue

        # Accumulate usage
        for k in total_usage:
            total_usage[k] += usage.get(k, 0)

        # Log to prompt-db
        log_to_prompt_db(session_id, turn_index, raw_prompt, raw_response, usage, args.model)
        turn_index += 1

        api_time = time.time() - t0
        print(f"  API call: {usage['input_tokens']}in + {usage['output_tokens']}out = "
              f"{usage['input_tokens'] + usage['output_tokens']} tokens in {api_time:.1f}s")

        if result.get("skip"):
            print(f"  SKIP: {result.get('reason', 'no good match')}")
            skipped += 1
            continue

        idx = result.get("thread_index", 0)
        if idx >= len(available):
            idx = 0
        thread = available[idx]
        comment = result["comment"]

        print(f"  Thread: r/{thread.get('subreddit', '?')} — {thread['title'][:60]}")
        print(f"  Comment: {comment[:80]}...")
        print(f"  Reason: {result.get('reason', '')}")

        posted_urls.add(thread['url'])
        recent_posts.insert(0, comment)

        if args.dry_run:
            print("  [DRY RUN — not posting]")
            posts_made += 1
            continue

        # Step 3: Post via Playwright (zero LLM tokens)
        print("[3/3] Posting via Playwright...")
        t0 = time.time()
        try:
            permalink = post_to_reddit(thread['url'], comment)
            post_time = time.time() - t0
            print(f"  Posted in {post_time:.1f}s")
            if permalink:
                print(f"  Permalink: {permalink}")
        except Exception as e:
            print(f"  POST ERROR: {e}")
            skipped += 1
            continue

        # Step 4: Log to DB (zero LLM tokens)
        log_to_db(conn, "reddit", thread, permalink or "", comment, account)
        print("  Logged to DB")
        posts_made += 1

        # Small delay between posts
        if i < args.count - 1:
            time.sleep(2)

    # Final report
    elapsed = time.time() - start_time
    total_tokens = sum(total_usage.values())
    billed_input = total_usage["input_tokens"] + total_usage["cache_creation_tokens"]
    billed_output = total_usage["output_tokens"]

    # Cost calc (Sonnet: $3/M input, $15/M output)
    if "sonnet" in args.model:
        cost = (billed_input * 3 + billed_output * 15) / 1_000_000
    else:  # Opus
        cost = (billed_input * 15 + billed_output * 75) / 1_000_000

    print(f"\n{'='*50}")
    print(f"STANDALONE POSTER REPORT")
    print(f"{'='*50}")
    print(f"Posts made:       {posts_made}")
    print(f"Skipped:          {skipped}")
    print(f"Time:             {elapsed:.0f}s ({elapsed/max(posts_made,1):.0f}s per post)")
    print(f"")
    print(f"TOKEN USAGE:")
    print(f"  Input tokens:   {total_usage['input_tokens']:,}")
    print(f"  Output tokens:  {total_usage['output_tokens']:,}")
    print(f"  Cache read:     {total_usage['cache_read_tokens']:,}")
    print(f"  Cache creation: {total_usage['cache_creation_tokens']:,}")
    print(f"  Total tokens:   {total_tokens:,}")
    print(f"  Per post:       {total_tokens // max(posts_made, 1):,}")
    print(f"")
    print(f"ESTIMATED COST:")
    print(f"  Model:          {args.model}")
    print(f"  Total:          ${cost:.4f}")
    print(f"  Per post:       ${cost / max(posts_made, 1):.4f}")
    print(f"{'='*50}")

    conn.close()


if __name__ == "__main__":
    main()
