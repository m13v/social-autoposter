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
        WHERE r.status='pending' AND r.platform NOT IN ('linkedin', 'x')
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
- Write like texting a coworker. Lowercase OK, fragments OK.
- First person, specific details from content_angle in config.json.
- NO em dashes. Use commas, periods, or regular dashes (-).
- No markdown in Reddit (no ##, **, numbered lists).
- Include imperfections: contractions, casual asides, occasional lowercase.
- Vary openings. Don't always start with credentials.

## Reply archetypes (rotate - pick a DIFFERENT one from your recent replies above):
- Short affirm (1 sentence): "love this framing" / "this is underrated" - no product tie-in
- Pure question (1-2 sentences): Ask something genuine. No tools mentioned.
- Respectful pushback: "I've actually seen the opposite..."
- Story/anecdote: Share a specific experience WITHOUT product tie.
- Recommendation: Recommend a project from config casually. MAX 30% of replies.

## Anti-patterns
- NEVER start with "exactly", "yeah totally", "100%", "that's smart".
- NEVER say "I built" / "we built" / "I'm working on". Frame as recommendations.
- Some replies should be 1 sentence.

## Tiered links
- Tier 1 (default): No link. Genuine engagement.
- Tier 2: Topic matches a config project. Mention casually.
- Tier 3: They ask for link/tool. Give it from config.

## Guardrails
- NEVER suggest calls, meetings, demos.
- NEVER promise to share links/files not in config.json.
- NEVER offer to DM. NEVER make time-bound promises.

## Execution steps

1. Decide: reply or skip? If skip (troll, spam, not directed at us, light acknowledgment), run:
   python3 {REPLY_DB} skipped {reply['id']} "REASON"
   Then output DONE.

2. If replying, draft 1-3 sentences following the rules above.

3. Mark as processing:
   python3 {REPLY_DB} processing {reply['id']}

4. Navigate to the comment:
   Use mcp__reddit-agent__browser_navigate to: {reply['their_comment_url']}

5. Post via a SINGLE mcp__reddit-agent__browser_run_code call with this JS:
```javascript
async (page) => {{
  const OUR_USERNAME = '{reddit_username}';
  const thing = await page.$('#thing_t1_{reply['their_comment_id'].replace('t1_', '')}');
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
  await page.waitForSelector('#thing_t1_{reply['their_comment_id'].replace('t1_', '')} .usertext-edit textarea', {{ timeout: 3000 }});
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
Replace REPLY_TEXT_HERE with a JS string literal of your drafted text.
Use thing.evaluate() for clicks (NOT direct .click()).

6. After posting:
   - If 'already_replied': python3 {REPLY_DB} replied {reply['id']} ""
   - If permalink returned: python3 {REPLY_DB} replied {reply['id']} "YOUR_TEXT" "PERMALINK_URL"
   - If null (no permalink): python3 {REPLY_DB} replied {reply['id']} "YOUR_TEXT"

7. If you recommended a project (Tier 2/3), also run:
   source ~/social-autoposter/.env
   psql "$DATABASE_URL" -c "UPDATE replies SET project_name='PROJECT_NAME' WHERE id={reply['id']};"

8. Output DONE when finished.

CRITICAL: Use ONLY mcp__reddit-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If browser times out, wait 30s and retry up to 3 times. If still blocked, skip.
CRITICAL: Close browser tabs after posting (browser_tabs action 'close').
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
    """Run claude -p with the given prompt. Returns (success, output, usage_dict)."""
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_create": 0, "cost_usd": 0.0}
    mcp_config = ensure_mcp_config()
    cmd = ["claude", "-p", "--output-format", "json"]
    # Use bare mode for minimal overhead (skips hooks, CLAUDE.md, skills, plugins)
    cmd.append("--bare")
    if mcp_config:
        cmd += ["--strict-mcp-config", "--mcp-config", mcp_config]
    cmd += ["--tools", "Bash,Read"]
    # Set API key for bare mode (which skips OAuth)
    env = os.environ.copy()
    api_key = get_api_key()
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    try:
        # Pass prompt via stdin (too long for CLI arg)
        result = subprocess.run(
            cmd, env=env, input=prompt,
            capture_output=True, text=True, timeout=timeout,
        )
        # Parse JSON output for usage stats
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

        # Run Claude session for this one reply
        reply_start = time.time()
        print(f"[engage_reddit] Processing #{reply['id']} ({reply['platform']}) "
              f"from {reply['their_author']}: {(reply['their_content'] or '')[:60]}...")

        ok, output, usage = run_claude(prompt, timeout=args.per_reply_timeout)
        reply_elapsed = time.time() - reply_start

        # Accumulate usage
        for k in total_usage:
            total_usage[k] += usage[k]

        if ok:
            succeeded += 1
            print(f"[engage_reddit] #{reply['id']} done ({reply_elapsed:.0f}s) "
                  f"[in={usage['input_tokens']} out={usage['output_tokens']} "
                  f"cache_r={usage['cache_read']} cache_w={usage['cache_create']} "
                  f"${usage['cost_usd']:.4f}]")
        else:
            failed += 1
            print(f"[engage_reddit] #{reply['id']} FAILED ({reply_elapsed:.0f}s): {output[:200]}")

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

    # Print final status
    subprocess.run(["python3", REPLY_DB, "status"])
    conn.close()


if __name__ == "__main__":
    main()
