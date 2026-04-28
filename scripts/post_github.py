#!/usr/bin/env python3
"""GitHub Issues posting orchestrator with momentum-gated candidate selection.

Two-phase design (consolidated 2026-04-24, replacing the short-lived
run_github_cycle.py):

  Phase 1: search project topics across N seeds, snapshot T0 comment + reaction
           counts. The originating seed is stamped on every candidate so the
           feedback loop (top_search_topics.py) gets fed back into the next run.
  Sleep --sleep seconds (default 600).
  Phase 2a: re-poll every candidate, compute delta_score = 3*Δcomments + 2*Δreactions.
  Phase 2b: adaptive cap (CAP_DEFAULT, bumped to CAP_BUMPED when >= HIGH_DELTA_BUMP
            candidates clear DELTA_THRESHOLD), Claude only drafts comments — no
            Bash tools, no in-flight searches, single JSON response. Python posts
            via gh and persists everything (search_topic, language, engagement_style,
            claude_session_id) to the posts table.

Why a single Python orchestrator instead of letting Claude search itself:
the pre-filter cuts Claude's tool budget to zero, the momentum gate suppresses
posts on stale threads, and the seed-per-candidate signal closes the
top_search_topics feedback loop. Claude returns one JSON in one shot.

Usage:
    python3 scripts/post_github.py
    python3 scripts/post_github.py --sleep 60 --dry-run         # quick dev
    python3 scripts/post_github.py --project Fazm               # force project
    python3 scripts/post_github.py --limit 5                    # caps adaptive cap
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod
from engagement_styles import (
    VALID_STYLES, get_styles_prompt, get_content_rules, get_anti_patterns,
)

REPO_DIR = os.path.expanduser("~/social-autoposter")
SCRIPTS = os.path.join(REPO_DIR, "scripts")
CONFIG_PATH = os.path.join(REPO_DIR, "config.json")
SKILL_FILE = os.path.join(REPO_DIR, "SKILL.md")
GITHUB_TOOLS = os.path.join(SCRIPTS, "github_tools.py")
RUN_CLAUDE = os.path.join(SCRIPTS, "run_claude.sh")

# Momentum tunables. Edit here, not at call sites.
DELTA_THRESHOLD = 1.0
HIGH_DELTA_BUMP = 3
CAP_DEFAULT = 1
CAP_BUMPED = 3
CLAUDE_CANDIDATE_LIMIT = 8     # show top N to Claude
SEARCH_PER_TOPIC = 5            # gh search --limit per topic
MAX_TOPICS_PER_PROJECT = 6


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [post_github] {msg}", flush=True)


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ---------- Project picking & context ---------------------------------------

def get_top_performers(project_name, platform="github"):
    try:
        result = subprocess.run(
            ["python3", os.path.join(SCRIPTS, "top_performers.py"),
             "--platform", platform, "--project", project_name],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def get_top_search_topics(project_name, platform="github", limit=8, window_days=30):
    """Best-performing search_topic seeds for this project on this platform.
    Empty string if no data yet. Mirrors post_reddit.get_top_search_topics."""
    try:
        result = subprocess.run(
            ["python3", os.path.join(SCRIPTS, "top_search_topics.py"),
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
        "WHERE platform='github' ORDER BY id DESC LIMIT %s",
        [limit],
    )
    results = [row[0] for row in cur.fetchall()]
    conn.close()
    return results


def recent_github_posts_by_project(days=7):
    """project_name -> count of github posts in last N days, for deficit weighting."""
    dbmod.load_env()
    conn = dbmod.get_conn()
    rows = conn.execute(
        "SELECT project_name, COUNT(*) FROM posts "
        "WHERE platform='github' "
        "  AND posted_at > NOW() - INTERVAL '%s days' "
        "  AND project_name IS NOT NULL "
        "GROUP BY project_name" % int(days)
    ).fetchall()
    conn.close()
    return {name: int(cnt) for name, cnt in rows}


def pick_github_project(config, recent_counts):
    """Inverse-recent-share weighting. Eligibility: enabled, weight>0, has
    a non-empty unified search_topics list (or legacy github_search_topics)."""
    pool = [
        p for p in config.get("projects", [])
        if p.get("enabled", True)
        and p.get("weight", 0) > 0
        and (p.get("search_topics") or p.get("github_search_topics"))
    ]
    if not pool:
        return None
    weights = [
        p["weight"] / (1 + recent_counts.get(p["name"], 0))
        for p in pool
    ]
    return random.choices(pool, weights=weights, k=1)[0]


def _angle_str(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_angle_str(x)}" for k, x in v.items() if x)
    if isinstance(v, (list, tuple)):
        return ", ".join(_angle_str(x) for x in v if x)
    return str(v) if v else ""


def build_content_angle(project, config):
    """Rich angle: prefer content_angle override, otherwise compose from
    description / differentiator / icp / setup / messaging / voice."""
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


# ---------- Phase 1 / 2 momentum helpers ------------------------------------

def gh_search(query, limit=SEARCH_PER_TOPIC):
    try:
        out = subprocess.check_output(
            ["python3", GITHUB_TOOLS, "search", query, "--limit", str(limit)],
            text=True, timeout=45,
        )
        items = json.loads(out)
    except Exception as e:
        log(f"  gh_search failed for '{query}': {e}")
        return []
    return [i for i in items if not i.get("already_posted")]


def gh_view_counts(repo, number):
    """Return dict{comment_count, reaction_count, title, body, author, url} or
    None if the issue is no longer open / unfetchable."""
    try:
        out = subprocess.check_output(
            ["gh", "issue", "view", str(number), "-R", repo,
             "--json", "title,body,author,url,comments,reactionGroups,state"],
            text=True, timeout=30, stderr=subprocess.STDOUT,
        )
        data = json.loads(out)
    except Exception:
        return None
    if data.get("state") and data["state"].lower() != "open":
        return None
    comments = data.get("comments") or []
    reaction_count = 0
    for g in data.get("reactionGroups") or []:
        reaction_count += int(
            (g.get("users") or {}).get("totalCount", 0) or g.get("totalCount", 0) or 0
        )
    return {
        "comment_count": len(comments),
        "reaction_count": reaction_count,
        "title": data.get("title", ""),
        "body": (data.get("body") or "")[:1500],
        "author": (data.get("author") or {}).get("login", ""),
        "url": data.get("url", ""),
    }


def delta_score(c0, r0, c1, r1):
    return 3.0 * max(c1 - c0, 0) + 2.0 * max(r1 - r0, 0)


def parse_repo_number(url):
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/(?:issues|pull)/(\d+)", url or "")
    if not m:
        return None, None
    return f"{m.group(1)}/{m.group(2)}", int(m.group(3))


def parse_issue_url(url):
    if not url:
        return None, None, None
    m = re.search(r"github\.com/([^/]+)/([^/]+)/(?:issues|pull)/(\d+)", url)
    if not m:
        return None, None, None
    return m.group(1), m.group(2), int(m.group(3))


# ---------- Prompt -----------------------------------------------------------

def build_prompt(project, config, candidates, cap, top_report, recent_comments,
                 top_topics_report=""):
    content_angle = build_content_angle(project, config)
    excluded_repos = config.get("exclusions", {}).get("github_repos", [])
    excluded_authors = config.get("exclusions", {}).get("authors", [])

    cand_block = []
    for i, c in enumerate(candidates, 1):
        seed_line = f"seed: {c['search_topic']}\n" if c.get("search_topic") else ""
        cand_block.append(
            f"--- #{i} {c['repo']}#{c['number']} delta={c['delta_score']:.1f} "
            f"(cm {c['comment_count_t0']}->{c['comment_count_t1']}, "
            f"rx {c['reaction_count_t0']}->{c['reaction_count_t1']}) ---\n"
            f"{seed_line}"
            f"title: {c['title']}\n"
            f"author: {c['author']}\n"
            f"url: {c['url']}\n"
            f"body: {c['body']}\n"
        )
    candidates_text = "\n".join(cand_block)

    recent_ctx = ""
    if recent_comments:
        snippets = "\n".join(f"  - {c}" for c in recent_comments if c)
        if snippets:
            recent_ctx = f"""
Your last {len(recent_comments)} GitHub comments (don't repeat talking points):
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

Prefer issues whose seed has a higher total/avg score when the fit is genuine.
New seeds with 0 posts are fine, we still need to explore.
"""

    return f"""You are the Social Autoposter drafting GitHub issue comments for project {project['name']}.

Read {SKILL_FILE} for content rules (no em dashes, anti-AI tells, voice).

## Project context
{content_angle}

## Pre-filtered candidates (top {len(candidates)} by recent engagement delta)

Each candidate already cleared exclusion + already-posted filtering. The seed
shown is the search_topic that surfaced the issue, echo it back verbatim in
"search_topic" so we can score which seeds produce engagement.

{candidates_text}
{recent_ctx}{top_ctx}{top_topics_ctx}
{get_styles_prompt("github", context="posting")}

## Targeting
- Best topics: Agents, Accessibility, Voice/ASR, Tool Use. Prioritize when present.
- Prefer small-to-mid repos (<1000 stars) where the maintainer is active.
- Exclusions are already filtered, but for reference:
  - Excluded repos: {', '.join(excluded_repos) if excluded_repos else '(none)'}
  - Excluded authors: {', '.join(excluded_authors) if excluded_authors else '(none)'}

## Comment style
- Lead with the pain you hit, then your fix. "the token overhead is brutal" beats "here is how to optimize".
- Conversational, no markdown headings, no code blocks unless tiny.
- 400-600 chars. Short enough to read, long enough to show real experience.
- Specific (file names, metrics, tradeoffs), not generic advice.
- NO links. Links are added later by Phase D after the comment earns engagement.

## YOUR JOB

Pick AT MOST {cap} candidates and draft one comment for each.
Post fewer than {cap} if fewer are genuinely on-brand.

## Content rules
{get_content_rules("github")}

{get_anti_patterns()}

## OUTPUT FORMAT

Return ONLY a single JSON object. No prose, no markdown fencing, no Bash calls:

{{
  "posts": [
    {{
      "repo": "<owner/repo>",
      "number": <issue number>,
      "thread_url": "<issue url>",
      "thread_title": "<issue title>",
      "thread_author": "<issue author>",
      "matched_project": "{project['name']}",
      "engagement_style": "<one of {', '.join(sorted(VALID_STYLES))}>",
      "search_topic": "<the seed from the candidate block, copied verbatim>",
      "language": "<ISO 639-1 code matching the issue language: en, ja, zh, es, ...>",
      "comment_text": "<the actual comment to post, 400-600 chars, NO links>"
    }}
  ],
  "skipped": [
    {{ "url": "<issue url>", "reason": "<short reason>" }}
  ]
}}

CRITICAL: Do NOT call gh, Bash, or any tool. The orchestrator already searched
and viewed; just return the JSON.
"""


# ---------- Claude one-shot (no tools needed since pre-filter is in Python) -

def run_claude(prompt, timeout=900):
    """One-shot non-streaming Claude via run_claude.sh wrapper. Returns
    (ok, raw_stdout, usage_dict)."""
    usage = {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
             "cache_read": 0, "cache_create": 0}
    cmd = [RUN_CLAUDE, "post_github",
           "--strict-mcp-config",
           "--mcp-config", os.path.expanduser("~/.claude/browser-agent-configs/no-agents-mcp.json"),
           "-p", "--output-format", "json", prompt]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT", usage
    try:
        outer = json.loads(proc.stdout)
        usage["cost_usd"] = float(outer.get("total_cost_usd", 0.0) or 0.0)
        u = outer.get("usage", {}) or {}
        usage["input_tokens"] = int(u.get("input_tokens", 0) or 0)
        usage["output_tokens"] = int(u.get("output_tokens", 0) or 0)
        usage["cache_read"] = int(u.get("cache_read_input_tokens", 0) or 0)
        usage["cache_create"] = int(u.get("cache_creation_input_tokens", 0) or 0)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return proc.returncode == 0, proc.stdout, usage


def parse_claude_json(output):
    """Extract the inner JSON object from --output-format json envelope."""
    try:
        outer = json.loads(output)
        result = outer.get("result", "") if isinstance(outer, dict) else str(outer)
    except Exception:
        result = output
    start = result.find("{")
    if start < 0:
        return None
    depth, in_str, esc, end = 0, False, False, -1
    for i in range(start, len(result)):
        ch = result[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None
    try:
        return json.loads(result[start:end + 1])
    except Exception:
        return None


# ---------- Posting + logging ------------------------------------------------

def post_comment(owner, repo, number, body):
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
             github_username, engagement_style=None, search_topic=None, language=None,
             claude_session_id=None):
    """Defers to github_tools.py log-post, which handles dedup + INSERT."""
    try:
        cmd = ["python3", GITHUB_TOOLS, "log-post",
               thread_url, our_url or "", text, project_name,
               thread_author or "unknown", thread_title or "",
               "--account", github_username]
        if engagement_style:
            cmd.extend(["--engagement-style", engagement_style])
        if search_topic:
            cmd.extend(["--search-topic", search_topic])
        if language:
            cmd.extend(["--language", language])
        if claude_session_id:
            cmd.extend(["--claude-session-id", claude_session_id])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.stdout.strip():
            try:
                parsed = json.loads(result.stdout.strip())
                if parsed.get("error"):
                    log(f"log-post error: {parsed}")
            except json.JSONDecodeError:
                pass
    except Exception as e:
        log(f"WARNING: log-post failed: {e}")


# ---------- Main -------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="GitHub Issues posting orchestrator (momentum-gated)")
    parser.add_argument("--sleep", type=int, default=600,
                        help="Phase 1 -> Phase 2 momentum window in seconds (default 600)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Hard ceiling on posts per run; caps the adaptive cap")
    parser.add_argument("--timeout", type=int, default=900,
                        help="Claude drafting timeout in seconds")
    parser.add_argument("--project", default=None, help="Override project selection")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompt + would-post candidates; do not invoke Claude or post")
    args = parser.parse_args()

    run_start = time.time()
    log(f"=== GitHub run: sleep={args.sleep}s ===")

    config = load_config()
    github_username = config.get("accounts", {}).get("github", {}).get("username", "m13v")

    # ---- Pick project ------------------------------------------------------
    if args.project:
        project = next(
            (p for p in config.get("projects", [])
             if p.get("name", "").lower() == args.project.lower()),
            None,
        )
        if not project:
            log(f"ERROR: project '{args.project}' not found")
            sys.exit(1)
        project_name = project.get("name")
        log(f"Project (forced): {project_name}")
    else:
        recent_counts = recent_github_posts_by_project(days=7)
        project = pick_github_project(config, recent_counts)
        if project is None:
            log("ERROR: no eligible project (none have search_topics)")
            sys.exit(1)
        project_name = project.get("name")
        log(f"Project (deficit-weighted): {project_name} "
            f"(weight={project.get('weight', 0)}, posts_7d={recent_counts.get(project_name, 0)})")

    # ---- Phase 1: search topics, T0 snapshot -------------------------------
    topics_pool = list(project.get("search_topics")
                       or project.get("github_search_topics")
                       or project.get("topics")
                       or [])
    if not topics_pool:
        log("Project has no topics to search. Exiting.")
        sys.exit(0)
    # Shuffle before slicing so each run samples a different MAX_TOPICS_PER_PROJECT
    # subset. Without this, projects with >6 seeds always query the first 6, which
    # starves diverse coverage and biases top_search_topics scoring (c0nsl run on
    # 2026-04-24 yielded only 2 candidates because its first 6 seeds were narrow).
    random.shuffle(topics_pool)
    topics = topics_pool[:MAX_TOPICS_PER_PROJECT]

    log(f"Phase 1: searching {len(topics)} topic queries...")
    raw = []
    seen_urls = set()
    for topic in topics:
        for item in gh_search(topic):
            url = item.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                # Stamp the originating seed so it survives dedup -> INSERT and
                # feeds top_search_topics scoring on the next run.
                item["search_topic"] = topic
                raw.append(item)
    log(f"Phase 1: {len(raw)} unique issues after dedup + already-posted filter")
    if not raw:
        log("No candidates. Exiting.")
        sys.exit(0)

    candidates = []
    for item in raw[:CLAUDE_CANDIDATE_LIMIT * 3]:
        repo, number = parse_repo_number(item.get("url"))
        if not repo:
            continue
        counts = gh_view_counts(repo, number)
        if not counts:
            continue
        candidates.append({
            "repo": repo,
            "number": number,
            "url": counts["url"],
            "title": counts["title"],
            "body": counts["body"],
            "author": counts["author"],
            "comment_count_t0": counts["comment_count"],
            "reaction_count_t0": counts["reaction_count"],
            "search_topic": item.get("search_topic"),
        })
    log(f"Phase 1: {len(candidates)} candidates with T0 snapshot")
    if not candidates:
        log("No live open issues to re-poll. Exiting.")
        sys.exit(0)

    # ---- Sleep -------------------------------------------------------------
    log(f"Sleeping {args.sleep}s before T1...")
    time.sleep(args.sleep)

    # ---- Phase 2a: re-poll T1 ---------------------------------------------
    log("Phase 2a: re-polling T1 counts...")
    for c in candidates:
        counts = gh_view_counts(c["repo"], c["number"])
        if not counts:
            c["comment_count_t1"] = c["comment_count_t0"]
            c["reaction_count_t1"] = c["reaction_count_t0"]
            c["delta_score"] = 0.0
            continue
        c["comment_count_t1"] = counts["comment_count"]
        c["reaction_count_t1"] = counts["reaction_count"]
        c["delta_score"] = delta_score(
            c["comment_count_t0"], c["reaction_count_t0"],
            c["comment_count_t1"], c["reaction_count_t1"],
        )

    # ---- Phase 2b: adaptive cap -------------------------------------------
    high_delta = [c for c in candidates if c["delta_score"] >= DELTA_THRESHOLD]
    cap = CAP_BUMPED if len(high_delta) >= HIGH_DELTA_BUMP else CAP_DEFAULT
    if args.limit is not None:
        cap = min(cap, max(0, args.limit))
    log(f"Phase 2b: {len(high_delta)} high-momentum candidates -> cap = {cap}")

    candidates.sort(key=lambda c: c["delta_score"], reverse=True)
    top = candidates[:CLAUDE_CANDIDATE_LIMIT]
    log(f"Phase 2b: showing Claude top {len(top)} by delta, cap = {cap}")

    if cap <= 0:
        log("cap=0, nothing to post. Exiting.")
        sys.exit(0)

    top_report = get_top_performers(project_name)
    recent_comments = get_recent_comments()
    top_topics_report = get_top_search_topics(project_name, platform="github")

    prompt = build_prompt(project, config, top, cap, top_report,
                          recent_comments, top_topics_report=top_topics_report)

    if args.dry_run:
        log("=== DRY RUN ===")
        log(f"Prompt length: {len(prompt)} chars")
        for c in top[:cap]:
            log(f"  would consider {c['repo']}#{c['number']} "
                f"delta={c['delta_score']:.1f} title={c['title'][:60]}")
        return

    # ---- Phase 2b: invoke Claude (one-shot, no tools) ----------------------
    claude_session_id = str(uuid.uuid4())
    os.environ["CLAUDE_SESSION_ID"] = claude_session_id
    log("Phase 2b: invoking Claude for drafting...")
    claude_start = time.time()
    ok, output, usage = run_claude(prompt, timeout=args.timeout)
    log(f"Claude finished in {time.time() - claude_start:.0f}s (${usage['cost_usd']:.4f})")

    if not ok:
        log(f"Claude FAILED: {output[:300]}")
        subprocess.run([
            "python3", os.path.join(SCRIPTS, "log_run.py"),
            "--script", "post_github",
            "--posted", "0", "--skipped", "0", "--failed", "1",
            "--cost", f"{usage['cost_usd']:.4f}",
            "--elapsed", f"{int(time.time() - run_start)}",
        ])
        sys.exit(1)

    decisions = parse_claude_json(output) or {}
    posts = decisions.get("posts", []) or []
    skipped = decisions.get("skipped", []) or []
    log(f"Claude picked {len(posts)}, skipped {len(skipped)}")

    if not posts:
        log("No valid post decisions. Last 500 chars of output:")
        log(output.strip()[-500:])

    posted = 0
    failed = 0
    for i, decision in enumerate(posts):
        thread_url = decision.get("thread_url", "")
        text = (decision.get("comment_text") or "").strip()
        thread_author = decision.get("thread_author", "unknown")
        thread_title = decision.get("thread_title", "")
        engagement_style = decision.get("engagement_style")
        if engagement_style and engagement_style not in VALID_STYLES:
            log(f"unknown style '{engagement_style}', clearing")
            engagement_style = None
        language = (decision.get("language") or "en").strip().lower()[:5] or "en"

        owner, repo, number = parse_issue_url(thread_url)
        if not owner or not text:
            log(f"SKIP: bad URL or empty text: {thread_url}")
            failed += 1
            continue

        log(f"Posting {i + 1}/{len(posts)} -> {owner}/{repo}#{number}: {thread_title[:60]}")
        ok_post, url_or_err = post_comment(owner, repo, number, text)
        if not ok_post:
            log(f"POST FAILED: {url_or_err}")
            failed += 1
            time.sleep(3)
            continue

        log_post(
            thread_url, url_or_err, text,
            decision.get("matched_project") or project_name,
            thread_author, thread_title, github_username,
            engagement_style=engagement_style,
            search_topic=(decision.get("search_topic") or "").strip() or None,
            language=language,
            claude_session_id=claude_session_id,
        )
        posted += 1
        log(f"POSTED: {url_or_err or 'ok'}")
        time.sleep(3)

    total_elapsed = time.time() - run_start
    log(f"=== SUMMARY: elapsed={total_elapsed:.0f}s posted={posted} failed={failed} ===")
    log(f"Tokens: input={usage['input_tokens']} output={usage['output_tokens']} "
        f"cache_read={usage['cache_read']} cache_create={usage['cache_create']}")
    log(f"Cost: ${usage['cost_usd']:.4f}")

    subprocess.run([
        "python3", os.path.join(SCRIPTS, "log_run.py"),
        "--script", "post_github",
        "--posted", str(posted),
        "--skipped", str(len(skipped)),
        "--failed", str(failed),
        "--cost", f"{usage['cost_usd']:.4f}",
        "--elapsed", f"{int(total_elapsed)}",
    ])


if __name__ == "__main__":
    main()
