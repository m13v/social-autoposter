#!/usr/bin/env python3
"""GitHub Issues posting orchestrator.

Spawns a Claude session that uses github_tools.py (search, view, already-posted)
to find issues and draft helpful comments. The Python orchestrator handles
the actual `gh issue comment` posting and DB logging, so the exact text
variable flows from Claude's JSON decision into both the posted comment and
the `our_content` column. This fixes the prior issue where Claude manually
constructed SQL INSERTs and stored a summary instead of the full comment.

Usage:
    python3 scripts/post_github.py
    python3 scripts/post_github.py --dry-run          # Print prompt without executing
    python3 scripts/post_github.py --limit 3           # Post at most 3 comments
    python3 scripts/post_github.py --timeout 3600      # Global timeout in seconds
    python3 scripts/post_github.py --project Fazm      # Override project selection
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
GITHUB_TOOLS = os.path.join(REPO_DIR, "scripts", "github_tools.py")

from engagement_styles import VALID_STYLES, get_styles_prompt, get_content_rules, get_anti_patterns


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def pick_project(platform="github"):
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


def get_top_performers(project_name, platform="github"):
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
        "WHERE platform='github' ORDER BY id DESC LIMIT %s",
        [limit],
    )
    results = [row[0] for row in cur.fetchall()]
    conn.close()
    return results


def build_content_angle(project, config):
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
    content_angle = build_content_angle(project, config)

    project_json = json.dumps({k: project.get(k) for k in
        ["name", "description", "github_search_topics"]
        if project.get(k)}, indent=2)

    # Prefer project-specific github_search_topics; fall back to global
    topics_list = project.get("github_search_topics") or \
        config.get("accounts", {}).get("github", {}).get("search_topics", [])

    recent_ctx = ""
    if recent_comments:
        snippets = "\n".join(f"  - {c}" for c in recent_comments if c)
        if snippets:
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

    excluded_repos = config.get("exclusions", {}).get("github_repos", [])
    excluded_authors = config.get("exclusions", {}).get("authors", [])

    return f"""Find {limit} GitHub issues where you can add genuine value as someone with expertise in {project.get('name', 'general')}.

Topic area: {project_json}
Content angle: {content_angle}

Your role: You are a practitioner sharing real experience. The comment should be indistinguishable from
any other knowledgeable person in the conversation. Do NOT include any links to our repos in the comment.
Links are added later by a separate pipeline (Phase D) after the comment earns engagement.
{recent_ctx}{top_ctx}
{get_styles_prompt("github", context="posting")}

## Tools (via Bash) - ALWAYS foreground, NEVER run_in_background
- Search: python3 {GITHUB_TOOLS} search "QUERY" --limit 10
- View issue: python3 {GITHUB_TOOLS} view OWNER/REPO NUMBER
- Check dedup: python3 {GITHUB_TOOLS} already-posted "THREAD_URL"

## CRITICAL Bash rules
- NEVER use run_in_background=true. All bash commands must run foreground and return quickly (under 20s each).
- NEVER use `sleep` commands.
- NEVER pipe multiple searches with `&` or `&&`. Run ONE command at a time.
- If a search/view fails, skip that issue and try another topic. Do NOT retry the same command.
- If you can't find enough issues after 5 search attempts, draft fewer posts rather than searching more.

## Exclusions (do NOT interact with these)
- Excluded repos/orgs: {', '.join(excluded_repos) if excluded_repos else '(none)'}
- Excluded authors: {', '.join(excluded_authors) if excluded_authors else '(none)'}

## Targeting
- Best topics: Agents, Accessibility, Voice/ASR, Tool Use. Prioritize these.
- Target small-to-mid repos (<1000 stars) where the maintainer is active. Solo maintainers reply; big repos bury comments.
- Prefer issues updated in last 7 days.

## Comment style
- Lead with the pain you hit, then your fix. "the token overhead is brutal" > "here is how to optimize".
- Keep it conversational, no code blocks.
- Aim for 400-600 chars. Short enough to read, long enough to show real experience.
- Share specific implementation details (file names, metrics, tradeoffs), not generic advice.
- Do NOT include any links.

## Steps
1. Search 2-3 topics from: {json.dumps(topics_list)}. Rotate topics across runs, don't always search the same ones.
2. Skip any entry where already_posted=true.
3. Pick {limit} best issues where we have genuine expertise. View each one for full context.
4. Draft a helpful comment (400-600 chars, NO links).
5. Output each as a JSON object, then DONE.

## Content rules
{get_content_rules("github")}

{get_anti_patterns()}

## CRITICAL OUTPUT FORMAT
You MUST output each draft as a raw JSON object on its own line. No commentary before or after. Example:

{{"action": "post", "thread_url": "https://github.com/owner/repo/issues/123", "text": "full comment body here", "thread_author": "alice", "thread_title": "Issue title", "engagement_style": "critic"}}

Rules for the JSON:
- `text`: the full comment body (400-600 chars, NO links, NO em dashes)
- `engagement_style`: one of {', '.join(sorted(VALID_STYLES))}
- `thread_author`: issue opener's GitHub username (from the search/view output)
- `thread_title`: exact issue title

After all {limit} JSON objects, output DONE on its own line.
Do NOT describe what you are doing. Do NOT narrate. Just search, view, draft, output JSON, DONE.
"""


def run_claude(prompt, timeout=3600):
    """Run claude -p with Bash+Read tools, streaming to stderr, collecting for parse."""
    import time as _time
    import select
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_create": 0, "cost_usd": 0.0}
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    cmd += ["--tools", "Bash,Read"]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)  # OAuth
    try:
        proc = subprocess.Popen(
            cmd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        proc.stdin.write(prompt)
        proc.stdin.close()
        collected = []
        deadline = _time.time() + timeout
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
                try:
                    evt = json.loads(line.strip())
                    etype = evt.get("type", "")
                    if etype == "assistant":
                        msg = evt.get("message", {})
                        for block in msg.get("content", []):
                            if block.get("type") == "tool_use":
                                cmd_str = str(block.get("input", {}).get("command", ""))[:120]
                                print(f"[post_github] tool: {block.get('name','')} | {cmd_str}", file=sys.stderr, flush=True)
                            elif block.get("type") == "text" and block.get("text", "").strip():
                                txt = block["text"].strip()[:200]
                                print(f"[post_github] {txt}", file=sys.stderr, flush=True)
                    elif etype == "result":
                        print(f"[post_github] done: cost=${evt.get('total_cost_usd',0):.4f}", file=sys.stderr, flush=True)
                except (json.JSONDecodeError, TypeError):
                    print(f"[post_github] {line.rstrip()[:200]}", file=sys.stderr, flush=True)
            elif proc.poll() is not None:
                rest = proc.stdout.read()
                if rest:
                    collected.append(rest)
                break
            else:
                elapsed_s = int(_time.time() - (deadline - timeout))
                print(f"[post_github] ... still running ({elapsed_s}s)", file=sys.stderr, flush=True)
        proc.wait()
        # Collect all text blocks (JSON decisions may appear across multiple assistant messages)
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


def parse_post_decisions(output):
    """Extract JSON post decisions from Claude's output, deduplicated by thread_url.

    JSON objects may contain nested braces (e.g. escaped quotes), so we use a
    greedy balanced-brace scan starting at each `{` containing `"action": "post"`.
    """
    decisions = []
    seen_urls = set()
    i = 0
    while i < len(output):
        start = output.find('{', i)
        if start == -1:
            break
        # Scan for matching close brace, respecting string literals
        depth = 0
        in_str = False
        esc = False
        end = -1
        for j in range(start, len(output)):
            ch = output[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
        if end == -1:
            break
        blob = output[start:end + 1]
        i = end + 1
        if '"action"' not in blob or '"post"' not in blob:
            continue
        try:
            decision = json.loads(blob)
        except (json.JSONDecodeError, TypeError):
            continue
        if decision.get("action") != "post":
            continue
        url = decision.get("thread_url", "")
        if not decision.get("text") or not url or url in seen_urls:
            continue
        decisions.append(decision)
        seen_urls.add(url)
    return decisions


def parse_issue_url(url):
    """Extract (owner, repo, number) from a github.com issue URL."""
    if not url:
        return None, None, None
    m = re.search(r"github\.com/([^/]+)/([^/]+)/(?:issues|pull)/(\d+)", url)
    if not m:
        return None, None, None
    return m.group(1), m.group(2), int(m.group(3))


def post_comment(owner, repo, number, body):
    """Post a comment via gh CLI. Returns (ok, url_or_error)."""
    try:
        out = subprocess.check_output(
            ["gh", "issue", "comment", str(number), "-R", f"{owner}/{repo}", "--body", body],
            text=True, timeout=60, stderr=subprocess.STDOUT,
        )
        url = None
        for line in out.strip().splitlines():
            if line.startswith("https://github.com"):
                url = line.strip()
                break
        return True, url
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        err = e.output if hasattr(e, "output") and e.output else str(e)
        return False, str(err)[:300]


def log_post(thread_url, our_url, text, project_name, thread_author, thread_title,
             github_username, engagement_style=None):
    """Log a successful post to the DB via github_tools.py."""
    try:
        cmd = ["python3", GITHUB_TOOLS, "log-post",
               thread_url, our_url or "", text, project_name,
               thread_author or "unknown", thread_title or "",
               "--account", github_username]
        if engagement_style:
            cmd.extend(["--engagement-style", engagement_style])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.stdout.strip():
            try:
                parsed = json.loads(result.stdout.strip())
                if parsed.get("error"):
                    print(f"[post_github] log-post error: {parsed}", file=sys.stderr)
            except json.JSONDecodeError:
                pass
    except Exception as e:
        print(f"[post_github] WARNING: log-post failed: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="GitHub Issues posting orchestrator")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without executing")
    parser.add_argument("--limit", type=int, default=10, help="Max issues to post on (default: 10)")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout for Claude session (seconds)")
    parser.add_argument("--project", default=None, help="Override project selection")
    args = parser.parse_args()

    config = load_config()
    github_username = config.get("accounts", {}).get("github", {}).get("username", "m13v")

    if args.project:
        project = None
        for p in config.get("projects", []):
            if p.get("name", "").lower() == args.project.lower():
                project = p
                break
        if not project:
            print(f"[post_github] ERROR: project '{args.project}' not found")
            sys.exit(1)
    else:
        project = pick_project("github")
        if not project:
            print("[post_github] ERROR: could not pick project")
            sys.exit(1)

    project_name = project.get("name", "general")
    print(f"[post_github] Project: {project_name}")

    top_report = get_top_performers(project_name)
    recent_comments = get_recent_comments()

    prompt = build_prompt(project, config, args.limit, top_report, recent_comments)

    if args.dry_run:
        print(f"=== DRY RUN ===")
        print(f"Prompt length: {len(prompt)} chars")
        print(prompt)
        print("=== END DRY RUN ===")
        return

    print(f"[post_github] Starting Claude session (limit={args.limit}, timeout={args.timeout}s)")
    start = time.time()

    ok, output, usage = run_claude(prompt, timeout=args.timeout)
    claude_elapsed = time.time() - start

    print(f"[post_github] Claude finished in {claude_elapsed:.0f}s (${usage['cost_usd']:.4f})")

    if not ok:
        print(f"[post_github] Claude FAILED: {output[:300]}")
        subprocess.run([
            "python3", os.path.join(REPO_DIR, "scripts", "log_run.py"),
            "--script", "post_github",
            "--posted", "0", "--skipped", "0", "--failed", "1",
            "--cost", f"{usage['cost_usd']:.4f}",
            "--elapsed", f"{claude_elapsed:.0f}",
        ])
        sys.exit(1)

    decisions = parse_post_decisions(output)
    print(f"[post_github] Claude drafted {len(decisions)} post(s)")

    if not decisions:
        print(f"[post_github] No valid post decisions found. Last 20 lines of output:")
        for line in output.strip().split("\n")[-20:]:
            print(f"  {line}")

    posted = 0
    failed = 0

    for i, decision in enumerate(decisions):
        thread_url = decision["thread_url"]
        text = decision["text"]
        thread_author = decision.get("thread_author", "unknown")
        thread_title = decision.get("thread_title", "")
        engagement_style = decision.get("engagement_style")
        if engagement_style and engagement_style not in VALID_STYLES:
            print(f"[post_github] unknown style '{engagement_style}', clearing")
            engagement_style = None

        owner, repo, number = parse_issue_url(thread_url)
        if not owner:
            print(f"[post_github] SKIP: bad URL {thread_url}")
            failed += 1
            continue

        print(f"[post_github] Posting {i + 1}/{len(decisions)} to {owner}/{repo}#{number}: {thread_title[:60]}")

        ok_post, url_or_err = post_comment(owner, repo, number, text)
        if not ok_post:
            print(f"[post_github] POST FAILED: {url_or_err}")
            failed += 1
            time.sleep(3)
            continue

        comment_url = url_or_err
        log_post(thread_url, comment_url, text, project_name,
                 thread_author, thread_title, github_username,
                 engagement_style=engagement_style)
        posted += 1
        print(f"[post_github] POSTED: {comment_url or 'ok'}")

        time.sleep(3)

    total_elapsed = time.time() - start

    print(f"\n[post_github] === SUMMARY ===")
    print(f"[post_github] elapsed={total_elapsed:.0f}s posted={posted} failed={failed}")
    print(f"[post_github] Tokens: input={usage['input_tokens']} output={usage['output_tokens']} "
          f"cache_read={usage['cache_read']} cache_create={usage['cache_create']}")
    print(f"[post_github] Cost: ${usage['cost_usd']:.4f}")

    subprocess.run([
        "python3", os.path.join(REPO_DIR, "scripts", "log_run.py"),
        "--script", "post_github",
        "--posted", str(posted),
        "--skipped", "0",
        "--failed", str(failed),
        "--cost", f"{usage['cost_usd']:.4f}",
        "--elapsed", f"{total_elapsed:.0f}",
    ])


if __name__ == "__main__":
    main()
