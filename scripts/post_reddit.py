#!/usr/bin/env python3
"""Reddit posting orchestrator.

Spawns a single Claude session that uses reddit_tools.py (search, fetch) to find
threads and browser MCP to post. Claude decides which threads are relevant.

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
    """Build prompt that gives Claude tools to search, evaluate, and post."""
    reddit_username = config.get("accounts", {}).get("reddit", {}).get("username", "Deep_Ad1959")
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

    return f"""You are the Social Autoposter. Post {limit} comment(s) on relevant Reddit threads for this project.

## MANDATORY: One comment per thread
NEVER post more than one comment per Reddit thread. Before posting in ANY thread, run `already-posted` to check.
If you already posted in a thread during this session (even moments ago), SKIP that thread entirely.
The log-post command will REJECT duplicate posts, but you must also check proactively.

## Project: {project.get('name', 'general')}
{project_json}

## Content angle
{content_angle}
{recent_ctx}{top_ctx}

## Your tools (call via Bash)

1. **Search**: Find threads by topic
   python3 ~/social-autoposter/scripts/reddit_tools.py search "QUERY" --limit 15
   Returns JSON array with subreddit, title, score, selftext preview, already_posted flag.

2. **Fetch thread**: Get full thread + top comments
   python3 ~/social-autoposter/scripts/reddit_tools.py fetch "THREAD_URL"
   Returns thread info + top 15 comments with their thing IDs and body text.

3. **Check dedup**: Check if we already posted in a thread
   python3 ~/social-autoposter/scripts/reddit_tools.py already-posted "THREAD_URL"

4. **Log post**: After posting, log to database
   python3 ~/social-autoposter/scripts/reddit_tools.py log-post "THREAD_URL" "OUR_PERMALINK" "OUR_TEXT" "{project.get('name', 'general')}" "THREAD_AUTHOR" "THREAD_TITLE" --account {reddit_username}

5. **Post via browser**: Navigate and post using mcp__reddit-agent__browser_* tools (see below)

## Workflow

1. Search for 2-3 of these topics (pick the most specific ones): {json.dumps(topics_list)}
   Review the results. Skip threads marked already_posted=true.

2. From the search results, pick the most relevant threads where you can add genuine value.
   Consider: subreddit relevance, thread topic, engagement level, whether our content angle fits.
   SKIP threads from fiction/gaming/meme subreddits that happen to match keywords.

3. For each promising thread, fetch it to read the full discussion and comments.
   Pick the best comment to reply to (high-upvote for visibility, or OP if appropriate).

4. Draft your reply (2-3 sentences), then post via browser MCP.

5. Log the post, then move to the next thread. Stop after {limit} successful post(s).

## Posting via browser MCP

Navigate to the thread: mcp__reddit-agent__browser_navigate to the thread URL.
Then post via mcp__reddit-agent__browser_run_code with this JS pattern:

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
Replace COMMENT_THING_ID with the thing ID (e.g. t1_abc123 or t3_xyz for OP).
Replace REPLY_TEXT_HERE with your text as a JS string literal.
Use thing.evaluate() for clicks (NOT direct .click()).

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

CRITICAL: Use ONLY mcp__reddit-agent__* browser tools. NEVER use generic mcp__playwright-extension__* or others.
CRITICAL: Close browser tab after each post: mcp__reddit-agent__browser_tabs action 'close'.
CRITICAL: If browser times out, wait 30s and retry up to 3 times.

Output DONE when finished with all {limit} post(s), or DONE with a count if you posted fewer.
"""


def run_claude(prompt, timeout=600):
    """Run claude -p in bare mode."""
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
    parser = argparse.ArgumentParser(description="Reddit posting orchestrator")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without executing")
    parser.add_argument("--limit", type=int, default=1, help="Max comments to post (default: 1)")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout for Claude session")
    parser.add_argument("--project", default=None, help="Override project selection")
    args = parser.parse_args()

    config = load_config()

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

    ok, output, usage = run_claude(prompt, timeout=args.timeout)
    elapsed = time.time() - start

    print(f"\n[post_reddit] === SUMMARY ===")
    print(f"[post_reddit] elapsed={elapsed:.0f}s success={ok}")
    print(f"[post_reddit] Tokens: input={usage['input_tokens']} output={usage['output_tokens']} "
          f"cache_read={usage['cache_read']} cache_create={usage['cache_create']}")
    print(f"[post_reddit] Cost: ${usage['cost_usd']:.4f}")
    if output:
        # Show last few lines of Claude's output
        lines = output.strip().split("\n")
        for line in lines[-5:]:
            print(f"[post_reddit] {line}")

    # Log run summary for monitoring
    posted = args.limit if ok else 0
    failed_count = 0 if ok else 1
    subprocess.run([
        "python3", os.path.join(REPO_DIR, "scripts", "log_run.py"),
        "--script", "post_reddit",
        "--posted", str(posted),
        "--skipped", "0",
        "--failed", str(failed_count),
        "--cost", f"{usage['cost_usd']:.4f}",
        "--elapsed", f"{elapsed:.0f}",
    ])


if __name__ == "__main__":
    main()
