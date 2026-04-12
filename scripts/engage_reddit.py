#!/usr/bin/env python3
"""Reddit reply engagement orchestrator.

Processes pending Reddit replies one at a time, each in its own Claude session.
This avoids the context accumulation problem of batching 200 replies into one session.

Usage:
    python3 scripts/engage_reddit.py
    python3 scripts/engage_reddit.py --dry-run          # Print prompt for first reply, don't post
    python3 scripts/engage_reddit.py --limit 5           # Process at most 5 replies
    python3 scripts/engage_reddit.py --timeout 3600      # Global timeout in seconds (default: 5400)
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
REPLY_DB = os.path.join(REPO_DIR, "scripts", "reply_db.py")
REDDIT_MCP_CONFIG = os.path.expanduser("~/.claude/browser-agent-configs/reddit-agent-mcp.json")
API_KEY_KEYCHAIN_SERVICE = "Anthropic API Key Fazm"

from engagement_styles import REPLY_STYLES as VALID_STYLES, get_styles_prompt, get_content_rules, get_anti_patterns


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_next_pending(conn):
    """Fetch the next pending Reddit/Moltbook reply (one at a time)."""
    cur = conn.execute("""
        SELECT r.id, r.platform, r.their_author,
               LEFT(r.their_content, 300) as their_content,
               r.their_comment_url, r.their_comment_id, r.depth,
               LEFT(p.thread_title, 100) as thread_title,
               p.thread_url, LEFT(p.our_content, 200) as our_content, p.our_url,
               CASE WHEN p.thread_url = p.our_url THEN 1 ELSE 0 END as is_our_original_post
        FROM replies r
        JOIN posts p ON r.post_id = p.id
        WHERE r.status='pending' AND r.platform IN ('reddit', 'moltbook')
        ORDER BY
            CASE WHEN p.thread_url = p.our_url THEN 0 ELSE 1 END,
            r.discovered_at ASC
        LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "platform": row[1], "their_author": row[2],
        "their_content": row[3], "their_comment_url": row[4],
        "their_comment_id": row[5], "depth": row[6],
        "thread_title": row[7], "thread_url": row[8],
        "our_content": row[9], "our_url": row[10],
        "is_our_original_post": row[11],
    }


def get_recent_archetypes(conn, limit=3):
    """Fetch archetypes of last N replied replies for rotation context."""
    cur = conn.execute("""
        SELECT LEFT(our_reply_content, 150)
        FROM replies
        WHERE status='replied' AND our_reply_content IS NOT NULL
            AND platform NOT IN ('linkedin', 'x')
        ORDER BY replied_at DESC
        LIMIT %s
    """, [limit])
    return [row[0] for row in cur.fetchall()]


def build_prompt(reply, recent_replies, config, excluded_authors):
    """Build a minimal prompt for one reply."""
    reddit_username = config.get("accounts", {}).get("reddit", {}).get("username", "Deep_Ad1959")
    reply_json = json.dumps(reply, indent=2)

    recent_context = ""
    if recent_replies:
        snippets = "\n".join(f"  - {r}" for r in recent_replies)
        recent_context = f"""
Your last {len(recent_replies)} replies (vary your style, don't repeat the same archetype):
{snippets}
"""

    exclusion_note = ""
    if excluded_authors and reply["their_author"].lower() in {a.lower() for a in excluded_authors}:
        return None  # will be skipped by caller

    return f"""Reply to this Reddit comment. You are the Social Autoposter engagement bot.

## Reply data
{reply_json}

## Context
Read ~/social-autoposter/config.json for project details and content_angle.
{recent_context}
## Content rules
{get_content_rules("reddit")}
- First person, specific details from content_angle in config.json.
- Vary openings. Don't always start with credentials.

{get_styles_prompt("reddit", context="replying")}

{get_anti_patterns()}
- On Moltbook: write as an agent ("my human" not "I").

## Tiered links
- Tier 1 (default): No link. Genuine engagement.
- Tier 2: Topic matches a config project. Mention casually.
- Tier 3: They ask for link/tool. Give it from config.

## Guardrails
- NEVER suggest calls, meetings, demos.
- NEVER promise to share links/files not in config.json.
- NEVER offer to DM. NEVER make time-bound promises.

## Execution steps

1. First, fetch the full thread context cheaply via Bash (NO browser needed):
   python3 ~/social-autoposter/scripts/reddit_tools.py fetch '{reply['thread_url']}'
   This returns JSON with "thread" (title, author, selftext, score, subreddit) and "comments" (id, author, body, score, permalink).
   Read the output to understand the full conversation context, who said what, and the overall tone.

2. Using the thread context from step 1 AND the reply data above, decide: reply or skip?
   If skip (troll, spam, not directed at us, light acknowledgment, conversation already resolved), output ONLY this JSON:
   {{"action": "skip", "reason": "SHORT_REASON"}}

3. If replying, draft 1-3 sentences following the rules above. Output ONLY this JSON:
   {{"action": "reply", "text": "YOUR_REPLY_TEXT", "project": null, "engagement_style": "STYLE_NAME"}}
   Set "engagement_style" to the style you used (critic, storyteller, pattern_recognizer, curious_probe, contrarian, data_point_drop, snarky_oneliner, recommendation).
   If you recommended a project, set "project" to the project name.

CRITICAL: Your ENTIRE output must be ONLY the JSON object above. No other text, no explanations, no markdown.
The orchestrator script will handle posting via CDP and database updates automatically.
"""


def ensure_mcp_config():
    """Create a minimal MCP config with only the reddit-agent server."""
    if os.path.exists(REDDIT_MCP_CONFIG):
        return REDDIT_MCP_CONFIG
    # Extract reddit-agent config from ~/.claude.json
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


def get_api_key():
    """Retrieve Anthropic API key from macOS keychain."""
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


def run_claude(prompt, timeout=300):
    """Run claude -p with the given prompt. Returns (success, output, usage_dict).

    Streams output in real time to stderr for log visibility.
    """
    import time as _time
    import select
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_create": 0, "cost_usd": 0.0}
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    # --bare removed: it blocks OAuth auth which we need
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
                                print(f"[engage_reddit] tool: {block.get('name','')} | {str(block.get('input',{}).get('command',''))[:120]}", file=sys.stderr, flush=True)
                            elif block.get("type") == "text" and block.get("text","").strip():
                                txt = block["text"].strip()[:200]
                                print(f"[engage_reddit] {txt}", file=sys.stderr, flush=True)
                    elif etype == "result":
                        print(f"[engage_reddit] done: cost=${evt.get('total_cost_usd',0):.4f}", file=sys.stderr, flush=True)
                except (json.JSONDecodeError, TypeError):
                    print(f"[engage_reddit] {line.rstrip()[:200]}", file=sys.stderr, flush=True)
            elif proc.poll() is not None:
                rest = proc.stdout.read()
                if rest:
                    collected.append(rest)
                break
            else:
                print(f"[engage_reddit] ... still running ({int(_time.time() - (deadline - timeout))}s)", file=sys.stderr, flush=True)
        proc.wait()
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


def main():
    parser = argparse.ArgumentParser(description="Reddit reply engagement (one at a time)")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt for first reply without executing")
    parser.add_argument("--limit", type=int, default=0, help="Max replies to process (0 = unlimited)")
    parser.add_argument("--timeout", type=int, default=5400, help="Global timeout in seconds")
    parser.add_argument("--per-reply-timeout", type=int, default=300, help="Timeout per claude session in seconds")
    args = parser.parse_args()

    dbmod.load_env()
    conn = dbmod.get_conn()
    config = load_config()
    excluded_authors = config.get("exclusions", {}).get("authors", [])

    start_time = time.time()
    processed = 0
    succeeded = 0
    skipped = 0
    failed = 0
    total_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_create": 0, "cost_usd": 0.0}

    print(f"[engage_reddit] Starting. limit={args.limit or 'unlimited'}, timeout={args.timeout}s")

    while True:
        # Global timeout check
        elapsed = time.time() - start_time
        if elapsed > args.timeout:
            print(f"[engage_reddit] Global timeout reached ({args.timeout}s). Stopping.")
            break

        # Limit check
        if args.limit and processed >= args.limit:
            print(f"[engage_reddit] Limit reached ({args.limit}). Stopping.")
            break

        # Fetch next pending reply
        reply = get_next_pending(conn)
        if not reply:
            print("[engage_reddit] No pending replies. Done!")
            break

        # Check exclusion before spawning Claude
        if reply["their_author"].lower() in {a.lower() for a in excluded_authors}:
            subprocess.run(["python3", REPLY_DB, "skipped", str(reply["id"]), "excluded_author"])
            print(f"[engage_reddit] #{reply['id']} skipped (excluded_author: {reply['their_author']})")
            skipped += 1
            processed += 1
            continue

        # Get recent replies for archetype rotation
        recent = get_recent_archetypes(conn, limit=3)

        # Build prompt
        prompt = build_prompt(reply, recent, config, excluded_authors)
        if prompt is None:
            skipped += 1
            processed += 1
            continue

        if args.dry_run:
            print("=== DRY RUN: Prompt for reply #{} ===".format(reply["id"]))
            print(prompt)
            print("=== END DRY RUN ===")
            break

        # Run Claude session for this one reply (Claude decides + drafts, we post)
        reply_start = time.time()
        print(f"[engage_reddit] Processing #{reply['id']} ({reply['platform']}) "
              f"from {reply['their_author']}: {(reply['their_content'] or '')[:60]}...")

        ok, output, usage = run_claude(prompt, timeout=args.per_reply_timeout)
        reply_elapsed = time.time() - reply_start

        # Accumulate usage
        for k in total_usage:
            total_usage[k] += usage[k]

        if not ok:
            failed += 1
            print(f"[engage_reddit] #{reply['id']} CLAUDE FAILED ({reply_elapsed:.0f}s): {output[:200]}")
        else:
            # Parse Claude's JSON decision
            decision = None
            try:
                # Extract JSON from output (may have surrounding text)
                import re as _re
                json_match = _re.search(r'\{[^{}]*"action"\s*:\s*"[^"]+?"[^{}]*\}', output)
                if json_match:
                    decision = json.loads(json_match.group())
            except (json.JSONDecodeError, TypeError):
                pass

            if not decision:
                # Fallback: check if output looks like a skip/reply
                failed += 1
                print(f"[engage_reddit] #{reply['id']} BAD OUTPUT ({reply_elapsed:.0f}s): {output[:200]}")
            elif decision.get("action") == "skip":
                reason = decision.get("reason", "unknown")
                subprocess.run(["python3", REPLY_DB, "skipped", str(reply["id"]), reason])
                skipped += 1
                print(f"[engage_reddit] #{reply['id']} skipped: {reason} ({reply_elapsed:.0f}s) "
                      f"[${usage['cost_usd']:.4f}]")
            elif decision.get("action") == "reply":
                reply_text = decision.get("text", "")
                project = decision.get("project")
                engagement_style = decision.get("engagement_style")
                if engagement_style and engagement_style not in VALID_STYLES:
                    print(f"[engage_reddit] #{reply['id']} unknown style '{engagement_style}', clearing")
                    engagement_style = None
                if not reply_text:
                    failed += 1
                    print(f"[engage_reddit] #{reply['id']} empty reply text")
                else:
                    # Mark as processing
                    subprocess.run(["python3", REPLY_DB, "processing", str(reply["id"])])

                    # Post via CDP
                    post_result = None
                    for attempt in range(3):
                        try:
                            cdp_out = subprocess.check_output(
                                ["python3", os.path.join(REPO_DIR, "scripts", "reddit_browser.py"),
                                 "reply", reply["their_comment_url"], reply_text],
                                text=True, timeout=60, stderr=subprocess.DEVNULL,
                            )
                            post_result = json.loads(cdp_out)
                            if post_result.get("ok"):
                                break
                        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError) as e:
                            print(f"[engage_reddit] #{reply['id']} CDP attempt {attempt+1} failed: {e}")
                            if attempt < 2:
                                time.sleep(10)

                    if post_result and post_result.get("ok"):
                        # Check if already replied (dedup)
                        if post_result.get("already_replied"):
                            existing = post_result.get("existing_text", "")[:200]
                            existing_url = post_result.get("existing_url", "")
                            cmd_args = ["python3", REPLY_DB, "replied", str(reply["id"]), existing]
                            if existing_url:
                                cmd_args.append(existing_url)
                            subprocess.run(cmd_args)
                            succeeded += 1
                            print(f"[engage_reddit] #{reply['id']} DEDUP (already replied) ({reply_elapsed:.0f}s)")
                            print(f"[engage_reddit] #{reply['id']} tokens: in={usage['input_tokens']} out={usage['output_tokens']} "
                                  f"cache_r={usage['cache_read']} cache_w={usage['cache_create']} "
                                  f"${usage['cost_usd']:.4f}")
                            processed += 1
                            time.sleep(2)
                            continue

                        # Mark as replied in DB
                        reply_url = post_result.get("url", "")
                        cmd_args = ["python3", REPLY_DB, "replied", str(reply["id"]), reply_text, reply_url]
                        if engagement_style:
                            cmd_args.append(engagement_style)
                        subprocess.run(cmd_args)
                        # Update project if recommended
                        if project:
                            dbmod.load_env()
                            db_url = os.environ.get("DATABASE_URL", "")
                            if db_url:
                                subprocess.run(
                                    ["psql", db_url, "-c",
                                     f"UPDATE replies SET project_name='{project}' WHERE id={reply['id']};"],
                                    capture_output=True,
                                )
                        succeeded += 1
                        print(f"[engage_reddit] #{reply['id']} POSTED ({reply_elapsed:.0f}s) "
                              f"[${usage['cost_usd']:.4f}]")
                    else:
                        err = post_result.get("error", "unknown") if post_result else "no_response"
                        subprocess.run(["python3", REPLY_DB, "skipped", str(reply["id"]), f"CDP_ERROR: {err}"])
                        failed += 1
                        print(f"[engage_reddit] #{reply['id']} CDP FAILED: {err} ({reply_elapsed:.0f}s)")
            else:
                failed += 1
                print(f"[engage_reddit] #{reply['id']} unknown action: {decision}")

            print(f"[engage_reddit] #{reply['id']} tokens: in={usage['input_tokens']} out={usage['output_tokens']} "
                  f"cache_r={usage['cache_read']} cache_w={usage['cache_create']} "
                  f"${usage['cost_usd']:.4f}")

        processed += 1

        # Brief pause between sessions
        time.sleep(2)

    total_elapsed = time.time() - start_time
    print(f"\n[engage_reddit] === SUMMARY ===")
    print(f"[engage_reddit] processed={processed} succeeded={succeeded} "
          f"skipped={skipped} failed={failed} elapsed={total_elapsed:.0f}s")
    print(f"[engage_reddit] Total tokens: input={total_usage['input_tokens']} "
          f"output={total_usage['output_tokens']} "
          f"cache_read={total_usage['cache_read']} cache_create={total_usage['cache_create']}")
    print(f"[engage_reddit] Total cost: ${total_usage['cost_usd']:.4f}")
    if succeeded > 0:
        print(f"[engage_reddit] Avg cost per reply: ${total_usage['cost_usd'] / succeeded:.4f}")

    # Log run summary for monitoring
    subprocess.run([
        "python3", os.path.join(REPO_DIR, "scripts", "log_run.py"),
        "--script", "engage_reddit",
        "--posted", str(succeeded),
        "--skipped", str(skipped),
        "--failed", str(failed),
        "--cost", f"{total_usage['cost_usd']:.4f}",
        "--elapsed", f"{total_elapsed:.0f}",
    ])

    # Print final status
    subprocess.run(["python3", REPLY_DB, "status"])
    conn.close()


if __name__ == "__main__":
    main()
