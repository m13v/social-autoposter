#!/usr/bin/env python3
"""Reddit CLI tools for Claude to call via Bash.

Commands:
    python3 scripts/reddit_tools.py search "security cameras" [--limit 10] [--sort relevance] [--time week]
    python3 scripts/reddit_tools.py search "automation" --subreddits AI_Agents,SaaS,smallbusiness --time month
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


class RateLimitedError(Exception):
    """Raised when Reddit API returns 429. Contains reset seconds."""
    def __init__(self, reset_seconds):
        self.reset_seconds = reset_seconds
        super().__init__(f"rate_limited_wait_{int(reset_seconds)}s")


# Maximum time a single tool invocation is allowed to wait for rate limit to clear.
# Longer waits are returned as errors so Claude can skip and try something else.
# 90s stays under Claude's default 120s bash timeout while absorbing the common
# short-reset case (resets are usually 10-60s after a single burst).
MAX_INLINE_WAIT_SECONDS = 90


def _wait_if_needed():
    rl = _read_ratelimit()
    if rl["remaining"] <= 2 and rl["reset_at"] > time.time():
        wait = int(rl["reset_at"] - time.time()) + 2
        if wait > MAX_INLINE_WAIT_SECONDS:
            raise RateLimitedError(wait)
        print(f"Rate limit near zero, waiting {wait}s...", file=sys.stderr)
        time.sleep(wait)


def _do_request(url):
    """Make a Reddit API request with rate limit handling.

    On 429: raises RateLimitedError immediately if the reset would require
    a long wait (>15s). Short waits are absorbed inline.
    """
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
            if reset > MAX_INLINE_WAIT_SECONDS:
                raise RateLimitedError(reset)
            print(f"Rate limited. Waiting {int(reset)+2}s...", file=sys.stderr)
            time.sleep(int(reset) + 2)
            # Retry once
            resp = urllib.request.urlopen(req, timeout=20)
            remaining = float(resp.headers.get("X-Ratelimit-Remaining", 100))
            reset2 = float(resp.headers.get("X-Ratelimit-Reset", 0))
            _write_ratelimit(remaining, reset2)
            return json.loads(resp.read())
        raise


def batch_fetch_info(thing_ids, user_agent=USER_AGENT):
    """Fetch metadata for up to 100 Reddit thing IDs in a single API call.

    Args:
        thing_ids: list of full thing IDs like ["t3_abc123", "t3_def456", "t1_xyz"]
        user_agent: User-Agent header

    Returns:
        dict mapping thing_id -> post/comment data dict
    """
    results = {}
    # Process in chunks of 100 (Reddit's max per request)
    for i in range(0, len(thing_ids), 100):
        chunk = thing_ids[i:i + 100]
        ids_str = ",".join(chunk)
        url = f"https://old.reddit.com/api/info.json?id={ids_str}"
        _wait_if_needed()
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            remaining = float(resp.headers.get("X-Ratelimit-Remaining", 100))
            reset = float(resp.headers.get("X-Ratelimit-Reset", 0))
            _write_ratelimit(remaining, reset)
            data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                reset = float(e.headers.get("X-Ratelimit-Reset", 60))
                _write_ratelimit(0, reset)
                if reset > MAX_INLINE_WAIT_SECONDS:
                    raise RateLimitedError(reset)
                print(f"Rate limited. Waiting {int(reset)+2}s...", file=sys.stderr)
                time.sleep(int(reset) + 2)
                resp = urllib.request.urlopen(req, timeout=30)
                remaining = float(resp.headers.get("X-Ratelimit-Remaining", 100))
                reset2 = float(resp.headers.get("X-Ratelimit-Reset", 0))
                _write_ratelimit(remaining, reset2)
                data = json.loads(resp.read())
            else:
                raise

        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            name = d.get("name", "")
            results[name] = d

    return results


def _load_comment_blocked_subs():
    """Load subreddits where we cannot post comments.

    Reads subreddit_bans.comment_blocked plus exclusions.subreddits. Used by
    search/fetch so the comment-drafting agent never sees these subs as
    candidates in the first place.

    subreddit_bans.thread_blocked is NOT read here — a sub can block new
    thread creation while still allowing comments, so it must not leak into
    the comment pipeline.
    """
    try:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
        with open(config_path) as f:
            config = json.load(f)
        blocked = set()
        bans = config.get("subreddit_bans") or {}
        if isinstance(bans, dict):
            for s in bans.get("comment_blocked") or []:
                blocked.add(s.lower())
        blocked.update(s.lower() for s in config.get("exclusions", {}).get("subreddits", []))
        return blocked
    except Exception:
        return set()


def _load_config_subreddits():
    """Load the subreddit list from config.json for scoped searches."""
    try:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
        with open(config_path) as f:
            config = json.load(f)
        return config.get("subreddits", [])
    except Exception:
        return []


def _build_search_url(query, sort, limit, time_filter, subreddits=None):
    """Build Reddit search URL with optional subreddit scoping."""
    quality_suffix = " self:yes nsfw:no"
    full_query = query + quality_suffix
    encoded = urllib.parse.quote(full_query)
    params = f"q={encoded}&sort={sort}&t={time_filter}&limit={limit}&type=link&raw_json=1"
    if subreddits:
        multi_sub = "+".join(subreddits)
        return f"https://www.reddit.com/r/{multi_sub}/search.json?{params}&restrict_sr=on"
    return f"https://www.reddit.com/search.json?{params}"


def _parse_search_results(data, already_posted, blocked_subs):
    """Parse Reddit search JSON into thread list."""
    threads = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        subreddit = post.get("subreddit", "").lower()
        if subreddit in blocked_subs:
            continue
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
        if age_hours > 4320 or post.get("archived"):
            continue
        if post.get("locked"):
            continue
        threads.append(entry)
    return threads


def cmd_search(args):
    """Search Reddit and return threads as JSON.

    Uses sort=relevance by default for topically relevant results.
    Supports --subreddits to scope search to specific subs via restrict_sr.
    Supports --time to filter by recency (hour, day, week, month, year, all).
    """
    query = args.query
    time_filter = args.time

    # Load already-posted URLs for filtering
    dbmod.load_env()
    conn = dbmod.get_conn()
    cur = conn.execute("SELECT thread_url FROM posts WHERE thread_url IS NOT NULL")
    already_posted = {row[0] for row in cur.fetchall()}
    conn.close()

    blocked_subs = _load_comment_blocked_subs()

    # Determine subreddit scoping
    target_subs = None
    if args.subreddits:
        target_subs = [s.lstrip("r/") for s in args.subreddits.split(",")]

    url = _build_search_url(query, args.sort, args.limit, time_filter, subreddits=target_subs)
    data = _do_request(url)
    threads = _parse_search_results(data, already_posted, blocked_subs)

    print(json.dumps(threads, indent=2))


def _html_postable_check(thread_url):
    """Second-opinion check against old.reddit.com HTML.

    Reddit's JSON `locked` and `archived` flags sometimes miss HTML-only
    lock states. Concretely seen on r/Entrepreneur where AutoMod renders
    `.locked-tagline` on the thread page while the JSON payload reports
    `locked=false`. This is cheap: one unauthenticated GET, ~1s, counts
    against the same rate-limit window as the JSON call above.

    Returns one of: "locked", "archived", "ok", or None on network error.
    """
    import re as _re
    try:
        url = thread_url.replace("www.reddit.com", "old.reddit.com").rstrip("/") + "/"
        _wait_if_needed()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        resp = urllib.request.urlopen(req, timeout=15)
        remaining = float(resp.headers.get("X-Ratelimit-Remaining", 100))
        reset = float(resp.headers.get("X-Ratelimit-Reset", 0))
        _write_ratelimit(remaining, reset)
        html = resp.read().decode("utf-8", errors="ignore")
        # Match only the tagline CSS classes, not the archived-popup template
        # that old.reddit.com preloads on every page.
        if _re.search(r'class="[^"]*\blocked-tagline\b', html):
            return "locked"
        if _re.search(r'class="[^"]*\barchived-tagline\b', html):
            return "archived"
        return "ok"
    except Exception:
        return None


def cmd_fetch(args):
    """Fetch a thread's comments via Reddit JSON API."""
    # Check if subreddit is blocked
    import re as _re
    sub_match = _re.search(r'/r/([^/]+)', args.url)
    if sub_match:
        blocked = _load_comment_blocked_subs()
        if sub_match.group(1).lower() in blocked:
            print(json.dumps({"error": "subreddit_blocked", "subreddit": sub_match.group(1)}))
            return

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

    if thread_data.get("archived") or thread_data.get("locked"):
        status = "archived" if thread_data.get("archived") else "locked"
        print(json.dumps({"error": f"thread_{status}", "thread": thread}))
        return

    html_state = _html_postable_check(args.url)
    if html_state in ("locked", "archived"):
        print(json.dumps({"error": f"thread_{html_state}", "thread": thread,
                          "detected_via": "html"}))
        return

    # Top comments (flatten one level)
    comments = []
    for child in data[1]["data"]["children"][:15]:
        if child.get("kind") != "t1":
            continue
        c = child.get("data", {})
        comment = {
            "id": c.get("name", ""),  # full thing ID like t1_abc123
            "author": c.get("author", ""),
            "body": c.get("body", "")[:1500],
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

    session_id = os.environ.get("CLAUDE_SESSION_ID") or None
    cur = conn.execute(
        """INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
           thread_title, thread_content, our_url, our_content, our_account,
           source_summary, project_name, status, posted_at, feedback_report_used, engagement_style, claude_session_id, search_topic)
           VALUES ('reddit', %s, %s, %s, %s, '', %s, %s, %s, '', %s, 'active', NOW(), TRUE, %s, %s, %s)
           RETURNING id""",
        [args.thread_url, args.thread_author, args.thread_author, args.thread_title,
         args.our_url, args.our_text, args.account, args.project,
         getattr(args, 'engagement_style', None), session_id,
         getattr(args, 'search_topic', None)],
    )
    new_post_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    print(json.dumps({"logged": True, "post_id": new_post_id, "claude_session_id": session_id}))


def main():
    parser = argparse.ArgumentParser(description="Reddit tools for Claude")
    sub = parser.add_subparsers(dest="command")

    # search
    p_search = sub.add_parser("search", help="Search Reddit for threads")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--limit", type=int, default=15, help="Max results")
    p_search.add_argument("--sort", default="relevance", help="Sort order (relevance, new, hot, top, comments)")
    p_search.add_argument("--time", default="week", help="Time filter (hour, day, week, month, year, all)")
    p_search.add_argument("--subreddits", default=None, help="Comma-separated subreddits to scope search (e.g. AI_Agents,SaaS,smallbusiness)")

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
    p_log.add_argument("--engagement-style", default=None)
    p_log.add_argument("--search-topic", dest="search_topic", default=None,
                       help="The seed topic/query used to find this thread (feedback loop input)")

    args = parser.parse_args()
    try:
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
    except RateLimitedError as e:
        # Return a clean JSON error so Claude can skip and try another action
        print(json.dumps({
            "error": "rate_limited",
            "wait_seconds": int(e.reset_seconds),
            "message": f"Reddit API rate limit hit. Skip this query and try a different topic or command.",
        }))
        sys.exit(2)


if __name__ == "__main__":
    main()
