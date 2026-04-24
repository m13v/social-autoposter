#!/usr/bin/env python3
"""run_github_cycle.py - phased GitHub Issues posting cycle.

Reduces volume by gating on:
  1. Historical (project, style) engagement signal in the drafter prompt.
  2. T0 -> T1 momentum gate: fetch comment/reaction counts now, sleep,
     re-fetch, keep only issues with positive delta.
  3. Adaptive cap: default 1, bump to 3 when >=3 candidates show momentum.

Phase 0: pick one project for this run, build context blocks.
Phase 1: gh search issues across project topics, snapshot T0 counts.
Sleep 600s.
Phase 2a: re-fetch same issues, compute delta.
Phase 2b: Claude drafts comments for pre-filtered top-N, Python posts + logs.

Usage:
    python3 scripts/run_github_cycle.py
    python3 scripts/run_github_cycle.py --sleep 300 --dry-run
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

REPO_DIR = os.path.expanduser("~/social-autoposter")
SCRIPTS = os.path.join(REPO_DIR, "scripts")
CONFIG_PATH = os.path.join(REPO_DIR, "config.json")
SKILL_FILE = os.path.join(REPO_DIR, "SKILL.md")
GITHUB_TOOLS = os.path.join(SCRIPTS, "github_tools.py")
RUN_CLAUDE = os.path.join(SCRIPTS, "run_claude.sh")
HISTORICAL = os.path.join(SCRIPTS, "historical_engagement.py")
PICK_PROJECT = os.path.join(SCRIPTS, "pick_project.py")

# --- Thresholds (tune here) -------------------------------------------------
DELTA_THRESHOLD = 1.0
HIGH_DELTA_BUMP = 3
CAP_DEFAULT = 1
CAP_BUMPED = 3
CLAUDE_CANDIDATE_LIMIT = 8
SEARCH_PER_TOPIC = 5           # gh search --limit per topic query
MAX_TOPICS_PER_PROJECT = 6


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def pick_project():
    try:
        out = subprocess.check_output(
            ["python3", PICK_PROJECT, "--platform", "github", "--json"],
            text=True, timeout=15,
        ).strip()
        if out:
            return json.loads(out)
    except Exception as e:
        log(f"pick_project failed: {e}")
    return None


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
    """Return (comment_count, reaction_count, body, author, title, url) for an issue."""
    try:
        out = subprocess.check_output(
            ["gh", "issue", "view", str(number), "-R", repo,
             "--json", "title,body,author,url,comments,reactionGroups,state"],
            text=True, timeout=30, stderr=subprocess.STDOUT,
        )
        data = json.loads(out)
    except Exception as e:
        return None

    if data.get("state") and data["state"].lower() != "open":
        return None

    comments = data.get("comments") or []
    comment_count = len(comments)

    reaction_count = 0
    for g in data.get("reactionGroups") or []:
        reaction_count += int((g.get("users") or {}).get("totalCount", 0) or g.get("totalCount", 0) or 0)

    return {
        "comment_count": comment_count,
        "reaction_count": reaction_count,
        "title": data.get("title", ""),
        "body": (data.get("body") or "")[:1500],
        "author": (data.get("author") or {}).get("login", ""),
        "url": data.get("url", ""),
    }


def delta_score(c0, r0, c1, r1):
    return 3.0 * max(c1 - c0, 0) + 2.0 * max(r1 - r0, 0)


def parse_repo_number(url):
    # https://github.com/owner/repo/issues/123
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/(?:issues|pull)/(\d+)", url or "")
    if not m:
        return None, None
    return f"{m.group(1)}/{m.group(2)}", int(m.group(3))


def get_top_search_topics(project_name, platform="github", limit=8, window_days=30):
    """Top-performing search_topic seeds for this project on this platform.
    Empty string if no signal yet. Mirrors the Reddit pattern."""
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


def build_prompt(candidates, cap, project, history_block, styles_block, content_angle,
                 top_topics_report=""):
    cand_block = []
    for i, c in enumerate(candidates, 1):
        topic_line = f"seed: {c['search_topic']}\n" if c.get("search_topic") else ""
        cand_block.append(
            f"--- #{i} {c['repo']}#{c['number']} delta={c['delta_score']:.1f} "
            f"(cm {c['comment_count_t0']}->{c['comment_count_t1']}, "
            f"rx {c['reaction_count_t0']}->{c['reaction_count_t1']}) ---\n"
            f"{topic_line}"
            f"title: {c['title']}\n"
            f"author: {c['author']}\n"
            f"body: {c['body']}\n"
            f"url: {c['url']}\n"
        )
    candidates_text = "\n".join(cand_block)

    top_topics_ctx = ""
    if top_topics_report:
        top_topics_ctx = f"""
## Search-topic feedback (which seeds actually led to engagement):
{top_topics_report}

Prefer issues whose seed has a higher total/avg score when the fit is genuine.
New seeds with 0 posts are fine, we need to explore.
"""

    return f"""You are the Social Autoposter drafting GitHub issue comments for project {project['name']}.

Read {SKILL_FILE} for content rules (no em dashes, anti-AI tells, voice).

## Project context
{content_angle}

## Pre-filtered candidates (top {len(candidates)} by recent engagement delta)

Each candidate carries the search seed it came from. Echo that seed back in
"search_topic" so we can score which seeds produce engagement.

{candidates_text}

{styles_block}

{history_block}
{top_topics_ctx}
## YOUR JOB

Pick AT MOST {cap} issues and draft a helpful, technical comment for each.
**Post fewer than {cap} if fewer are genuinely on-brand.**

Rules:
- Comments must add concrete technical value: a specific answer, a code pointer,
  a gotcha you've hit, a small script. No generic product pitches.
- Plain prose, no markdown headings. One short paragraph is fine.
- Consult the historical engagement table. Prefer [good] (project, style) pairs;
  avoid [dead] pairs unless the issue is an unusually strong fit.
- Match engagement_style to the thread: bug report -> critic/pattern_recognizer,
  feature question -> storyteller/data_point_drop, unclear -> curious_probe.
- Mention the product only when it's a natural fit. Never start with the product name.

## OUTPUT FORMAT

Return ONLY a single JSON object, no prose, no markdown fencing:

{{
  "posts": [
    {{
      "repo": "<owner/repo>",
      "number": <issue number>,
      "thread_url": "<issue url>",
      "thread_title": "<issue title>",
      "thread_author": "<issue author>",
      "matched_project": "{project['name']}",
      "engagement_style": "<style>",
      "search_topic": "<the seed from the candidate block, copied verbatim>",
      "comment_text": "<the actual comment to post>"
    }}
  ],
  "skipped": [
    {{ "url": "<issue url>", "reason": "<short reason>" }}
  ]
}}

CRITICAL: Do NOT call gh or any Bash tool. Only return the JSON. The orchestrator posts."""


def parse_claude_json(output):
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
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i; break
    if end < 0:
        return None
    try:
        return json.loads(result[start : end + 1])
    except Exception:
        return None


def post_and_log(decisions, claude_session_id):
    dbmod.load_env()
    conn = dbmod.get_conn()
    posted = 0
    failed = 0

    for p in decisions.get("posts", []):
        repo = p.get("repo")
        number = p.get("number")
        text = (p.get("comment_text") or "").strip()
        if not repo or not number or not text:
            failed += 1
            continue

        try:
            proc = subprocess.run(
                ["gh", "issue", "comment", str(number), "-R", repo, "--body", text],
                capture_output=True, text=True, timeout=60,
            )
        except Exception as e:
            log(f"  post error {repo}#{number}: {e}")
            failed += 1
            continue

        if proc.returncode != 0:
            log(f"  gh comment failed {repo}#{number}: {proc.stderr.strip()[:200]}")
            failed += 1
            continue

        # gh prints the new comment URL on success
        our_url = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""

        conn.execute(
            """
            INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
                thread_title, thread_content, our_url, our_content, our_account,
                source_summary, project_name, engagement_style, feedback_report_used,
                language, status, posted_at, claude_session_id, search_topic)
            VALUES ('github', %s, %s, %s, %s, %s, %s, %s, 'm13v',
                'github cycle comment', %s, %s, TRUE, 'en', 'active', NOW(), %s::uuid, %s)
            """,
            [
                p.get("thread_url", ""),
                p.get("thread_author", ""),
                p.get("thread_author", ""),
                p.get("thread_title", ""),
                "",
                our_url,
                text,
                p.get("matched_project", ""),
                p.get("engagement_style", ""),
                claude_session_id,
                (p.get("search_topic") or "").strip() or None,
            ],
        )
        posted += 1
        log(f"  posted {repo}#{number}  style={p.get('engagement_style')}")

    conn.commit()
    conn.close()
    return posted, failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sleep", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run_start = time.time()
    log(f"=== GitHub Cycle: sleep={args.sleep}s ===")

    project = pick_project()
    if not project:
        log("No project picked. Exiting.")
        return 0
    log(f"Project: {project.get('name')}")

    try:
        history_block = subprocess.run(
            ["python3", HISTORICAL, "--platform", "github"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        history_block = "## Historical engagement\n(unavailable)\n"

    try:
        styles_block = subprocess.run(
            ["bash", "-c", f"source {REPO_DIR}/skill/styles.sh && generate_styles_block github posting"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        styles_block = ""

    content_angle = (
        f"name: {project.get('name')}\n"
        f"description: {project.get('description', '')}\n"
        f"website: {project.get('website', '')}\n"
        f"github: {(project.get('github_repo') or '')}\n"
    )

    # --- Phase 1: scan T0 ---------------------------------------------------
    # Prefer unified search_topics (per 2026-04-24 migration); fall back to legacy lists.
    topics = (project.get("search_topics")
              or project.get("github_topics")
              or project.get("topics")
              or [])[:MAX_TOPICS_PER_PROJECT]
    if not topics:
        log("Project has no topics to search. Exiting.")
        return 0

    log(f"Phase 1: searching {len(topics)} topic queries...")
    raw = []
    seen_urls = set()
    for topic in topics:
        for item in gh_search(topic):
            url = item.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                # Stamp the originating seed so we can carry it through to the
                # INSERT and feed it back into top_search_topics scoring.
                item["search_topic"] = topic
                raw.append(item)

    log(f"Phase 1: {len(raw)} unique issues after dedup + already-posted filter")
    if not raw:
        log("No candidates. Exiting.")
        return 0

    # Snapshot T0 counts
    candidates = []
    for item in raw[:CLAUDE_CANDIDATE_LIMIT * 3]:  # cap the fetch count
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
        return 0

    # --- Sleep --------------------------------------------------------------
    log(f"Sleeping {args.sleep}s before T1...")
    time.sleep(args.sleep)

    # --- Phase 2a: re-poll T1 ----------------------------------------------
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

    # --- Phase 2b: adaptive cap + Claude ------------------------------------
    high_delta = [c for c in candidates if c["delta_score"] >= DELTA_THRESHOLD]
    cap = CAP_BUMPED if len(high_delta) >= HIGH_DELTA_BUMP else CAP_DEFAULT
    log(f"Phase 2b: {len(high_delta)} high-momentum candidates -> cap = {cap}")

    candidates.sort(key=lambda c: c["delta_score"], reverse=True)
    top = candidates[:CLAUDE_CANDIDATE_LIMIT]
    log(f"Phase 2b: showing Claude top {len(top)} by delta, cap = {cap}")

    if args.dry_run:
        for c in top[:cap]:
            log(f"  would consider {c['repo']}#{c['number']} delta={c['delta_score']:.1f} "
                f"title={c['title'][:60]}")
        return 0

    claude_session_id = str(uuid.uuid4())
    os.environ["CLAUDE_SESSION_ID"] = claude_session_id
    top_topics_report = get_top_search_topics(project.get("name", ""), platform="github")
    prompt = build_prompt(top, cap, project, history_block, styles_block, content_angle,
                          top_topics_report=top_topics_report)

    log("Phase 2b: invoking Claude for drafting...")
    try:
        proc = subprocess.run(
            [RUN_CLAUDE, "run-github-cycle",
             "--strict-mcp-config",
             "--mcp-config", os.path.expanduser("~/.claude/browser-agent-configs/no-agents-mcp.json"),
             "-p", "--output-format", "json", prompt],
            capture_output=True, text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        log("Claude timed out after 900s")
        return 1

    if proc.returncode != 0:
        log(f"Claude exited rc={proc.returncode}: {proc.stderr[-500:]}")
        return 1

    decisions = parse_claude_json(proc.stdout)
    if not decisions:
        log("Could not parse Claude JSON output.")
        log(f"Last 500 chars: {proc.stdout[-500:]}")
        return 1

    log(f"Claude picked {len(decisions.get('posts', []))}, skipped {len(decisions.get('skipped', []))}")

    posted, failed = post_and_log(decisions, claude_session_id)

    elapsed = int(time.time() - run_start)
    log(f"=== Cycle complete: posted={posted}, failed={failed}, elapsed={elapsed}s ===")

    try:
        subprocess.run(
            ["python3", os.path.join(SCRIPTS, "log_run.py"),
             "--script", "run-github-cycle",
             "--posted", str(posted), "--skipped", str(len(decisions.get("skipped", []))),
             "--failed", str(failed), "--cost", "0", "--elapsed", str(elapsed)],
            timeout=15,
        )
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
