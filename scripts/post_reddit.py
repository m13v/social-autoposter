#!/usr/bin/env python3
"""Reddit posting orchestrator.

Spawns a Claude session per post that uses reddit_tools.py (search, fetch) to find
threads and drafts replies. Python orchestrator handles CDP posting and DB logging.

Usage:
    python3 scripts/post_reddit.py
    python3 scripts/post_reddit.py --dry-run          # Print prompt without executing
    python3 scripts/post_reddit.py --limit 3           # Post at most 3 comments
    python3 scripts/post_reddit.py --timeout 3600      # Global timeout in seconds
    python3 scripts/post_reddit.py --project Cyrano    # Override project selection
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

REPO_DIR = os.path.expanduser("~/social-autoposter")
CONFIG_PATH = os.path.join(REPO_DIR, "config.json")
API_KEY_KEYCHAIN_SERVICE = "Anthropic API Key Fazm"
REDDIT_BROWSER = os.path.join(REPO_DIR, "scripts", "reddit_browser.py")
REDDIT_TOOLS = os.path.join(REPO_DIR, "scripts", "reddit_tools.py")


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


def pick_project(platform="reddit"):
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
    return ""


def get_recent_comments(limit=5):
    dbmod.load_env()
    conn = dbmod.get_conn()
    cur = conn.execute(
        "SELECT LEFT(our_content, 150) FROM posts "
        "WHERE platform='reddit' ORDER BY id DESC LIMIT %s",
        [limit],
    )
    results = [row[0] for row in cur.fetchall()]
    conn.close()
    return results


def build_prompt(project, config, limit, top_report, recent_comments):
    """Build prompt for Claude to search, evaluate, and draft replies (no posting)."""
    content_angle = project.get("content_angle", config.get("content_angle", ""))

    project_json = json.dumps({k: project.get(k) for k in
        ["name", "description", "website", "github", "topics", "features",
         "demo_video", "booking_link", "pricing"]
        if project.get(k)}, indent=2)

    topics_list = project.get("topics", [])

    recent_ctx = ""
    if recent_comments:
        snippets = "\n".join(f"  - {c}" for c in recent_comments)
        recent_ctx = f"""
Your last {len(recent_comments)} comments (don't repeat talking points):
{snippets}
"""

    top_ctx = ""
    if top_report:
        lines = top_report.split("\n")[:30]
        top_ctx = f"""
## Feedback from past performance:
{chr(10).join(lines)}
"""

    return f"""You are the Social Autoposter. Find {limit} relevant Reddit thread(s) and draft comment(s) for this project.

## Project: {project.get('name', 'general')}
{project_json}

## Content angle
{content_angle}
{recent_ctx}{top_ctx}

## Your tools (call via Bash)

1. **Search**: Find threads by topic
   python3 {REDDIT_TOOLS} search "QUERY" --limit 15
   Returns JSON array with subreddit, title, score, selftext preview, already_posted flag.

2. **Fetch thread**: Get full thread + top comments
   python3 {REDDIT_TOOLS} fetch "THREAD_URL"
   Returns thread info + top 15 comments with their thing IDs and body text.

3. **Check dedup**: Check if we already posted in a thread
   python3 {REDDIT_TOOLS} already-posted "THREAD_URL"

## Workflow

1. Search for 2-3 of these topics (pick the most specific ones): {json.dumps(topics_list)}
   Review the results. Skip threads marked already_posted=true.

2. From the search results, pick the most relevant threads where you can add genuine value.
   Consider: subreddit relevance, thread topic, engagement level, whether our content angle fits.
   SKIP threads from fiction/gaming/meme subreddits that happen to match keywords.

3. For each promising thread, fetch it to read the full discussion and comments.
   Decide: post a top-level comment, or reply to a high-upvote comment for visibility.

4. Draft your comment (2-4 sentences), then output a JSON object.

5. Stop after {limit} JSON object(s).

## Content rules
- Write like texting a coworker. Lowercase OK, fragments OK.
- First person, specific details from content angle.
- NO em dashes. Use commas, periods, or regular dashes (-).
- No markdown on Reddit (no ##, **, numbered lists).
- Imperfections: contractions, casual asides, occasional lowercase.
- Vary openings. 2-4 short sentences max.
- Reply to high-upvote comments for visibility, not just OP.

## Anti-patterns
- NEVER start with "exactly", "yeah totally", "100%", "that's smart".
- NEVER say "I built" / "we built" / "I'm working on". Frame as recommendations.
- No product links in top-level comments. Earn attention first.

## Guardrails
- NEVER suggest calls, meetings, demos.
- NEVER promise to share links/files not in project config.
- NEVER offer to DM. NEVER make time-bound promises.

## Output format

For EACH post, output EXACTLY one JSON object on its own line, with no other text around it:

{{"action": "post", "thread_url": "THREAD_URL", "reply_to_url": "COMMENT_PERMALINK_OR_NULL", "text": "YOUR_COMMENT_TEXT", "thread_author": "AUTHOR", "thread_title": "TITLE"}}

- thread_url: the thread URL (e.g. https://old.reddit.com/r/sub/comments/abc/title/)
- reply_to_url: if replying to a specific comment, its permalink URL. If posting a top-level comment, set to null.
- text: your drafted comment text
- thread_author: the thread OP's username
- thread_title: the thread title

Output {limit} JSON object(s), then output DONE on its own line.
Do NOT post anything yourself. The orchestrator will handle posting.
"""


def run_claude(prompt, timeout=600):
    """Run claude -p in bare mode with Bash tool only (no MCP needed).

    Streams output in real time to stderr (picked up by tee in the shell wrapper)
    while collecting the full output for JSON parsing.
    """
    import time as _time
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_create": 0, "cost_usd": 0.0}
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--bare"]
    cmd += ["--tools", "Bash,Read"]
    env = os.environ.copy()
    api_key = get_api_key()
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    try:
        proc = subprocess.Popen(
            cmd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        proc.stdin.write(prompt)
        proc.stdin.close()
        collected = []
        deadline = _time.time() + timeout
        import select
        while True:
            remaining = deadline - _time.time()
            if remaining <= 0:
                proc.kill()
                return False, "TIMEOUT", usage
            ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 30))
            if ready:
                line = proc.stdout.readline()
                if not line:
                    break
                collected.append(line)
                # Stream to stderr so tee/log captures it in real time
                print(f"[post_reddit] {line.rstrip()}", file=sys.stderr, flush=True)
            elif proc.poll() is not None:
                # Process ended, read remaining
                rest = proc.stdout.read()
                if rest:
                    collected.append(rest)
                break
            else:
                print(f"[post_reddit] ... still running ({int(_time.time() - (deadline - timeout))}s)", file=sys.stderr, flush=True)
        proc.wait()
        # Parse stream-json: each line is a JSON event
        # The "result" event has usage and the final text output
        text_output = ""
        for line_str in collected:
            line_str = line_str.strip()
            if not line_str:
                continue
            try:
                event = json.loads(line_str)
                if event.get("type") == "result":
                    text_output = event.get("result", "")
                    usage["cost_usd"] = event.get("total_cost_usd", 0.0)
                    u = event.get("usage", {})
                    usage["input_tokens"] = u.get("input_tokens", 0)
                    usage["output_tokens"] = u.get("output_tokens", 0)
                    usage["cache_read"] = u.get("cache_read_input_tokens", 0)
                    usage["cache_create"] = u.get("cache_creation_input_tokens", 0)
            except (json.JSONDecodeError, TypeError):
                pass
        if not text_output:
            text_output = "".join(collected)
        stderr_out = proc.stderr.read() if proc.stderr else ""
        return proc.returncode == 0, text_output + stderr_out, usage
    except Exception as e:
        return False, str(e), usage


def post_via_cdp(thread_url, reply_to_url, text):
    """Post a comment or reply via CDP. Returns parsed JSON result."""
    for attempt in range(3):
        try:
            if reply_to_url:
                cdp_out = subprocess.check_output(
                    ["python3", REDDIT_BROWSER, "reply", reply_to_url, text],
                    text=True, timeout=60, stderr=subprocess.DEVNULL,
                )
            else:
                cdp_out = subprocess.check_output(
                    ["python3", REDDIT_BROWSER, "post-comment", thread_url, text],
                    text=True, timeout=60, stderr=subprocess.DEVNULL,
                )
            result = json.loads(cdp_out)
            if result.get("ok"):
                return result
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError) as e:
            print(f"[post_reddit] CDP attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(10)
    return {"ok": False, "error": "all_attempts_failed"}


def log_post(thread_url, permalink, text, project_name, thread_author, thread_title, reddit_username):
    """Log a successful post to the database."""
    try:
        subprocess.run(
            ["python3", REDDIT_TOOLS, "log-post",
             thread_url, permalink or "", text, project_name,
             thread_author, thread_title,
             "--account", reddit_username],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        print(f"[post_reddit] WARNING: log-post failed: {e}")


def parse_post_decisions(output):
    """Extract JSON post decisions from Claude's output."""
    decisions = []
    for line in output.split("\n"):
        line = line.strip()
        if not line or line == "DONE":
            continue
        # Try to extract JSON objects with "action": "post"
        try:
            match = re.search(r'\{[^{}]*"action"\s*:\s*"post"[^{}]*\}', line)
            if match:
                decision = json.loads(match.group())
                if decision.get("text") and decision.get("thread_url"):
                    decisions.append(decision)
        except (json.JSONDecodeError, TypeError):
            continue
    return decisions


def main():
    parser = argparse.ArgumentParser(description="Reddit posting orchestrator")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without executing")
    parser.add_argument("--limit", type=int, default=1, help="Max comments to post (default: 1)")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout for Claude session")
    parser.add_argument("--project", default=None, help="Override project selection")
    args = parser.parse_args()

    config = load_config()
    reddit_username = config.get("accounts", {}).get("reddit", {}).get("username", "Deep_Ad1959")

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

    # Get context
    top_report = get_top_performers(project_name)
    recent_comments = get_recent_comments()

    # Build prompt
    prompt = build_prompt(project, config, args.limit, top_report, recent_comments)

    if args.dry_run:
        print(f"=== DRY RUN ===")
        print(f"Prompt length: {len(prompt)} chars")
        print(prompt)
        print("=== END DRY RUN ===")
        return

    print(f"[post_reddit] Starting Claude session (limit={args.limit}, timeout={args.timeout}s)")
    start = time.time()

    # Phase 1: Claude searches and drafts (no MCP, no browser)
    ok, output, usage = run_claude(prompt, timeout=args.timeout)
    claude_elapsed = time.time() - start

    print(f"[post_reddit] Claude finished in {claude_elapsed:.0f}s "
          f"(${usage['cost_usd']:.4f})")

    if not ok:
        print(f"[post_reddit] Claude FAILED: {output[:300]}")
        subprocess.run([
            "python3", os.path.join(REPO_DIR, "scripts", "log_run.py"),
            "--script", "post_reddit",
            "--posted", "0", "--skipped", "0", "--failed", "1",
            "--cost", f"{usage['cost_usd']:.4f}",
            "--elapsed", f"{claude_elapsed:.0f}",
        ])
        sys.exit(1)

    # Phase 2: Parse Claude's decisions and post via CDP
    decisions = parse_post_decisions(output)
    print(f"[post_reddit] Claude drafted {len(decisions)} post(s)")

    if not decisions:
        print(f"[post_reddit] No valid post decisions found in output:")
        for line in output.strip().split("\n")[-10:]:
            print(f"  {line}")

    posted = 0
    failed = 0

    for i, decision in enumerate(decisions):
        thread_url = decision["thread_url"]
        reply_to_url = decision.get("reply_to_url")
        text = decision["text"]
        thread_author = decision.get("thread_author", "unknown")
        thread_title = decision.get("thread_title", "unknown")

        target = reply_to_url or thread_url
        print(f"[post_reddit] Posting {i + 1}/{len(decisions)}: "
              f"{thread_title[:50]}...")

        result = post_via_cdp(thread_url, reply_to_url, text)

        if result.get("ok"):
            if result.get("already_replied"):
                print(f"[post_reddit] DEDUP: already posted in this thread")
                continue

            permalink = result.get("permalink", "")
            log_post(thread_url, permalink, text, project_name,
                     thread_author, thread_title, reddit_username)
            posted += 1
            print(f"[post_reddit] POSTED: {permalink or 'ok'}")
        else:
            err = result.get("error", "unknown")
            failed += 1
            print(f"[post_reddit] CDP FAILED: {err}")

        time.sleep(3)

    total_elapsed = time.time() - start

    print(f"\n[post_reddit] === SUMMARY ===")
    print(f"[post_reddit] elapsed={total_elapsed:.0f}s posted={posted} failed={failed}")
    print(f"[post_reddit] Tokens: input={usage['input_tokens']} output={usage['output_tokens']} "
          f"cache_read={usage['cache_read']} cache_create={usage['cache_create']}")
    print(f"[post_reddit] Cost: ${usage['cost_usd']:.4f}")

    subprocess.run([
        "python3", os.path.join(REPO_DIR, "scripts", "log_run.py"),
        "--script", "post_reddit",
        "--posted", str(posted),
        "--skipped", "0",
        "--failed", str(failed),
        "--cost", f"{usage['cost_usd']:.4f}",
        "--elapsed", f"{total_elapsed:.0f}",
    ])


if __name__ == "__main__":
    main()
