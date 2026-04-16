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
RESTRICTED_SUBS_PATH = os.path.join(REPO_DIR, "scripts", ".restricted_subreddits.json")
API_KEY_KEYCHAIN_SERVICE = "Anthropic API Key Fazm"
REDDIT_BROWSER = os.path.join(REPO_DIR, "scripts", "reddit_browser.py")
REDDIT_TOOLS = os.path.join(REPO_DIR, "scripts", "reddit_tools.py")

from engagement_styles import VALID_STYLES, get_styles_prompt, get_content_rules


def mark_subreddit_restricted(thread_url: str) -> None:
    """Persist a subreddit as restricted so future searches skip it."""
    sub_match = re.search(r'/r/([^/]+)/', thread_url)
    if not sub_match:
        return
    sub = sub_match.group(1).lower()
    try:
        data = {}
        if os.path.exists(RESTRICTED_SUBS_PATH):
            with open(RESTRICTED_SUBS_PATH) as f:
                data = json.load(f)
        if sub not in data:
            data[sub] = True
            with open(RESTRICTED_SUBS_PATH, "w") as f:
                json.dump(data, f, indent=2)
            print(f"[post_reddit] Marked r/{sub} as restricted (won't retry)")
    except Exception as e:
        print(f"[post_reddit] WARNING: could not persist restricted sub r/{sub}: {e}")


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


def build_content_angle(project, config):
    """Prefer project-specific positioning over the global config angle."""
    if project.get("content_angle"):
        return project["content_angle"]

    parts = []
    for key in ("description", "differentiator", "icp", "setup"):
        value = project.get(key)
        if value:
            parts.append(value)

    messaging = project.get("messaging", {})
    for key in ("lead_with_pain", "solution", "proof"):
        value = messaging.get(key)
        if value:
            parts.append(value)

    voice = project.get("voice", {})
    if voice.get("tone"):
        parts.append(f"Voice: {voice['tone']}")

    if parts:
        return " ".join(parts)

    return config.get("content_angle", "")


def build_prompt(project, config, limit, top_report, recent_comments):
    """Build prompt for Claude to search, evaluate, and draft replies (no posting)."""
    content_angle = build_content_angle(project, config)

    project_json = json.dumps({k: project.get(k) for k in
        ["name", "description", "topics"]
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

    return f"""Find {limit} Reddit threads where you can add genuine value as someone with expertise in {project.get('name', 'general')}.

Topic area: {project_json}
Content angle: {content_angle}

Your role: You are a practitioner sharing real experience. Your comments should be indistinguishable
from any other knowledgeable person in the conversation. NEVER mention product names, links, websites,
or GitHub repos. Product mentions happen ONLY later in the reply pipeline when people respond to you.
{recent_ctx}{top_ctx}
{get_styles_prompt("reddit", context="posting")}

## Tools (via Bash) - ALWAYS foreground, NEVER run_in_background
- Search (global, by relevance): python3 {REDDIT_TOOLS} search "QUERY" --limit 15
- Search (scoped to specific subs): python3 {REDDIT_TOOLS} search "QUERY" --subreddits AI_Agents,SaaS,smallbusiness --time month
- Search (broader time range): python3 {REDDIT_TOOLS} search "QUERY" --time month
- Fetch thread: python3 {REDDIT_TOOLS} fetch "THREAD_URL"
- Check dedup: python3 {REDDIT_TOOLS} already-posted "THREAD_URL"

Search defaults to sort=relevance and time=week. Use --time month for broader results. Use --subreddits for targeted sub searches.

## CRITICAL Bash rules
- NEVER use run_in_background=true. All bash commands must run foreground and return quickly (under 20s each).
- NEVER use `sleep` commands. NEVER run `sleep N && cat ...` to wait for background tasks.
- NEVER pipe multiple searches with `&` or `&&`. Run ONE search command at a time, wait for output, then decide next step.
- If you see `{{"error": "rate_limited", ...}}` in the output, DO NOT retry that command. Skip it and move on.
  Rate limits are global. Waiting won't help this session. Use whatever search results you already have.
- If you can't find enough threads after 5 search attempts total, draft fewer posts (even 1-2 is fine) rather than searching more.

## CRITICAL CONTENT RULES
- Study the style performance data in the feedback report below. Pick styles with the highest avg upvotes.
- Go BIMODAL on length: either 1 punchy sentence (<100 chars) or 4-5 sentences of real substance. AVOID the 2-3 sentence middle.
- Start with "I" or "my" when possible. First-person gets more engagement.
- NEVER mention product names (fazm, assrt, pieline, cyrano, terminator, mk0r, s4l).
- NEVER include URLs or links.
- Prefer replying to OP (top-level reply).
- ONE comment per thread.
- Statements beat questions. Be authoritative, not inquisitive.

## Steps
1. Search 2 topics from: {json.dumps(topics_list)}. Skip already_posted=true threads.
2. Pick {limit} best threads where you have genuine expertise to contribute. Prefer replying to OP. Fetch each one.
3. Draft the comment following the CRITICAL CONTENT RULES above. Quality over quantity.
4. Output each as a JSON object, then DONE.

## Content rules
{get_content_rules("reddit")}

## CRITICAL OUTPUT FORMAT
You MUST output each draft as a raw JSON object on its own line. No commentary before or after. Example:

{{"action": "post", "thread_url": "https://old.reddit.com/r/sub/comments/abc/title/", "reply_to_url": null, "text": "your comment here", "thread_author": "username", "thread_title": "thread title", "engagement_style": "critic"}}

After all {limit} JSON objects, output DONE on its own line.
Do NOT describe what you are doing. Do NOT narrate. Just search, draft, output JSON, DONE.
"""


def run_claude(prompt, timeout=600):
    """Run claude -p in bare mode with Bash tool only (no MCP needed).

    Streams output in real time to stderr (picked up by tee in the shell wrapper)
    while collecting the full output for JSON parsing.
    """
    import time as _time
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_create": 0, "cost_usd": 0.0}
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    cmd += ["--tools", "Bash,Read"]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)  # ensure claude uses OAuth, not API key
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
                # Stream meaningful events to stderr so tee/log captures them
                try:
                    evt = json.loads(line.strip())
                    etype = evt.get("type", "")
                    if etype == "assistant":
                        msg = evt.get("message", {})
                        for block in msg.get("content", []):
                            if block.get("type") == "tool_use":
                                print(f"[post_reddit] tool: {block.get('name','')} | {str(block.get('input',{}).get('command',''))[:120]}", file=sys.stderr, flush=True)
                            elif block.get("type") == "text" and block.get("text","").strip():
                                txt = block["text"].strip()[:200]
                                print(f"[post_reddit] {txt}", file=sys.stderr, flush=True)
                    elif etype == "result":
                        print(f"[post_reddit] done: cost=${evt.get('total_cost_usd',0):.4f}", file=sys.stderr, flush=True)
                except (json.JSONDecodeError, TypeError):
                    print(f"[post_reddit] {line.rstrip()[:200]}", file=sys.stderr, flush=True)
            elif proc.poll() is not None:
                # Process ended, read remaining
                rest = proc.stdout.read()
                if rest:
                    collected.append(rest)
                break
            else:
                print(f"[post_reddit] ... still running ({int(_time.time() - (deadline - timeout))}s)", file=sys.stderr, flush=True)
        proc.wait()
        # Parse stream-json: collect ALL text blocks (not just the final result)
        # JSON post decisions can appear in any assistant message, not just the last one
        all_text_parts = []
        for line_str in collected:
            line_str = line_str.strip()
            if not line_str:
                continue
            try:
                event = json.loads(line_str)
                etype = event.get("type", "")
                if etype == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            all_text_parts.append(block["text"])
                elif etype == "result":
                    if event.get("result"):
                        all_text_parts.append(event["result"])
                    usage["cost_usd"] = event.get("total_cost_usd", 0.0)
                    u = event.get("usage", {})
                    usage["input_tokens"] = u.get("input_tokens", 0)
                    usage["output_tokens"] = u.get("output_tokens", 0)
                    usage["cache_read"] = u.get("cache_read_input_tokens", 0)
                    usage["cache_create"] = u.get("cache_creation_input_tokens", 0)
            except (json.JSONDecodeError, TypeError):
                pass
        text_output = "\n".join(all_text_parts) if all_text_parts else "".join(collected)
        stderr_out = proc.stderr.read() if proc.stderr else ""
        return proc.returncode == 0, text_output + stderr_out, usage
    except Exception as e:
        return False, str(e), usage


def post_via_cdp(thread_url, reply_to_url, text):
    """Post a comment or reply via CDP. Returns parsed JSON result."""
    for attempt in range(3):
        try:
            target = reply_to_url or thread_url
            cmd = ["python3", REDDIT_BROWSER, "reply" if reply_to_url else "post-comment", target, text]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            cdp_out = proc.stdout.strip()
            if not cdp_out:
                print(f"[post_reddit] CDP attempt {attempt + 1}: no stdout. stderr: {proc.stderr[:200]}")
                if attempt < 2:
                    time.sleep(10)
                continue
            result = json.loads(cdp_out)
            if result.get("ok"):
                return result
            err = result.get("error", "unknown")
            print(f"[post_reddit] CDP attempt {attempt + 1}: {err}")
            if err in ("thread_not_found", "thread_locked", "thread_archived", "already_replied", "not_logged_in", "subreddit_restricted"):
                return result  # Don't retry these
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError) as e:
            print(f"[post_reddit] CDP attempt {attempt + 1} exception: {e}")
            if attempt < 2:
                time.sleep(10)
    return {"ok": False, "error": "all_attempts_failed"}


def log_post(thread_url, permalink, text, project_name, thread_author, thread_title, reddit_username, engagement_style=None):
    """Log a successful post to the database."""
    try:
        cmd = ["python3", REDDIT_TOOLS, "log-post",
             thread_url, permalink or "", text, project_name,
             thread_author, thread_title,
             "--account", reddit_username]
        if engagement_style:
            cmd.extend(["--engagement-style", engagement_style])
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"[post_reddit] WARNING: log-post failed: {e}")


def parse_post_decisions(output):
    """Extract JSON post decisions from Claude's output, deduplicated by thread_url."""
    decisions = []
    seen_urls = set()
    for match in re.finditer(r'\{[^{}]*?"action"\s*:\s*"post"[^{}]*?\}', output):
        try:
            decision = json.loads(match.group())
            url = decision.get("thread_url", "")
            if decision.get("text") and url and url not in seen_urls:
                decisions.append(decision)
                seen_urls.add(url)
        except (json.JSONDecodeError, TypeError):
            continue
    return decisions


def main():
    parser = argparse.ArgumentParser(description="Reddit posting orchestrator")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without executing")
    parser.add_argument("--limit", type=int, default=3, help="Max comments to post (default: 3)")
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
        engagement_style = decision.get("engagement_style")
        if engagement_style and engagement_style not in VALID_STYLES:
            print(f"[post_reddit] unknown style '{engagement_style}', clearing")
            engagement_style = None

        target = reply_to_url or thread_url
        print(f"[post_reddit] Posting {i + 1}/{len(decisions)}: "
              f"{thread_title[:50]}...")

        result = post_via_cdp(thread_url, reply_to_url, text)

        if result.get("ok"):
            if result.get("already_replied"):
                print(f"[post_reddit] DEDUP: already posted in this thread")
                continue

            permalink = result.get("permalink", "")
            if not permalink or not permalink.startswith("http"):
                print(f"[post_reddit] SKIPPED LOG: no valid permalink captured (got: {permalink!r})")
                failed += 1
                continue
            log_post(thread_url, permalink, text, project_name,
                     thread_author, thread_title, reddit_username,
                     engagement_style=engagement_style)
            posted += 1
            print(f"[post_reddit] POSTED: {permalink}")
        else:
            err = result.get("error", "unknown")
            failed += 1
            print(f"[post_reddit] CDP FAILED: {err}")
            if err == "subreddit_restricted":
                mark_subreddit_restricted(thread_url)

        time.sleep(180)  # 3 min gap between posts to avoid spam detection

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
