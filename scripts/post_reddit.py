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
import errno
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

REPO_DIR = os.path.expanduser("~/social-autoposter")
CONFIG_PATH = os.path.join(REPO_DIR, "config.json")
REDDIT_BROWSER = os.path.join(REPO_DIR, "scripts", "reddit_browser.py")
REDDIT_TOOLS = os.path.join(REPO_DIR, "scripts", "reddit_tools.py")
RATELIMIT_FILE = "/tmp/reddit_ratelimit.json"
PREFLIGHT_WAIT_BUDGET_SECONDS = 180

from engagement_styles import VALID_STYLES, get_styles_prompt, get_content_rules, validate_or_register


def _apply_rate_limit_policy(remaining, reset_seconds, source, budget_seconds):
    """Given current quota, decide: proceed (True), wait then proceed, or skip (False)."""
    if remaining > 2 or reset_seconds <= 0:
        return True
    if reset_seconds > budget_seconds:
        print(f"[post_reddit] Reddit rate-limited ({source}), reset in "
              f"{int(reset_seconds)}s (> {budget_seconds}s budget). Skipping run.")
        return False
    wait = int(reset_seconds) + 3
    print(f"[post_reddit] Reddit rate-limited ({source}), waiting {wait}s "
          f"for reset before spawning Claude...")
    time.sleep(wait)
    return True


def _probe_reddit_quota():
    """One cheap request to Reddit to learn the live quota.

    Updates RATELIMIT_FILE so downstream reddit_tools.py calls share the
    fresh state. Returns (remaining, reset_seconds) or None on network error.
    """
    import urllib.request
    import urllib.error
    url = "https://old.reddit.com/r/popular.json?limit=1"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        remaining = float(resp.headers.get("X-Ratelimit-Remaining", 100))
        reset = float(resp.headers.get("X-Ratelimit-Reset", 0))
        with open(RATELIMIT_FILE, "w") as f:
            json.dump({"remaining": remaining, "reset_at": time.time() + reset}, f)
        return remaining, reset
    except urllib.error.HTTPError as e:
        if e.code == 429:
            reset = float(e.headers.get("X-Ratelimit-Reset", 60))
            with open(RATELIMIT_FILE, "w") as f:
                json.dump({"remaining": 0, "reset_at": time.time() + reset}, f)
            return 0.0, reset
        return None
    except Exception:
        return None


def preflight_rate_limit(budget_seconds=PREFLIGHT_WAIT_BUDGET_SECONDS):
    """Block or bail before spawning Claude if Reddit search is throttled.

    Strategy:
      1. Cheap probe to Reddit to read live X-Ratelimit-Remaining headers.
         This catches the case where the shared state file is stale but the
         server still throttles us (10-min rolling window).
      2. Fall back to the cached state file if the probe network-fails.
    A $0.44 Claude spawn with 5 rate-limited searches is the cost we're
    avoiding; a single probe request is ~300ms.
    """
    probe = _probe_reddit_quota()
    if probe is not None:
        remaining, reset = probe
        print(f"[post_reddit] Reddit quota probe: remaining={remaining:.0f} "
              f"reset_in={int(reset)}s")
        return _apply_rate_limit_policy(remaining, reset, "probe", budget_seconds)
    try:
        with open(RATELIMIT_FILE) as f:
            rl = json.load(f)
    except Exception:
        return True
    wait = int(rl.get("reset_at", 0) - time.time())
    return _apply_rate_limit_policy(
        rl.get("remaining", 100), wait, "cached", budget_seconds,
    )


def mark_comment_blocked(thread_url: str) -> None:
    """Add a subreddit to config.json subreddit_bans.comment_blocked at runtime.

    Called when the bot's comment attempt is rejected (no comment form, locked,
    restricted). The sub gets blocked for future comment attempts so the
    drafter never targets it again. Thread-posting eligibility is tracked
    separately in subreddit_bans.thread_blocked.
    """
    sub_match = re.search(r'/r/([^/]+)/', thread_url)
    if not sub_match:
        return
    sub = sub_match.group(1).lower()
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        bans = config.setdefault("subreddit_bans", {})
        blocked = bans.setdefault("comment_blocked", [])
        existing = {s.lower() for s in blocked}
        if sub not in existing:
            blocked.append(sub)
            blocked.sort(key=str.lower)
            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f, indent=2)
            print(f"[post_reddit] Added r/{sub} to subreddit_bans.comment_blocked")
    except Exception as e:
        print(f"[post_reddit] WARNING: could not persist blocked sub r/{sub}: {e}")


# Keywords that indicate a permanent account/subreddit block rather than a
# transient failure.  Case-insensitive match against Claude's abort_reason.
# Tuned 2026-04-29: broaden to catch mod-rule bans expressed in present tense
# ("the sub bans software", "no software allowed") in addition to account-level
# bans ("u/X has been banned"). Each new pattern observed from real abort logs.
_THREAD_BLOCK_PATTERNS = [
    r"\bbanned\b",
    r"\bbans\b\s+(all|any|every|every kind|posts?|comments?|software|websites?|self[- ]promo|advertising|promotional)",
    r"\bban\b.*\b(software|posts?|websites?|self[- ]promo|advertising)\b",
    r"access was denied",
    r"\b403\b",
    r"link[- ]only",
    r"text posts? (are )?disabled",
    r"text (tab|option) (is )?disabled",
    r"does not allow text",
    r"not allowed to post",
    r"posting.*restricted",
    r"no (software|self[- ]promo|promotional|advertising|ads)",
    r"\bprohibit(ed|s)?\b",
    r"\bremoved\b.*\b(rule|mod)\b",   # "would be removed per rule X"
    r"would (get )?removed",
    r"\bnot permitted\b",
    r"approved (submitter|user)s? only",
    r"forbidden",
]

def _abort_is_permanent_block(abort_reason: str) -> bool:
    """Return True if abort_reason signals a permanent account/sub block."""
    lower = abort_reason.lower()
    for pat in _THREAD_BLOCK_PATTERNS:
        if re.search(pat, lower):
            return True
    return False


def mark_thread_blocked(subreddit: str, abort_reason: str = "") -> None:
    """Add a subreddit to config.json subreddit_bans.thread_blocked at runtime.

    Called when a thread-post attempt is permanently blocked (account banned,
    link-only sub, text posts disabled, 403). The sub is skipped by
    pick_thread_target.py on all future runs.  Comment eligibility is tracked
    separately in subreddit_bans.comment_blocked.

    subreddit may be bare ('programming') or prefixed ('r/programming').
    """
    sub = re.sub(r"^r/", "", subreddit, flags=re.IGNORECASE).strip().lower()
    if not sub:
        return
    if abort_reason and not _abort_is_permanent_block(abort_reason):
        return
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        bans = config.setdefault("subreddit_bans", {})
        blocked = bans.setdefault("thread_blocked", [])
        existing = {s.lower() for s in blocked}
        if sub not in existing:
            blocked.append(sub)
            blocked.sort(key=str.lower)
            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f, indent=2)
                f.write("\n")
            print(f"[post_reddit] Auto-blocked r/{sub} from future thread posts (permanent block detected)")
        else:
            print(f"[post_reddit] r/{sub} already in thread_blocked, skipping")
    except Exception as e:
        print(f"[post_reddit] WARNING: could not persist thread-blocked sub r/{sub}: {e}")


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def pick_project(platform="reddit", exclude=None):
    try:
        cmd = ["python3", os.path.join(REPO_DIR, "scripts", "pick_project.py"),
               "--platform", platform, "--json"]
        if exclude:
            cmd.extend(["--exclude", ",".join(exclude)])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
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


def get_top_search_topics(project_name, platform="reddit", limit=8, window_days=30):
    """Return a short text block of best-performing search_topic seeds for this
    project on this platform, or '' if no data yet. See top_search_topics.py."""
    try:
        result = subprocess.run(
            ["python3", os.path.join(REPO_DIR, "scripts", "top_search_topics.py"),
             "--project", project_name, "--platform", platform,
             "--window-days", str(window_days), "--limit", str(limit)],
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


def load_active_reddit_campaigns():
    """Active Reddit campaigns that carry a literal suffix.

    Tool-level enforcement: the LLM never sees these. We append the suffix to
    the drafted text in Python before posting, so the literal text is
    guaranteed to land on Reddit. sample_rate gates the per-post coin flip
    for concurrent A/B (e.g. 0.5 = ~half of posts get tagged).
    """
    dbmod.load_env()
    conn = dbmod.get_conn()
    try:
        cur = conn.execute(
            """SELECT id, suffix, COALESCE(sample_rate, 1.000)
               FROM campaigns
               WHERE status = 'active'
                 AND (',' || platforms || ',') LIKE '%,reddit,%'
                 AND max_posts_total IS NOT NULL
                 AND posts_made < max_posts_total
                 AND suffix IS NOT NULL AND suffix <> ''
               ORDER BY id"""
        )
        return [
            {"id": r[0], "suffix": r[1], "sample_rate": float(r[2])}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def _angle_str(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_angle_str(x)}" for k, x in v.items() if x)
    if isinstance(v, (list, tuple)):
        return ", ".join(_angle_str(x) for x in v if x)
    return str(v) if v else ""


def build_content_angle(project, config):
    """Prefer project-specific positioning over the global config angle."""
    if project.get("content_angle"):
        return project["content_angle"]

    parts = []
    for key in ("description", "differentiator", "icp", "setup"):
        s = _angle_str(project.get(key))
        if s:
            parts.append(s)

    messaging = project.get("messaging", {}) or {}
    for key in ("lead_with_pain", "solution", "proof"):
        s = _angle_str(messaging.get(key))
        if s:
            parts.append(s)

    voice = project.get("voice", {}) or {}
    if voice.get("tone"):
        parts.append(f"Voice: {voice['tone']}")
    if voice.get("never"):
        parts.append("Never: " + "; ".join(voice["never"]))
    examples = voice.get("examples") or voice.get("examples_good") or []
    if examples:
        parts.append("Voice examples: " + " | ".join(examples[:3]))

    if parts:
        return " ".join(parts)

    return config.get("content_angle", "")


def build_prompt(project, config, limit, top_report, recent_comments, top_topics_report=""):
    """Build prompt for Claude to search, evaluate, and draft replies (no posting)."""
    content_angle = build_content_angle(project, config)

    # Prefer unified search_topics (shared across platforms); fall back to the
    # legacy topics list for pre-migration safety.
    topics_list = project.get("search_topics") or project.get("topics", [])

    project_json = json.dumps({
        "name": project.get("name"),
        "description": project.get("description"),
        "search_topics": topics_list,
    }, indent=2)

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

    top_topics_ctx = ""
    if top_topics_report:
        top_topics_ctx = f"""
## Search-topic feedback (which seeds actually led to engagement):
{top_topics_report}

Prefer seeds with higher total/avg scores when they fit. If none of the top
seeds match this run's angle, pick any seed from the project's search_topics
list. New seeds with 0 posts are fine — we need to explore.
"""

    return f"""Find {limit} Reddit threads where you can add genuine value as someone with expertise in {project.get('name', 'general')}.

Topic area: {project_json}
Content angle: {content_angle}

Your role: You are a knowledgeable observer of this space. Your comments should be indistinguishable
from any other knowledgeable person in the conversation. NEVER mention product names, links, websites,
or GitHub repos. Product mentions happen ONLY later in the reply pipeline when people respond to you.

CRITICAL: every comment picks ONE of two lanes (see the GROUNDING RULE below).
  LANE 1 - DISCLOSED STORY: open with a hedge ("hypothetically", "imagine someone running this",
  "scenario:", "say a friend tried") and you may then invent any specifics.
  LANE 2 - NO FABRICATION: stay first-person / plain voice, but every specific (numbers, durations,
  places, course names, headcount, named tools) must appear verbatim in this project's content_angle /
  voice / messaging in config.json. Otherwise drop the specific or pattern-frame ("the part that
  breaks down is...", "the typical failure mode is...").
Never present an invented specific as a personal first-hand claim without a Lane 1 opener.
{recent_ctx}{top_ctx}{top_topics_ctx}
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
- First-person ("I"/"my") has TWO valid forms (see GROUNDING RULE). Lane 1: open with a disclosure phrase ("hypothetically", "imagine someone running this", "scenario:") and then invent freely. Lane 2: stay first-person only if every specific is grounded in content_angle/voice/messaging in config.json, or drop the specific and pattern-frame ("the part that breaks down is...", "the typical failure mode is..."). Never undisclosed first-person with invented numbers/places/programs.
- NEVER mention product names (fazm, assrt, pieline, cyrano, terminator, mk0r, s4l).
- NEVER include URLs or links.
- Prefer replying to OP (top-level reply).
- ONE comment per thread.
- Statements beat questions. Be authoritative, not inquisitive.

## Steps
1. Pick 2 concepts from the project's search_topics list: {json.dumps(topics_list)}.
   These are shared concept seeds across platforms (Twitter, Reddit, GitHub, LinkedIn). Some
   phrases are tuned for other platforms — rephrase each into natural Reddit search terms
   (vernacular, problem-framing, pain points) before running the search. Skip already_posted=true threads.
2. Pick {limit} best threads where you have genuine expertise to contribute. Prefer replying to OP. Fetch each one.
3. Draft the comment following the CRITICAL CONTENT RULES above. Quality over quantity.
4. Output each as a JSON object, then DONE. Include the seed concept you used in "search_topic".

## Content rules
{get_content_rules("reddit")}

## CRITICAL OUTPUT FORMAT
You MUST output each draft as a raw JSON object on its own line. No commentary before or after. Example:

{{"action": "post", "thread_url": "https://old.reddit.com/r/sub/comments/abc/title/", "reply_to_url": null, "text": "your comment here", "thread_author": "username", "thread_title": "thread title", "engagement_style": "critic", "search_topic": "the seed concept you picked", "new_style": null}}

If, and ONLY if, none of the listed styles fits, you may invent one. Set "engagement_style" to your snake_case name AND replace `"new_style": null` with `{{"description": "...", "example": "...", "note": "...", "why_existing_didnt_fit": "..."}}`. Inventing should be rare; prefer an existing style if it's even 80% right.

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
    session_id = str(uuid.uuid4())
    usage["session_id"] = session_id
    # Set in this process's env so subsequent log_post → reddit_tools.py inherits it
    os.environ["CLAUDE_SESSION_ID"] = session_id
    cmd = ["claude", "-p", "--session-id", session_id, "--output-format", "stream-json", "--verbose"]
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
        try:
            subprocess.run(
                ["python3", os.path.join(REPO_DIR, "scripts", "log_claude_session.py"),
                 "--session-id", session_id, "--script", "post_reddit"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as e:
            print(f"[post_reddit] WARNING: log_claude_session failed: {e}", file=sys.stderr)
        return proc.returncode == 0, text_output + stderr_out, usage
    except Exception as e:
        return False, str(e), usage


def post_via_cdp(thread_url, reply_to_url, text):
    """Post a comment or reply via CDP. Returns parsed JSON result."""
    # 5 attempts with lock-aware backoff. Lock contention (engage.sh or other
    # reddit-agent sessions mid-work) gets longer waits since those sessions
    # have natural gaps every 20-60s between replies. Other errors use a short
    # retry in case of transient network issues.
    MAX_ATTEMPTS = 5
    for attempt in range(MAX_ATTEMPTS):
        try:
            target = reply_to_url or thread_url
            cmd = ["python3", REDDIT_BROWSER, "reply" if reply_to_url else "post-comment", target, text]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            cdp_out = proc.stdout.strip()
            if not cdp_out:
                print(f"[post_reddit] CDP attempt {attempt + 1}: no stdout. stderr: {proc.stderr[:200]}")
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(10)
                continue
            result = json.loads(cdp_out)
            if result.get("ok"):
                return result
            err = result.get("error", "unknown")
            print(f"[post_reddit] CDP attempt {attempt + 1}: {err}")
            if err in ("thread_not_found", "thread_locked", "thread_archived", "already_replied", "not_logged_in", "account_blocked_in_sub"):
                return result  # Don't retry these
            # Lock contention: another reddit-agent session is actively working.
            # Back off in increasing intervals to catch a natural gap between
            # their reply drafts. Total wait across 5 attempts: ~2.5 min.
            if "locked by session" in err.lower():
                if attempt < MAX_ATTEMPTS - 1:
                    wait = [20, 35, 50, 60][attempt]
                    print(f"[post_reddit] CDP waiting {wait}s for browser lock to free...")
                    time.sleep(wait)
                continue
            # Any other error: short sleep then retry
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(5)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError) as e:
            print(f"[post_reddit] CDP attempt {attempt + 1} exception: {e}")
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(10)
    return {"ok": False, "error": "all_attempts_failed"}


def log_post(thread_url, permalink, text, project_name, thread_author, thread_title, reddit_username, engagement_style=None, search_topic=None):
    """Log a successful post to the database. Returns the new post_id, or None."""
    try:
        cmd = ["python3", REDDIT_TOOLS, "log-post",
             thread_url, permalink or "", text, project_name,
             thread_author, thread_title,
             "--account", reddit_username]
        if engagement_style:
            cmd.extend(["--engagement-style", engagement_style])
        if search_topic:
            cmd.extend(["--search-topic", search_topic])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        try:
            payload = json.loads((result.stdout or "").strip())
            return payload.get("post_id")
        except (json.JSONDecodeError, AttributeError, TypeError):
            return None
    except Exception as e:
        print(f"[post_reddit] WARNING: log-post failed: {e}")
        return None


def bump_campaigns(table, row_id, campaign_ids):
    """Attach a row in {posts,replies,dm_messages} to its applied campaigns."""
    if not row_id or not campaign_ids:
        return
    bump = os.path.join(REPO_DIR, "scripts", "campaign_bump.py")
    for cid in campaign_ids:
        try:
            subprocess.run(
                ["python3", bump,
                 "--table", table, "--id", str(row_id), "--campaign-id", str(cid)],
                capture_output=True, text=True, timeout=15,
            )
        except Exception as e:
            print(f"[post_reddit] WARNING: campaign_bump failed (id={row_id} c={cid}): {e}")


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


def _plan_iteration(args, config, reddit_username, already_picked):
    """Pick project → build prompt → run Claude → parse decisions. No browser.

    Returns a dict {project_name, decisions, cost, error?} or None if no project
    was eligible. Decisions list may be empty when Claude returns nothing usable.
    """
    if args.project:
        project = None
        for p in config.get("projects", []):
            if p["name"].lower() == args.project.lower():
                project = p
                break
        if not project:
            print(f"[post_reddit] ERROR: project '{args.project}' not found")
            return None
    else:
        project = pick_project("reddit", exclude=already_picked)
        if not project:
            print(f"[post_reddit] No eligible project left (already picked: {already_picked})")
            return None

    project_name = project.get("name", "general")
    print(f"[post_reddit] Project: {project_name}")

    top_report = get_top_performers(project_name)
    recent_comments = get_recent_comments()
    top_topics_report = get_top_search_topics(project_name, platform="reddit")
    prompt = build_prompt(project, config, args.limit, top_report, recent_comments,
                          top_topics_report=top_topics_report)

    if args.dry_run:
        print(f"=== DRY RUN (project={project_name}) ===")
        print(f"Prompt length: {len(prompt)} chars")
        print(prompt)
        print("=== END DRY RUN ===")
        return {"project_name": project_name, "decisions": [], "cost": 0.0, "dry_run": True}

    print(f"[post_reddit] Starting Claude session (limit={args.limit}, timeout={args.timeout}s)")
    start = time.time()
    ok, output, usage = run_claude(prompt, timeout=args.timeout)
    claude_elapsed = time.time() - start
    print(f"[post_reddit] Claude finished in {claude_elapsed:.0f}s (${usage['cost_usd']:.4f})")

    if not ok:
        print(f"[post_reddit] Claude FAILED: {output[:300]}")
        return {"project_name": project_name, "decisions": [], "cost": usage["cost_usd"], "error": "claude_failed"}

    decisions = parse_post_decisions(output)
    print(f"[post_reddit] Claude drafted {len(decisions)} post(s)")
    if not decisions:
        print(f"[post_reddit] No valid post decisions found in output:")
        for line in output.strip().split("\n")[-10:]:
            print(f"  {line}")

    return {"project_name": project_name, "decisions": decisions, "cost": usage["cost_usd"]}


def _post_iteration(plan, reddit_username):
    """Execute browser CDP posts for the decisions in plan. Returns (posted, failed)."""
    project_name = plan["project_name"]
    decisions = plan.get("decisions") or []

    if not decisions:
        return 0, 0

    active_campaigns = load_active_reddit_campaigns()
    if active_campaigns:
        for c in active_campaigns:
            print(f"[post_reddit] active campaign id={c['id']} "
                  f"sample_rate={c['sample_rate']:.3f} suffix={c['suffix']!r}")

    posted = 0
    failed = 0

    for i, decision in enumerate(decisions):
        thread_url = decision["thread_url"]
        reply_to_url = decision.get("reply_to_url")
        text = decision["text"]
        thread_author = decision.get("thread_author", "unknown")
        thread_title = decision.get("thread_title", "unknown")
        # validate_or_register accepts known styles, registers well-formed
        # new ones as candidates, and returns None for unknown-and-undocumented.
        # source_post URL is the thread we're replying to; we don't have our
        # own URL until after the post lands.
        engagement_style, _style_action = validate_or_register(
            decision,
            source_post={
                "platform": "reddit",
                "post_url": thread_url,
                "post_id": None,
                "model": decision.get("model"),
            },
        )
        search_topic = decision.get("search_topic") or None

        applied_campaign_ids = []
        for camp in active_campaigns:
            if random.random() < camp["sample_rate"]:
                text = text + camp["suffix"]
                applied_campaign_ids.append(camp["id"])
        if applied_campaign_ids:
            print(f"[post_reddit] applied campaigns {applied_campaign_ids} (suffix appended)")

        print(f"[post_reddit] Posting {i + 1}/{len(decisions)}: {thread_title[:50]}...")
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
            new_post_id = log_post(thread_url, permalink, text, project_name,
                     thread_author, thread_title, reddit_username,
                     engagement_style=engagement_style,
                     search_topic=search_topic)
            bump_campaigns("posts", new_post_id, applied_campaign_ids)
            posted += 1
            print(f"[post_reddit] POSTED: {permalink}")
        else:
            err = result.get("error", "unknown")
            failed += 1
            print(f"[post_reddit] CDP FAILED: {err}")
            if err == "account_blocked_in_sub":
                mark_comment_blocked(thread_url)

        if i < len(decisions) - 1:
            time.sleep(180)  # 3 min gap between posts within a single Claude session

    return posted, failed


def run_one_iteration(args, config, reddit_username, already_picked):
    """Backwards-compatible wrapper: plan + post in one call.

    Holds the browser lock continuously across the no-browser plan phase. New
    callers should drive plan/post separately at the shell level so the
    reddit-browser lock can be released around `_plan_iteration`'s Claude run.
    """
    plan = _plan_iteration(args, config, reddit_username, already_picked)
    if plan is None:
        return 0, 0, 0.0, None
    project_name = plan["project_name"]
    cost = plan.get("cost", 0.0)
    if plan.get("dry_run"):
        return 0, 0, cost, project_name
    if plan.get("error"):
        return 0, 1, cost, project_name
    posted, failed = _post_iteration(plan, reddit_username)
    return posted, failed, cost, project_name


def main():
    parser = argparse.ArgumentParser(description="Reddit posting orchestrator")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without executing")
    parser.add_argument("--limit", type=int, default=3, help="Max comments per Claude session (default: 3)")
    parser.add_argument("--iterations", type=int, default=1,
                        help="Sequential pick->draft->post cycles per run (default: 1). "
                             "Each iteration picks a different project.")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout for Claude session")
    parser.add_argument("--project", default=None, help="Override project selection (forces iterations=1)")
    parser.add_argument("--phase", choices=["plan", "post", "all"], default="all",
                        help="plan: pick project + Claude (no browser), writes JSON to --out. "
                             "post: read JSON from --in and post via CDP. all: legacy single-call.")
    parser.add_argument("--out", default=None, help="Plan output JSON path (--phase plan)")
    parser.add_argument("--in", dest="in_path", default=None, help="Plan input JSON path (--phase post)")
    parser.add_argument("--exclude", default="", help="Comma-separated project names to exclude (--phase plan)")
    args = parser.parse_args()

    if args.project and args.iterations > 1:
        print(f"[post_reddit] --project set, forcing iterations=1")
        args.iterations = 1

    config = load_config()
    reddit_username = config.get("accounts", {}).get("reddit", {}).get("username", "Deep_Ad1959")

    if args.phase == "plan":
        if not args.out:
            print("[post_reddit] ERROR: --phase plan requires --out PATH", file=sys.stderr)
            sys.exit(2)
        if not preflight_rate_limit():
            print("[post_reddit] rate-limited, plan skipped")
            sys.exit(3)
        excluded = [x.strip() for x in args.exclude.split(",") if x.strip()]
        plan = _plan_iteration(args, config, reddit_username, excluded)
        if plan is None:
            sys.exit(4)  # no eligible project
        with open(args.out, "w") as f:
            json.dump(plan, f)
        if plan.get("dry_run"):
            sys.exit(0)
        if plan.get("error"):
            sys.exit(5)
        if not plan.get("decisions"):
            sys.exit(6)  # no decisions to post
        return

    if args.phase == "post":
        if not args.in_path or not os.path.exists(args.in_path):
            print(f"[post_reddit] ERROR: --phase post requires --in PATH (got {args.in_path!r})", file=sys.stderr)
            sys.exit(2)
        with open(args.in_path) as f:
            plan = json.load(f)
        posted, failed = _post_iteration(plan, reddit_username)
        print(f"[post_reddit] phase=post project={plan.get('project_name')} posted={posted} failed={failed}")
        return

    # phase == "all": legacy single-call path
    run_start = time.time()
    total_posted = 0
    total_failed = 0
    total_skipped = 0
    total_cost = 0.0
    already_picked = []

    for iteration in range(args.iterations):
        print(f"\n[post_reddit] === iteration {iteration + 1}/{args.iterations} ===")

        if not preflight_rate_limit():
            total_skipped += args.iterations - iteration
            print(f"[post_reddit] rate-limited, skipping remaining {args.iterations - iteration} iteration(s)")
            break

        posted, failed, cost, project_name = run_one_iteration(
            args, config, reddit_username, already_picked,
        )
        total_posted += posted
        total_failed += failed
        total_cost += cost
        if project_name:
            already_picked.append(project_name)
        elif args.project is None:
            # Couldn't pick a project (all excluded or pick failure) — stop looping
            total_skipped += args.iterations - iteration
            break

    total_elapsed = time.time() - run_start

    print(f"\n[post_reddit] === RUN SUMMARY ===")
    print(f"[post_reddit] iterations={args.iterations} projects={already_picked}")
    print(f"[post_reddit] posted={total_posted} failed={total_failed} skipped={total_skipped} "
          f"elapsed={total_elapsed:.0f}s cost=${total_cost:.4f}")

    subprocess.run([
        "python3", os.path.join(REPO_DIR, "scripts", "log_run.py"),
        "--script", "post_reddit",
        "--posted", str(total_posted),
        "--skipped", str(total_skipped),
        "--failed", str(total_failed),
        "--cost", f"{total_cost:.4f}",
        "--elapsed", f"{total_elapsed:.0f}",
    ])


if __name__ == "__main__":
    main()
