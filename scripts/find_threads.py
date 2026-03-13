#!/usr/bin/env python3
"""Find candidate threads to comment on via Reddit JSON API + Moltbook API.

No browser needed — uses public APIs only.
Outputs JSON array of candidate threads.

Usage:
    python3 scripts/find_threads.py [--db PATH] [--subreddits r/ClaudeAI,r/programming]
    python3 scripts/find_threads.py --topic "macOS automation"
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def fetch_json(url, headers=None, user_agent="social-autoposter/1.0"):
    hdrs = {"User-Agent": user_agent}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}", file=sys.stderr)
        return None


def get_already_posted():
    """Return set of thread URLs we've already posted in."""
    conn = dbmod.get_conn()
    rows = conn.execute("SELECT thread_url FROM posts WHERE thread_url IS NOT NULL").fetchall()
    conn.close()
    return {row[0] for row in rows}


def get_recent_posts(limit=5):
    """Return our last N post contents for repetition checking."""
    conn = dbmod.get_conn()
    rows = conn.execute("SELECT our_content FROM posts ORDER BY id DESC LIMIT %s", [limit]).fetchall()
    conn.close()
    return [row[0] for row in rows]


def check_rate_limit(max_per_day=40):
    """Return (posts_today, can_post)."""
    conn = dbmod.get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE posted_at >= NOW() - INTERVAL '24 hours' AND platform != 'github_issues'"
    ).fetchone()
    conn.close()
    count = row[0]
    return count, count < max_per_day


def fetch_reddit_threads(subreddits, sort="new", limit=10, user_agent="social-autoposter/1.0"):
    """Fetch threads from subreddits via Reddit JSON API."""
    threads = []
    for sub in subreddits:
        sub = sub.lstrip("r/")
        url = f"https://old.reddit.com/r/{sub}/{sort}.json?limit={limit}"
        data = fetch_json(url, user_agent=user_agent)
        if not data or "data" not in data:
            continue

        for child in data["data"].get("children", []):
            post = child.get("data", {})
            created = post.get("created_utc", 0)
            age_hours = (datetime.now(timezone.utc).timestamp() - created) / 3600 if created else 999

            threads.append({
                "platform": "reddit",
                "subreddit": f"r/{sub}",
                "url": f"https://old.reddit.com{post.get('permalink', '')}",
                "title": post.get("title", ""),
                "author": post.get("author", ""),
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "age_hours": round(age_hours, 1),
                "selftext": post.get("selftext", "")[:500],
            })
        time.sleep(5)

    return threads


def fetch_moltbook_threads(api_key, limit=10):
    """Fetch threads from Moltbook REST API."""
    if not api_key:
        return []

    data = fetch_json(
        f"https://www.moltbook.com/api/v1/posts?limit={limit}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if not data or "posts" not in data:
        return []

    threads = []
    for post in data["posts"]:
        threads.append({
            "platform": "moltbook",
            "url": f"https://www.moltbook.com/post/{post.get('uuid', post.get('id', ''))}",
            "title": post.get("title", ""),
            "author": post.get("author", {}).get("name", ""),
            "score": post.get("upvotes", 0),
            "num_comments": post.get("comment_count", 0),
            "content": post.get("content", "")[:500],
        })

    return threads


def filter_threads(threads, already_posted, topic=None):
    """Filter out already-posted threads and optionally filter by topic."""
    filtered = []
    for t in threads:
        if t["url"] in already_posted:
            t["skip_reason"] = "already_posted"
            continue
        if topic:
            text = f"{t.get('title', '')} {t.get('selftext', '')} {t.get('content', '')}".lower()
            if topic.lower() not in text:
                continue
        filtered.append(t)
    return filtered


def main():
    parser = argparse.ArgumentParser(description="Find candidate threads to comment on")
    parser.add_argument("--subreddits", default=None, help="Comma-separated subreddits (e.g. ClaudeAI,programming)")
    parser.add_argument("--topic", default=None, help="Filter threads by topic keyword")
    parser.add_argument("--sort", default="new", choices=["new", "hot", "top"], help="Reddit sort order")
    parser.add_argument("--limit", type=int, default=10, help="Threads per subreddit")
    parser.add_argument("--include-moltbook", action="store_true", help="Also search Moltbook")
    parser.add_argument("--force", action="store_true", help="Skip rate limit check")
    args = parser.parse_args()

    config = load_config()
    subreddits = args.subreddits.split(",") if args.subreddits else config.get("subreddits", [])
    reddit_username = config.get("accounts", {}).get("reddit", {}).get("username", "")
    user_agent = f"social-autoposter/1.0 (u/{reddit_username})" if reddit_username else "social-autoposter/1.0"

    # Rate limit check
    posts_today, can_post = check_rate_limit()
    if not can_post and not args.force:
        print(json.dumps({"error": "rate_limit", "posts_today": posts_today, "threads": []}))
        sys.exit(1)

    already_posted = get_already_posted()
    recent_posts = get_recent_posts()

    # Fetch threads
    threads = fetch_reddit_threads(subreddits, sort=args.sort, limit=args.limit, user_agent=user_agent)

    if args.include_moltbook:
        moltbook_key = os.environ.get("MOLTBOOK_API_KEY", "")
        threads.extend(fetch_moltbook_threads(moltbook_key))

    # Filter
    candidates = filter_threads(threads, already_posted, topic=args.topic)

    output = {
        "posts_today": posts_today,
        "can_post": can_post,
        "total_found": len(threads),
        "candidates": len(candidates),
        "recent_post_snippets": [p[:100] if p else "" for p in recent_posts],
        "threads": candidates,
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
