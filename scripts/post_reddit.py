#!/usr/bin/env python3
"""Reddit posting orchestrator.

Finds candidate threads and posts comments one at a time, each in its own
bare-mode Claude session. This avoids loading all MCP servers/skills/CLAUDE.md
and prevents context accumulation across multiple posts.

Usage:
    python3 scripts/post_reddit.py
    python3 scripts/post_reddit.py --dry-run          # Print prompt for first candidate
    python3 scripts/post_reddit.py --limit 3           # Post at most 3 comments
    python3 scripts/post_reddit.py --timeout 3600      # Global timeout in seconds
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

REPO_DIR = os.path.expanduser("~/social-autoposter")
CONFIG_PATH = os.path.join(REPO_DIR, "config.json")
REDDIT_MCP_CONFIG = os.path.expanduser("~/.claude/browser-agent-configs/reddit-agent-mcp.json")
API_KEY_KEYCHAIN_SERVICE = "Anthropic API Key Fazm"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_api_key():
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", API_KEY_KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def ensure_mcp_config():
    if os.path.exists(REDDIT_MCP_CONFIG):
        return REDDIT_MCP_CONFIG
    claude_json = os.path.expanduser("~/.claude.json")
    if os.path.exists(claude_json):
        with open(claude_json) as f:
            data = json.load(f)
        reddit_cfg = data.get("mcpServers", {}).get("reddit-agent")
        if reddit_cfg:
            mcp = {"mcpServers": {"reddit-agent": reddit_cfg}}
            os.makedirs(os.path.dirname(REDDIT_MCP_CONFIG), exist_ok=True)
            with open(REDDIT_MCP_CONFIG, "w") as f:
                json.dump(mcp, f, indent=2)
            return REDDIT_MCP_CONFIG
    return None


def pick_project(platform="reddit"):
    """Use pick_project.py to select the next project."""
    try:
        result = subprocess.run(
            ["python3", os.path.join(REPO_DIR, "scripts", "pick_project.py"),
             "--platform", platform, "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except Exception:
        pass
    return None


def get_top_performers(project_name, platform="reddit"):
    """Get top performers report for feedback."""
    try:
        result = subprocess.run(
            ["python3", os.path.join(REPO_DIR, "scripts", "top_performers.py"),
             "--platform", platform, "--project", project_name],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "(top performers report unavailable)"


_ratelimit_remaining = None
_ratelimit_reset = None


def search_reddit(query, sort="new", limit=25, user_agent="social-autoposter/1.0"):
    """Search Reddit for threads matching a query. Respects rate limit headers."""
    global _ratelimit_remaining, _ratelimit_reset
    import urllib.request
    import urllib.parse
    from datetime import datetime, timezone

    # If we know we're out of requests, wait for reset
    if _ratelimit_remaining is not None and _ratelimit_remaining <= 1 and _ratelimit_reset:
        wait = int(_ratelimit_reset) + 2
        print(f"[post_reddit] Rate limit near zero, waiting {wait}s for reset...")
        time.sleep(wait)

    encoded = urllib.parse.quote(query)
    url = f"https://old.reddit.com/search.json?q={encoded}&sort={sort}&limit={limit}&type=link"
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        # Read rate limit headers
        _ratelimit_remaining = float(resp.headers.get("X-Ratelimit-Remaining", 100))
        _ratelimit_reset = float(resp.headers.get("X-Ratelimit-Reset", 0))
        used = resp.headers.get("X-Ratelimit-Used", "?")
        print(f"[post_reddit] Rate limit: {_ratelimit_remaining:.0f} remaining, "
              f"{used} used, resets in {_ratelimit_reset:.0f}s")

        data = json.loads(resp.read())
        threads = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            created = post.get("created_utc", 0)
            age_hours = (datetime.now(timezone.utc).timestamp() - created) / 3600 if created else 999
            threads.append({
                "platform": "reddit",
                "subreddit": f"r/{post.get('subreddit', '')}",
                "url": f"https://old.reddit.com{post.get('permalink', '')}",
                "title": post.get("title", ""),
                "author": post.get("author", ""),
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "age_hours": round(age_hours, 1),
                "selftext": post.get("selftext", "")[:500],
            })
        return threads
    except urllib.error.HTTPError as e:
        if e.code == 429:
            reset = e.headers.get("X-Ratelimit-Reset", "?")
            print(f"[post_reddit] Rate limited on '{query}', resets in {reset}s. Waiting...")
            try:
                wait = int(float(reset)) + 2
                time.sleep(wait)
                # Retry once after waiting
                return search_reddit(query, sort, limit, user_agent)
            except (ValueError, TypeError):
                pass
        print(f"[post_reddit] search error for '{query}': {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[post_reddit] search error for '{query}': {e}", file=sys.stderr)
        return []


def find_threads_by_search(project, config):
    """Search Reddit using project topics instead of fetching all subreddits.

    Makes ~N API calls (one per topic) instead of ~133 (one per subreddit).
    """
    import random
    topics = project.get("topics", [])
    if not topics:
        print("[post_reddit] WARNING: project has no topics, can't search")
        return []

    # Shuffle topics so different runs explore different queries
    topics = list(topics)
    random.shuffle(topics)

    reddit_username = config.get("accounts", {}).get("reddit", {}).get("username", "")
    user_agent = f"social-autoposter/1.0 (u/{reddit_username})" if reddit_username else "social-autoposter/1.0"

    # Load already-posted URLs and exclusions for filtering
    conn = dbmod.get_conn()
    already_posted = set()
    cur = conn.execute("SELECT thread_url FROM posts WHERE thread_url IS NOT NULL")
    already_posted = {row[0] for row in cur.fetchall()}

    exclusions = config.get("exclusions", {})
    excluded_authors = {a.lower() for a in exclusions.get("authors", [])}
    excluded_keywords = [k.lower() for k in exclusions.get("keywords", [])]
    excluded_subs = {s.lower().lstrip("r/") for s in exclusions.get("subreddits", [])}
    conn.close()

    all_threads = []
    seen_urls = set()

    for topic in topics:
        threads = search_reddit(topic, user_agent=user_agent)
        for t in threads:
            # Dedup within this run
            if t["url"] in seen_urls:
                continue
            seen_urls.add(t["url"])
            # Already posted
            if t["url"] in already_posted:
                continue
            # Excluded author
            if t["author"].lower() in excluded_authors:
                continue
            # Excluded subreddit
            if t["subreddit"].lower().lstrip("r/") in excluded_subs:
                continue
            # Excluded keywords
            text = f"{t['title']} {t['selftext']}".lower()
            if any(kw in text for kw in excluded_keywords):
                continue
            all_threads.append(t)

        print(f"[post_reddit] Searched '{topic}': {len(threads)} results, {len(all_threads)} candidates total")
        time.sleep(2)  # Rate limit: 2s between searches

        # Stop early if we have plenty of candidates
        if len(all_threads) >= 50:
            break

    # Sort: prefer threads with some engagement but not too old
    all_threads.sort(key=lambda t: (t["num_comments"] > 0, t["score"]), reverse=True)
    return all_threads


def check_already_posted(conn, thread_url):
    """Check if we already posted in this thread."""
    cur = conn.execute(
        "SELECT id, LEFT(our_content, 80) FROM posts "
        "WHERE platform='reddit' AND thread_url = %s LIMIT 1",
        [thread_url],
    )
    row = cur.fetchone()
    return row is not None


def get_recent_comments(conn, limit=5):
    """Fetch our last N Reddit comments for repetition checking."""
    cur = conn.execute(
        "SELECT LEFT(our_content, 150) FROM posts "
        "WHERE platform='reddit' ORDER BY id DESC LIMIT %s",
        [limit],
    )
    return [row[0] for row in cur.fetchall()]


def build_prompt(thread, project, top_report, recent_comments, config):
    """Build a minimal prompt for posting one comment."""
    reddit_username = config.get("accounts", {}).get("reddit", {}).get("username", "Deep_Ad1959")
    content_angle = config.get("content_angle", "")

    # Use project-specific content_angle if available
    if project.get("content_angle"):
        content_angle = project["content_angle"]

    thread_json = json.dumps(thread, indent=2)
    project_json = json.dumps({k: project.get(k) for k in
        ["name", "description", "website", "github", "topics", "features"]
        if project.get(k)}, indent=2)

    recent_ctx = ""
    if recent_comments:
        snippets = "\n".join(f"  - {c}" for c in recent_comments)
        recent_ctx = f"""
Your last {len(recent_comments)} comments (don't repeat talking points):
{snippets}
"""

    top_ctx = ""
    if top_report and top_report != "(top performers report unavailable)":
        # Truncate to keep prompt small
        lines = top_report.split("\n")[:30]
        top_ctx = f"""
## Feedback from past performance (use to write better comments):
{chr(10).join(lines)}
"""

    return f"""Post a comment on this Reddit thread. You are the Social Autoposter.

## Thread
{thread_json}

## Project: {project.get('name', 'general')}
{project_json}

## Content angle
{content_angle}
{recent_ctx}{top_ctx}
## Content rules
- Write like texting a coworker. Lowercase OK, fragments OK.
- First person, specific details from the content angle above.
- NO em dashes. Use commas, periods, or regular dashes (-).
- No markdown in Reddit (no ##, **, numbered lists).
- Include imperfections: contractions, casual asides, occasional lowercase.
- Vary openings. Don't always start with credentials.
- 2-3 sentences. Reply to a high-upvote comment for visibility, not just OP.
- No product links in top-level comments. Earn attention first.
- If the thread doesn't connect to the content angle, output SKIP and stop.

## Anti-AI-detection
- No em dashes, no markdown headers/bold/lists
- Contains at least one imperfection
- Reads like a real person, not an essay
- Not too long, 2-4 short sentences max

## Execution steps

1. Read the thread: use mcp__reddit-agent__browser_navigate to {thread['url']}
   Check tone, length, top comments. Find the best comment to reply to.

2. Draft your comment (2-3 sentences, following rules above).
   If nothing fits naturally, output SKIP and stop.

3. Post via mcp__reddit-agent__browser_run_code. Find the target comment's thing ID
   from the page, then use this pattern:
```javascript
async (page) => {{
  const OUR_USERNAME = '{reddit_username}';
  const thing = await page.$('#thing_COMMENT_THING_ID');
  if (!thing) return 'ERROR: comment not found';
  const existingReplies = await thing.$$('.child .comment');
  for (const r of existingReplies) {{
    const author = await r.$eval('.author', el => el.textContent).catch(() => '');
    if (author === OUR_USERNAME) return 'already_replied';
  }}
  await thing.evaluate(el => {{
    const btn = el.querySelector('.flat-list a[onclick*="reply"]');
    if (btn) btn.click();
  }});
  await page.waitForSelector('#thing_COMMENT_THING_ID .usertext-edit textarea', {{ timeout: 3000 }});
  const textarea = await thing.$('.usertext-edit textarea');
  await textarea.fill(REPLY_TEXT_HERE);
  await thing.evaluate(el => {{
    const btn = el.querySelector('.usertext-edit button.save, .usertext-edit .save');
    if (btn) btn.click();
  }});
  await page.waitForTimeout(2000);
  const newComments = await thing.$$('.child .comment .bylink');
  return newComments.length > 0 ? await newComments[newComments.length - 1].getAttribute('href') : null;
}}
```
   Replace COMMENT_THING_ID with the actual Reddit thing ID (e.g. t1_abc123 or t3_xyz for OP).
   Replace REPLY_TEXT_HERE with your drafted text as a JS string literal.
   Use thing.evaluate() for clicks (NOT direct .click()).

4. After posting, log to database:
```bash
source ~/social-autoposter/.env
psql "$DATABASE_URL" -c "INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle, thread_title, thread_content, our_url, our_content, our_account, source_summary, project_name, status, posted_at, feedback_report_used) VALUES ('reddit', 'THREAD_URL', 'THREAD_AUTHOR', 'THREAD_AUTHOR', 'THREAD_TITLE', '', 'OUR_PERMALINK', 'OUR_COMMENT_TEXT', '{reddit_username}', '', '{project.get('name', 'general')}', 'active', NOW(), TRUE);"
```
   Replace placeholders with actual values. Escape single quotes in text by doubling them.

5. Close the browser tab: mcp__reddit-agent__browser_tabs with action 'close'.

6. Output DONE when finished, or SKIP if no good angle.

CRITICAL: Use ONLY mcp__reddit-agent__* tools. NEVER use generic tools.
CRITICAL: If browser times out, wait 30s and retry up to 3 times.
"""


def run_claude(prompt, timeout=300):
    """Run claude -p in bare mode. Returns (success, output, usage)."""
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_create": 0, "cost_usd": 0.0}
    mcp_config = ensure_mcp_config()
    cmd = ["claude", "-p", "--output-format", "json", "--bare"]
    if mcp_config:
        cmd += ["--strict-mcp-config", "--mcp-config", mcp_config]
    cmd += ["--tools", "Bash,Read"]
    env = os.environ.copy()
    api_key = get_api_key()
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    try:
        result = subprocess.run(
            cmd, env=env, input=prompt,
            capture_output=True, text=True, timeout=timeout,
        )
        try:
            data = json.loads(result.stdout)
            usage["cost_usd"] = data.get("total_cost_usd", 0.0)
            u = data.get("usage", {})
            usage["input_tokens"] = u.get("input_tokens", 0)
            usage["output_tokens"] = u.get("output_tokens", 0)
            usage["cache_read"] = u.get("cache_read_input_tokens", 0)
            usage["cache_create"] = u.get("cache_creation_input_tokens", 0)
            text_output = data.get("result", "")
        except (json.JSONDecodeError, TypeError):
            text_output = result.stdout
        return result.returncode == 0, text_output + result.stderr, usage
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT", usage
    except Exception as e:
        return False, str(e), usage


def main():
    parser = argparse.ArgumentParser(description="Reddit posting (one thread at a time)")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt for first candidate")
    parser.add_argument("--limit", type=int, default=1, help="Max comments to post (default: 1)")
    parser.add_argument("--timeout", type=int, default=3600, help="Global timeout in seconds")
    parser.add_argument("--per-post-timeout", type=int, default=300, help="Timeout per claude session")
    parser.add_argument("--project", default=None, help="Override project selection")
    args = parser.parse_args()

    dbmod.load_env()
    conn = dbmod.get_conn()
    config = load_config()

    start_time = time.time()
    posted = 0
    skipped = 0
    failed = 0
    total_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_create": 0, "cost_usd": 0.0}

    # Pick project
    if args.project:
        project = None
        for p in config.get("projects", []):
            if p["name"].lower() == args.project.lower():
                project = p
                break
        if not project:
            print(f"[post_reddit] ERROR: project '{args.project}' not found")
            sys.exit(1)
    else:
        project = pick_project("reddit")
        if not project:
            print("[post_reddit] ERROR: could not pick project")
            sys.exit(1)

    project_name = project.get("name", "general")
    print(f"[post_reddit] Project: {project_name}")

    # Get feedback
    top_report = get_top_performers(project_name)

    # Find threads by searching project topics (not fetching all subreddits)
    threads = find_threads_by_search(project, config)
    if not threads:
        print("[post_reddit] No candidate threads found. Done.")
        return

    print(f"[post_reddit] Found {len(threads)} candidate threads")

    # Get recent comments for repetition check
    recent_comments = get_recent_comments(conn)

    for thread in threads:
        if args.limit and posted >= args.limit:
            break
        if time.time() - start_time > args.timeout:
            print(f"[post_reddit] Global timeout reached ({args.timeout}s). Stopping.")
            break

        # Dedup check
        if check_already_posted(conn, thread["url"]):
            print(f"[post_reddit] Skipping (already posted): {thread['url']}")
            skipped += 1
            continue

        prompt = build_prompt(thread, project, top_report, recent_comments, config)

        if args.dry_run:
            print(f"=== DRY RUN: Prompt for thread ===")
            print(f"Thread: {thread['title'][:80]}")
            print(f"URL: {thread['url']}")
            print(f"Prompt length: {len(prompt)} chars")
            print(prompt)
            print("=== END DRY RUN ===")
            break

        post_start = time.time()
        print(f"[post_reddit] Posting on: {thread['title'][:60]}... ({thread['url']})")

        ok, output, usage = run_claude(prompt, timeout=args.per_post_timeout)
        elapsed = time.time() - post_start

        for k in total_usage:
            total_usage[k] += usage[k]

        if ok and "SKIP" not in output[:100]:
            posted += 1
            print(f"[post_reddit] Posted ({elapsed:.0f}s) "
                  f"[in={usage['input_tokens']} out={usage['output_tokens']} "
                  f"cache_r={usage['cache_read']} cache_w={usage['cache_create']} "
                  f"${usage['cost_usd']:.4f}]")
        elif "SKIP" in (output or "")[:100]:
            skipped += 1
            print(f"[post_reddit] Skipped (no good angle) ({elapsed:.0f}s) ${usage['cost_usd']:.4f}")
        else:
            failed += 1
            print(f"[post_reddit] FAILED ({elapsed:.0f}s): {(output or '')[:200]}")

        time.sleep(2)

    total_elapsed = time.time() - start_time
    print(f"\n[post_reddit] === SUMMARY ===")
    print(f"[post_reddit] posted={posted} skipped={skipped} failed={failed} elapsed={total_elapsed:.0f}s")
    print(f"[post_reddit] Total tokens: input={total_usage['input_tokens']} "
          f"output={total_usage['output_tokens']} "
          f"cache_read={total_usage['cache_read']} cache_create={total_usage['cache_create']}")
    print(f"[post_reddit] Total cost: ${total_usage['cost_usd']:.4f}")
    if posted > 0:
        print(f"[post_reddit] Avg cost per post: ${total_usage['cost_usd'] / posted:.4f}")

    conn.close()


if __name__ == "__main__":
    main()
