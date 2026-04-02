#!/usr/bin/env python3
"""Reddit CLI tools for Claude to call via Bash.

Commands:
    python3 scripts/reddit_tools.py search "security cameras" [--limit 10]
    python3 scripts/reddit_tools.py fetch <thread_url>
    python3 scripts/reddit_tools.py log-post <thread_url> <our_permalink> <our_text> <project> <thread_author> <thread_title>
    python3 scripts/reddit_tools.py already-posted <thread_url>
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

# Persistent rate limit file to share state across invocations
RATELIMIT_FILE = "/tmp/reddit_ratelimit.json"


def _read_ratelimit():
    try:
        with open(RATELIMIT_FILE) as f:
            return json.load(f)
    except Exception:
        return {"remaining": 100, "reset_at": 0}


def _write_ratelimit(remaining, reset_seconds):
    reset_at = time.time() + reset_seconds
    with open(RATELIMIT_FILE, "w") as f:
        json.dump({"remaining": remaining, "reset_at": reset_at}, f)


def _wait_if_needed():
    rl = _read_ratelimit()
    if rl["remaining"] <= 2 and rl["reset_at"] > time.time():
        wait = int(rl["reset_at"] - time.time()) + 2
        print(f"Rate limit near zero, waiting {wait}s...", file=sys.stderr)
        time.sleep(wait)


def _do_request(url):
    """Make a Reddit API request with rate limit handling."""
    _wait_if_needed()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        remaining = float(resp.headers.get("X-Ratelimit-Remaining", 100))
        reset = float(resp.headers.get("X-Ratelimit-Reset", 0))
        _write_ratelimit(remaining, reset)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            reset = float(e.headers.get("X-Ratelimit-Reset", 60))
            _write_ratelimit(0, reset)
            print(f"Rate limited. Waiting {int(reset)+2}s...", file=sys.stderr)
            time.sleep(int(reset) + 2)
            # Retry once
            resp = urllib.request.urlopen(req, timeout=20)
            remaining = float(resp.headers.get("X-Ratelimit-Remaining", 100))
            reset2 = float(resp.headers.get("X-Ratelimit-Reset", 0))
            _write_ratelimit(remaining, reset2)
            return json.loads(resp.read())
        raise


def cmd_search(args):
    """Search Reddit and return threads as JSON."""
    query = args.query
    encoded = urllib.parse.quote(query)
    url = f"https://old.reddit.com/search.json?q={encoded}&sort={args.sort}&limit={args.limit}&type=link"
    data = _do_request(url)

    # Load already-posted URLs for filtering
    dbmod.load_env()
    conn = dbmod.get_conn()
    cur = conn.execute("SELECT thread_url FROM posts WHERE thread_url IS NOT NULL")
    already_posted = {row[0] for row in cur.fetchall()}
    conn.close()

    threads = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        created = post.get("created_utc", 0)
        age_hours = (datetime.now(timezone.utc).timestamp() - created) / 3600 if created else 999
        permalink = f"https://old.reddit.com{post.get('permalink', '')}"
        already = permalink in already_posted
        entry = {
            "subreddit": f"r/{post.get('subreddit', '')}",
            "url": permalink,
            "title": post.get("title", ""),
            "author": post.get("author", ""),
            "score": post.get("score", 0),
            "num_comments": post.get("num_comments", 0),
            "age_hours": round(age_hours, 1),
            "selftext": post.get("selftext", "")[:300],
            "already_posted": already,
        }
        if already:
            entry["SKIP"] = ">>> ALREADY POSTED IN THIS THREAD - DO NOT POST AGAIN <<<"
        threads.append(entry)

    print(json.dumps(threads, indent=2))


def cmd_fetch(args):
    """Fetch a thread's comments via Reddit JSON API."""
    # Convert URL to .json endpoint
    url = args.url.rstrip("/")
    # Handle old.reddit.com or www.reddit.com
    if not url.endswith(".json"):
        url = url + ".json"
    url = url + "?limit=20&sort=top"

    data = _do_request(url)

    if not isinstance(data, list) or len(data) < 2:
        print(json.dumps({"error": "unexpected response format"}))
        return

    # Thread info
    thread_data = data[0]["data"]["children"][0]["data"]
    thread = {
        "title": thread_data.get("title", ""),
        "author": thread_data.get("author", ""),
        "selftext": thread_data.get("selftext", "")[:1000],
        "score": thread_data.get("score", 0),
        "num_comments": thread_data.get("num_comments", 0),
        "subreddit": f"r/{thread_data.get('subreddit', '')}",
        "url": args.url,
    }

    # Top comments (flatten one level)
    comments = []
    for child in data[1]["data"]["children"][:15]:
        if child.get("kind") != "t1":
            continue
        c = child.get("data", {})
        comment = {
            "id": c.get("name", ""),  # full thing ID like t1_abc123
            "author": c.get("author", ""),
            "body": c.get("body", "")[:500],
            "score": c.get("score", 0),
            "permalink": f"https://old.reddit.com{c.get('permalink', '')}",
        }
        comments.append(comment)

    print(json.dumps({"thread": thread, "comments": comments}, indent=2))


def cmd_already_posted(args):
    """Check if we already posted in a thread."""
    dbmod.load_env()
    conn = dbmod.get_conn()
    cur = conn.execute(
        "SELECT id, LEFT(our_content, 100) FROM posts WHERE platform='reddit' AND thread_url = %s LIMIT 1",
        [args.url],
    )
    row = cur.fetchone()
    conn.close()
    if row:
        print(json.dumps({"already_posted": True, "post_id": row[0], "content_preview": row[1]}))
    else:
        print(json.dumps({"already_posted": False}))


def cmd_log_post(args):
    """Log a posted comment to the database."""
    dbmod.load_env()
    conn = dbmod.get_conn()

    # Hard dedup: refuse to insert if we already posted in this thread
    cur = conn.execute(
        "SELECT id, LEFT(our_content, 100) FROM posts WHERE platform='reddit' AND thread_url = %s LIMIT 1",
        [args.thread_url],
    )
    existing = cur.fetchone()
    if existing:
        conn.close()
        print(json.dumps({"error": "DUPLICATE_THREAD", "message": "Already posted in this thread", "existing_post_id": existing[0], "content_preview": existing[1]}))
        return

    conn.execute(
        """INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
           thread_title, thread_content, our_url, our_content, our_account,
           source_summary, project_name, status, posted_at, feedback_report_used)
           VALUES ('reddit', %s, %s, %s, %s, '', %s, %s, %s, '', %s, 'active', NOW(), TRUE)""",
        [args.thread_url, args.thread_author, args.thread_author, args.thread_title,
         args.our_url, args.our_text, args.account, args.project],
    )
    conn.commit()
    conn.close()
    print(json.dumps({"logged": True}))


def main():
    parser = argparse.ArgumentParser(description="Reddit tools for Claude")
    sub = parser.add_subparsers(dest="command")

    # search
    p_search = sub.add_parser("search", help="Search Reddit for threads")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--limit", type=int, default=15, help="Max results")
    p_search.add_argument("--sort", default="new", help="Sort order (new, hot, relevance)")

    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch thread + comments")
    p_fetch.add_argument("url", help="Thread URL")

    # already-posted
    p_ap = sub.add_parser("already-posted", help="Check if already posted in thread")
    p_ap.add_argument("url", help="Thread URL")

    # log-post
    p_log = sub.add_parser("log-post", help="Log a posted comment to DB")
    p_log.add_argument("thread_url")
    p_log.add_argument("our_url")
    p_log.add_argument("our_text")
    p_log.add_argument("project")
    p_log.add_argument("thread_author")
    p_log.add_argument("thread_title")
    p_log.add_argument("--account", default="Deep_Ad1959")

    args = parser.parse_args()
    if args.command == "search":
        cmd_search(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "already-posted":
        cmd_already_posted(args)
    elif args.command == "log-post":
        cmd_log_post(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
