#!/usr/bin/env python3
"""Find candidate threads to comment on via Reddit JSON API + Moltbook API.

Also generates Twitter/LinkedIn search URLs for browser-based discovery.

Usage:
    python3 scripts/find_threads.py [--subreddits r/ClaudeAI,r/programming]
    python3 scripts/find_threads.py --topic "macOS automation"
    python3 scripts/find_threads.py --include-twitter --include-linkedin
    python3 scripts/find_threads.py --include-moltbook --include-twitter --include-linkedin
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


def get_engaged_linkedin_authors():
    """Return set of LinkedIn authors we've already commented on.

    LinkedIn batch commenting uses search result pages (not unique post URLs),
    so URL-based dedup doesn't work. This provides author-level dedup instead.
    """
    conn = dbmod.get_conn()
    rows = conn.execute(
        "SELECT LOWER(thread_author) FROM posts "
        "WHERE platform = 'linkedin' AND thread_author IS NOT NULL AND thread_author != ''"
    ).fetchall()
    conn.close()
    return {row[0] for row in rows}


def get_recent_posts(limit=5):
    """Return our last N post contents for repetition checking."""
    conn = dbmod.get_conn()
    rows = conn.execute("SELECT our_content FROM posts ORDER BY id DESC LIMIT %s", [limit]).fetchall()
    conn.close()
    return [row[0] for row in rows]


def check_rate_limit(max_per_day=None):
    """Return (posts_today, can_post). No limit by default."""
    conn = dbmod.get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE posted_at >= NOW() - INTERVAL '24 hours' AND platform != 'github_issues'"
    ).fetchone()
    conn.close()
    count = row[0]
    return count, True


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


def fetch_moltbook_threads(api_key, limit=50):
    """Fetch threads from Moltbook REST API.

    Fetches multiple pages and filters out spam (mint/token posts).
    """
    if not api_key:
        return []

    threads = []
    spam_patterns = ['mbc-20', 'mbc20', '"op":"mint"', '"tick"', 'pump.fun']
    spam_title_patterns = ['mint', 'mbc20', 'token launch', 'inscription', 'redx',
                           'wang ', 'bot claim', 'hackai']

    for offset in [0, 50]:
        data = fetch_json(
            f"https://www.moltbook.com/api/v1/posts?sort=new&limit={limit}&offset={offset}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if not data or "posts" not in data:
            break

        for post in data["posts"]:
            content = post.get("content", "")
            title = post.get("title", "")

            # Filter spam
            if any(p in content.lower() for p in spam_patterns):
                continue
            if any(p in title.lower() for p in spam_title_patterns):
                continue
            if len(content) < 40:
                continue

            threads.append({
                "platform": "moltbook",
                "url": f"https://www.moltbook.com/post/{post.get('uuid', post.get('id', ''))}",
                "title": title,
                "author": post.get("author", {}).get("name", ""),
                "score": post.get("upvotes", 0),
                "num_comments": post.get("comment_count", 0),
                "content": content[:500],
            })

    return threads


def generate_twitter_search_urls(topics, exclusions=None):
    """Generate X/Twitter search URLs for browser-based discovery.

    Twitter has no free public search API, so we generate search URLs
    that the agent browses via Playwright to find threads.
    """
    import urllib.parse

    excluded_accounts = set()
    if exclusions:
        excluded_accounts = {a.lower() for a in exclusions.get("twitter_accounts", [])}

    threads = []
    for topic in topics:
        # Build exclusion string for the query
        exclude_str = " ".join(f"-from:{acct}" for acct in excluded_accounts)
        query = f"{topic} {exclude_str}".strip()
        # min_faves:5 filters to tweets with some engagement
        search_url = f"https://x.com/search?q={urllib.parse.quote(query + ' min_faves:5')}&f=live"

        threads.append({
            "platform": "twitter",
            "url": search_url,
            "title": f"Search: {topic}",
            "author": "",
            "score": 0,
            "num_comments": 0,
            "discovery_method": "search_url",
            "search_topic": topic,
        })

    return threads


def generate_linkedin_search_urls(topics, exclusions=None):
    """Generate LinkedIn search URLs for browser-based discovery.

    LinkedIn has no public search API, so we generate content search URLs
    that the agent browses via Playwright to find posts.
    """
    import urllib.parse

    threads = []
    for topic in topics:
        search_url = f"https://www.linkedin.com/search/results/content/?keywords={urllib.parse.quote(topic)}&sortBy=%22date_posted%22"

        threads.append({
            "platform": "linkedin",
            "url": search_url,
            "title": f"Search: {topic}",
            "author": "",
            "score": 0,
            "num_comments": 0,
            "discovery_method": "search_url",
            "search_topic": topic,
        })

    return threads


def fetch_github_issues(search_topics, exclusions=None, limit=10):
    """Search GitHub issues using gh CLI and return candidate threads.

    Rotates through search_topics, picking a random subset each run.
    """
    import random
    import subprocess

    excluded_repos = set()
    excluded_authors = set()
    if exclusions:
        excluded_repos = {r.lower() for r in exclusions.get("github_repos", [])}
        excluded_authors = {a.lower() for a in exclusions.get("authors", [])}

    # Pick 5 random topics to rotate
    topics = random.sample(search_topics, min(5, len(search_topics)))
    threads = []

    for topic in topics:
        try:
            result = subprocess.run(
                ["gh", "search", "issues", topic, "--limit", "10",
                 "--state", "open", "--sort", "updated",
                 "--json", "url,title,author,repository"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                continue
            issues = json.loads(result.stdout) if result.stdout.strip() else []
        except Exception as e:
            print(f"  ERROR searching GitHub for '{topic}': {e}", file=sys.stderr)
            continue

        for issue in issues:
            repo_name = issue.get("repository", {}).get("nameWithOwner", "")
            author = issue.get("author", {}).get("login", "")

            # Apply exclusions
            if any(excl in repo_name.lower() for excl in excluded_repos):
                continue
            if author.lower() in excluded_authors:
                continue

            threads.append({
                "platform": "github_issues",
                "url": issue.get("url", ""),
                "title": issue.get("title", ""),
                "author": author,
                "score": 0,
                "num_comments": 0,
                "search_topic": topic,
                "repository": repo_name,
            })

        if len(threads) >= limit:
            break

    return threads[:limit]


def load_exclusions(config):
    """Load exclusion lists from config."""
    excl = config.get("exclusions", {})
    return {
        "authors": {a.lower() for a in excl.get("authors", [])},
        "subreddits": {s.lower().lstrip("r/") for s in excl.get("subreddits", [])},
        "urls": excl.get("urls", []),
        "keywords": [k.lower() for k in excl.get("keywords", [])],
    }


def is_excluded(thread, exclusions):
    """Check if a thread matches any exclusion rule."""
    # Author exclusion
    author = thread.get("author", "").lower()
    if author and author in exclusions["authors"]:
        return "excluded_author"

    # Subreddit exclusion
    sub = thread.get("subreddit", "").lower().lstrip("r/")
    if sub and sub in exclusions["subreddits"]:
        return "excluded_subreddit"

    # URL pattern exclusion
    url = thread.get("url", "")
    for pattern in exclusions["urls"]:
        if pattern in url:
            return "excluded_url"

    # Keyword exclusion (skip threads containing these keywords)
    if exclusions["keywords"]:
        text = f"{thread.get('title', '')} {thread.get('selftext', '')} {thread.get('content', '')}".lower()
        for kw in exclusions["keywords"]:
            if kw in text:
                return "excluded_keyword"

    return None


def filter_threads(threads, already_posted, topic=None, exclusions=None):
    """Filter out already-posted threads and optionally filter by topic."""
    if exclusions is None:
        exclusions = {"authors": set(), "subreddits": set(), "urls": [], "keywords": []}
    filtered = []
    for t in threads:
        if t["url"] in already_posted:
            t["skip_reason"] = "already_posted"
            continue
        excl_reason = is_excluded(t, exclusions)
        if excl_reason:
            t["skip_reason"] = excl_reason
            continue
        if topic and t.get("discovery_method") != "search_url":
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
    parser.add_argument("--include-twitter", action="store_true", help="Generate X/Twitter search URLs")
    parser.add_argument("--include-linkedin", action="store_true", help="Generate LinkedIn search URLs")
    parser.add_argument("--include-github", action="store_true", help="Search GitHub issues via gh CLI")
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

    # Pre-filter excluded subreddits before fetching (saves API calls)
    exclusions = load_exclusions(config)
    if exclusions["subreddits"]:
        subreddits = [s for s in subreddits if s.lower().lstrip("r/") not in exclusions["subreddits"]]

    # Fetch threads
    threads = fetch_reddit_threads(subreddits, sort=args.sort, limit=args.limit, user_agent=user_agent)

    if args.include_moltbook:
        moltbook_key = os.environ.get("MOLTBOOK_API_KEY", "")
        threads.extend(fetch_moltbook_threads(moltbook_key))

    if args.include_twitter:
        twitter_topics = config.get("twitter_topics", [])
        if args.topic:
            twitter_topics = [t for t in twitter_topics if args.topic.lower() in t.lower()]
        raw_excl = config.get("exclusions", {})
        threads.extend(generate_twitter_search_urls(twitter_topics, exclusions=raw_excl))

    if args.include_linkedin:
        linkedin_topics = config.get("linkedin_topics", [])
        if args.topic:
            linkedin_topics = [t for t in linkedin_topics if args.topic.lower() in t.lower()]
        raw_excl = config.get("exclusions", {})
        threads.extend(generate_linkedin_search_urls(linkedin_topics, exclusions=raw_excl))

    if args.include_github:
        github_topics = config.get("accounts", {}).get("github", {}).get("search_topics", [])
        if args.topic:
            github_topics = [t for t in github_topics if args.topic.lower() in t.lower()]
        raw_excl = config.get("exclusions", {})
        threads.extend(fetch_github_issues(github_topics, exclusions=raw_excl))

    # Filter
    candidates = filter_threads(threads, already_posted, topic=args.topic, exclusions=exclusions)

    output = {
        "posts_today": posts_today,
        "can_post": can_post,
        "total_found": len(threads),
        "candidates": len(candidates),
        "recent_post_snippets": [p[:100] if p else "" for p in recent_posts],
        "threads": candidates,
    }

    # Include engaged LinkedIn authors for dedup (author-level, not URL-level)
    if args.include_linkedin:
        output["engaged_linkedin_authors"] = sorted(get_engaged_linkedin_authors())
        output["engaged_linkedin_count"] = len(output["engaged_linkedin_authors"])

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
