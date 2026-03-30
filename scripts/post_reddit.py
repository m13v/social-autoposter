#!/usr/bin/env python3
"""Programmatic Reddit poster — Playwright for browser, Claude API for drafting.

Replaces the token-heavy `claude -p` approach. Claude only sees thread text
and drafts a comment (~500 tokens per post instead of ~50k).

Usage:
    python3 scripts/post_reddit.py                    # default: 1 post
    python3 scripts/post_reddit.py --max-posts 10     # up to 10 posts
    python3 scripts/post_reddit.py --dry-run           # draft but don't post
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
BROWSER_STATE = os.path.expanduser("~/.claude/browser-sessions.json")
CHROMIUM_PATH = None  # auto-detect

# ---------------------------------------------------------------------------
# Config / helpers
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def pick_project(platform="reddit"):
    """Call existing pick_project.py and return (name, config_dict)."""
    name = subprocess.check_output(
        ["python3", f"{REPO_DIR}/scripts/pick_project.py", "--platform", platform],
        text=True,
    ).strip()
    config_json = subprocess.check_output(
        ["python3", f"{REPO_DIR}/scripts/pick_project.py", "--platform", platform, "--json"],
        text=True,
    ).strip()
    return name, json.loads(config_json)


def get_top_report(platform, project):
    """Call existing top_performers.py and return the text report."""
    try:
        return subprocess.check_output(
            ["python3", f"{REPO_DIR}/scripts/top_performers.py",
             "--platform", platform, "--project", project],
            text=True, timeout=30,
        ).strip()
    except Exception:
        return "(top performers report unavailable)"


def find_threads(project):
    """Call existing find_threads.py and return parsed JSON."""
    out = subprocess.check_output(
        ["python3", f"{REPO_DIR}/scripts/find_threads.py", "--project", project],
        text=True, timeout=60,
    )
    return json.loads(out)


def get_recent_comments(limit=5):
    """Return our last N comments for repetition avoidance."""
    conn = dbmod.get_conn()
    rows = conn.execute(
        "SELECT our_content FROM posts WHERE platform='reddit' "
        "ORDER BY id DESC LIMIT %s", [limit]
    ).fetchall()
    conn.close()
    return [row[0] for row in rows if row[0]]


def log_post(platform, thread_url, thread_author, thread_title, thread_content,
             our_url, our_content, our_account, project_name, source_summary="script:post_reddit.py"):
    """Insert a post record into the database."""
    conn = dbmod.get_conn()
    conn.execute(
        "INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle, "
        "thread_title, thread_content, our_url, our_content, our_account, "
        "source_summary, project_name, feedback_report_used, status, posted_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, 'active', NOW())",
        [platform, thread_url, thread_author, thread_author,
         thread_title, thread_content[:2000] if thread_content else None,
         our_url, our_content, our_account,
         source_summary, project_name],
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Playwright browser automation
# ---------------------------------------------------------------------------

def launch_browser():
    """Launch a persistent Chromium context with Reddit session cookies."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()

    # Find Chromium — prefer Playwright's bundled one
    chromium_path = CHROMIUM_PATH
    if not chromium_path:
        try:
            result = subprocess.check_output(
                ["python3", "-c",
                 "from playwright._impl._driver import compute_driver_executable; "
                 "import subprocess, json; "
                 "proc = subprocess.run([compute_driver_executable(), 'print-api-json'], "
                 "capture_output=True, text=True); "
                 "print('ok')"],
                text=True, timeout=10,
            )
        except Exception:
            pass

    browser = pw.chromium.launch(
        headless=False,
        args=[
            "--window-position=2131,-1032",
            "--window-size=911,1016",
        ],
    )

    # Load storage state (cookies/sessions) from the reddit-agent config
    context = browser.new_context(
        storage_state=BROWSER_STATE,
        viewport={"width": 911, "height": 1016},
    )
    context.set_default_timeout(30000)
    page = context.new_page()
    return pw, browser, context, page


def extract_thread_content(page, url):
    """Navigate to an old.reddit.com thread and extract text content.

    Returns dict with title, body, author, comments (list of dicts).
    """
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # Extract via JS on old.reddit.com
    data = page.evaluate("""() => {
        const title = document.querySelector('.title a.title')?.textContent?.trim() || '';
        const body = document.querySelector('.expando .usertext-body .md')?.textContent?.trim() || '';
        const author = document.querySelector('.tagline .author')?.textContent?.trim() || '';

        // Get top-level comments (first 15)
        const commentEls = document.querySelectorAll('.comment .entry');
        const comments = [];
        let count = 0;
        for (const el of commentEls) {
            if (count >= 15) break;
            const authorEl = el.querySelector('.tagline .author');
            const bodyEl = el.querySelector('.usertext-body .md');
            const scoreEl = el.querySelector('.tagline .score');
            if (bodyEl) {
                comments.push({
                    author: authorEl?.textContent?.trim() || '[deleted]',
                    body: bodyEl.textContent.trim().slice(0, 500),
                    score: scoreEl?.textContent?.trim() || '0',
                });
                count++;
            }
        }
        return { title, body: body.slice(0, 2000), author, comments };
    }""")
    return data


def post_comment(page, comment_text, reply_to_idx=None):
    """Post a comment on the current old.reddit.com thread page.

    If reply_to_idx is given, replies to that comment (0-indexed).
    Otherwise posts a top-level comment.

    Returns the permalink of the posted comment, or None on failure.
    """
    if reply_to_idx is not None:
        # Click reply on a specific comment
        reply_links = page.query_selector_all('.comment .entry .flat-list a[onclick*="reply"]')
        if reply_to_idx < len(reply_links):
            reply_links[reply_to_idx].click()
            page.wait_for_timeout(1000)
            # Find the newly opened reply textarea within that comment
            textareas = page.query_selector_all('.comment .usertext-edit textarea')
            if textareas:
                textarea = textareas[-1]  # last opened one
            else:
                return None
        else:
            return None
    else:
        # Top-level comment box
        textarea = page.query_selector('.usertext-edit textarea[name="text"]')
        if not textarea:
            return None

    textarea.fill(comment_text)
    page.wait_for_timeout(500)

    # Click save/submit button
    if reply_to_idx is not None:
        save_buttons = page.query_selector_all('.comment .usertext-edit button[type="submit"]')
        if save_buttons:
            save_buttons[-1].click()
        else:
            return None
    else:
        save_btn = page.query_selector('.bottom-area button[type="submit"]')
        if save_btn:
            save_btn.click()
        else:
            return None

    page.wait_for_timeout(3000)

    # Try to capture the permalink of our comment
    our_username = load_config().get("accounts", {}).get("reddit", {}).get("username", "")
    permalink = page.evaluate("""(username) => {
        const comments = document.querySelectorAll('.comment .entry');
        for (const el of comments) {
            const author = el.querySelector('.tagline .author');
            if (author && author.textContent.trim() === username) {
                const permaLink = el.querySelector('.flat-list .bylink[href*="/comments/"]');
                if (permaLink) return permaLink.href;
            }
        }
        return null;
    }""", our_username)

    return permalink


# ---------------------------------------------------------------------------
# Claude API — minimal prompt for comment drafting
# ---------------------------------------------------------------------------

def draft_comment(thread_data, project_config, content_angle, top_report,
                  recent_comments, config):
    """Call Claude API with minimal context to draft a comment.

    Only sends: thread content, project angle, top performer examples,
    and recent comments for dedup. ~500-1500 tokens input.
    """
    import anthropic

    # Load API key from .env (already sourced by db module)
    dbmod.load_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)

    # Build a concise prompt — no SKILL.md, no full config
    recent_str = "\n".join(f"- {c[:150]}" for c in recent_comments[:5]) if recent_comments else "(none)"

    # Truncate top_report to just the top posts section (skip bottom posts)
    top_section = top_report
    if "### Bottom" in top_report:
        top_section = top_report[:top_report.index("### Bottom")].strip()
    # Further truncate if very long
    if len(top_section) > 2000:
        top_section = top_section[:2000] + "\n...(truncated)"

    thread_comments_str = ""
    for i, c in enumerate(thread_data.get("comments", [])[:10]):
        thread_comments_str += f"\n[{c.get('score', '?')} pts] u/{c['author']}: {c['body'][:300]}"

    prompt = f"""Draft a Reddit comment for this thread. Reply as a real person sharing genuine experience.

PROJECT: {project_config.get('name', '')} - {project_config.get('description', '')}
WEBSITE: {project_config.get('website', '')}
YOUR ANGLE: {content_angle}

THREAD:
Title: {thread_data['title']}
Author: u/{thread_data['author']}
Body: {thread_data['body'][:1000]}

TOP COMMENTS:{thread_comments_str}

YOUR RECENT COMMENTS (don't repeat these talking points):
{recent_str}

TOP PERFORMING PAST COMMENTS (learn from what worked):
{top_section}

RULES:
- 2-3 sentences, first person, casual tone
- NO em dashes. Use commas, periods, or regular dashes
- NO markdown formatting (no bold, headers, lists)
- NO product links in top-level comments
- Include specific details from your experience
- Sound like a real person texting, not a blog post
- If replying to a specific comment, reference what they said
- If this thread has no natural connection to your work, respond with just: SKIP

Return ONLY the comment text (or SKIP). No explanation."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def pick_reply_target(thread_data):
    """Pick the best comment to reply to, or None for top-level.

    Prefers high-score comments that are relevant and not too old.
    Returns (index, comment_dict) or (None, None) for top-level.
    """
    comments = thread_data.get("comments", [])
    if not comments:
        return None, None

    # Parse scores and pick highest-scored comment with substantive content
    scored = []
    for i, c in enumerate(comments):
        try:
            score = int(re.sub(r'[^\d-]', '', c.get("score", "0")) or "0")
        except ValueError:
            score = 0
        body_len = len(c.get("body", ""))
        if body_len > 30:  # skip very short comments
            scored.append((score, body_len, i, c))

    if not scored:
        return None, None

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # Return the top comment (highest upvoted with substance)
    _, _, idx, comment = scored[0]
    return idx, comment


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(max_posts=1, dry_run=False):
    config = load_config()
    content_angle = config.get("content_angle", "")
    our_account = config.get("accounts", {}).get("reddit", {}).get("username", "")

    # Phase 1: Python-only data gathering
    print("=== Phase 1: Data gathering (no tokens) ===")
    project_name, project_config = pick_project("reddit")
    print(f"Project: {project_name}")

    top_report = get_top_report("reddit", project_name)
    print(f"Top report: {len(top_report)} chars")

    thread_data = find_threads(project_name)
    candidates = thread_data.get("threads", [])
    print(f"Found {len(candidates)} candidate threads")

    if not candidates:
        print("No candidate threads found. Stopping.")
        return

    recent_comments = get_recent_comments(5)

    # Phase 2: Browser + Claude API loop
    print(f"\n=== Phase 2: Post loop (max {max_posts}) ===")

    if not dry_run:
        pw, browser, context, page = launch_browser()
    else:
        pw = browser = context = page = None

    posted = 0
    for i, candidate in enumerate(candidates):
        if posted >= max_posts:
            break

        url = candidate.get("url", "")
        print(f"\n--- Thread {i+1}: {candidate.get('title', '')[:80]} ---")
        print(f"    URL: {url}")

        # Extract thread content via Playwright
        if not dry_run:
            try:
                thread_content = extract_thread_content(page, url)
            except Exception as e:
                print(f"    ERROR extracting thread: {e}")
                continue
        else:
            # In dry-run, use data from find_threads
            thread_content = {
                "title": candidate.get("title", ""),
                "body": candidate.get("selftext", ""),
                "author": candidate.get("author", ""),
                "comments": [],
            }

        if not thread_content.get("title"):
            print("    Empty thread, skipping.")
            continue

        # Draft comment via Claude API (~500 tokens)
        print("    Drafting comment via Claude API...")
        comment = draft_comment(
            thread_content, project_config, content_angle,
            top_report, recent_comments, config,
        )

        if comment == "SKIP" or not comment:
            print("    Claude said SKIP — no natural angle.")
            continue

        # Check for em dashes (safety net)
        comment = comment.replace("—", "-").replace("–", "-")

        print(f"    Draft: {comment[:120]}...")

        if dry_run:
            print("    [DRY RUN] Would post this comment.")
            posted += 1
            continue

        # Pick reply target
        reply_idx, reply_comment = pick_reply_target(thread_content)
        if reply_idx is not None:
            print(f"    Replying to comment #{reply_idx} by u/{reply_comment['author']}")
        else:
            print("    Posting top-level comment")

        # Post via Playwright
        try:
            permalink = post_comment(page, comment, reply_to_idx=reply_idx)
        except Exception as e:
            print(f"    ERROR posting: {e}")
            continue

        if permalink:
            print(f"    Posted! Permalink: {permalink}")
        else:
            print("    Posted (couldn't capture permalink)")
            permalink = url  # fallback

        # Log to database
        log_post(
            platform="reddit",
            thread_url=url,
            thread_author=thread_content.get("author", candidate.get("author", "")),
            thread_title=thread_content.get("title", candidate.get("title", "")),
            thread_content=thread_content.get("body", ""),
            our_url=permalink or url,
            our_content=comment,
            our_account=our_account,
            project_name=project_name,
        )
        print("    Logged to database.")

        # Add to recent comments for dedup
        recent_comments.insert(0, comment)
        posted += 1

        # Brief pause between posts
        if posted < max_posts and i < len(candidates) - 1:
            page.wait_for_timeout(2000)

    # Cleanup
    if not dry_run and page:
        page.close()
        context.close()
        browser.close()
        pw.stop()

    print(f"\n=== Done: {posted} posts ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Programmatic Reddit poster")
    parser.add_argument("--max-posts", type=int, default=1, help="Max posts per run (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Draft comments but don't post")
    args = parser.parse_args()
    run(max_posts=args.max_posts, dry_run=args.dry_run)
