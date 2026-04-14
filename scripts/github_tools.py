#!/usr/bin/env python3
"""GitHub CLI tools for Claude to call via Bash.

Commands:
    python3 scripts/github_tools.py search "QUERY" [--limit 10]
    python3 scripts/github_tools.py view OWNER/REPO NUMBER
    python3 scripts/github_tools.py already-posted "THREAD_URL"
    python3 scripts/github_tools.py log-post THREAD_URL OUR_URL OUR_TEXT PROJECT THREAD_AUTHOR THREAD_TITLE [--account m13v] [--engagement-style STYLE]
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")

THREAD_CHAR_CAP = 12000


def _load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _excluded_repos_and_authors(config):
    exclusions = config.get("exclusions", {})
    repos = {r.lower() for r in exclusions.get("github_repos", [])}
    authors = {a.lower() for a in exclusions.get("authors", [])}
    return repos, authors


def _is_excluded_repo(repo_full, excluded_repos):
    """repo_full is 'owner/name'. Match if either owner or name or full is in excluded list."""
    if not repo_full:
        return False
    rl = repo_full.lower()
    owner = rl.split("/", 1)[0] if "/" in rl else rl
    name = rl.split("/", 1)[1] if "/" in rl else rl
    return rl in excluded_repos or owner in excluded_repos or name in excluded_repos


def cmd_search(args):
    """Search GitHub for issues via gh CLI. Filters out excluded repos/authors and already-posted threads."""
    try:
        out = subprocess.check_output(
            ["gh", "search", "issues", args.query,
             "--limit", str(args.limit),
             "--state", "open",
             "--sort", "updated",
             "--json", "number,title,repository,author,state,updatedAt,url,body"],
            text=True, timeout=30, stderr=subprocess.STDOUT,
        )
        items = json.loads(out)
    except subprocess.CalledProcessError as e:
        print(json.dumps({"error": "gh_search_failed", "message": (e.output or str(e))[:300]}))
        sys.exit(2)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(json.dumps({"error": "gh_search_failed", "message": str(e)[:300]}))
        sys.exit(2)

    config = _load_config()
    excluded_repos, excluded_authors = _excluded_repos_and_authors(config)

    dbmod.load_env()
    conn = dbmod.get_conn()
    cur = conn.execute(
        "SELECT thread_url FROM posts WHERE platform='github' AND thread_url IS NOT NULL"
    )
    already_posted = {row[0] for row in cur.fetchall()}
    conn.close()

    results = []
    for item in items:
        repo = item.get("repository", {}) or {}
        repo_full = repo.get("nameWithOwner") or (
            f"{repo.get('owner', {}).get('login', '')}/{repo.get('name', '')}"
            if repo.get("owner") else ""
        )
        author = (item.get("author") or {}).get("login", "")

        if _is_excluded_repo(repo_full, excluded_repos):
            continue
        if author.lower() in excluded_authors:
            continue

        url = item.get("url", "")
        already = url in already_posted
        entry = {
            "url": url,
            "title": item.get("title", ""),
            "author": author,
            "repo": repo_full,
            "number": item.get("number"),
            "updated_at": item.get("updatedAt", ""),
            "body_preview": (item.get("body") or "")[:400],
            "already_posted": already,
        }
        if already:
            entry["SKIP"] = ">>> ALREADY POSTED IN THIS THREAD - DO NOT POST AGAIN <<<"
        results.append(entry)

    print(json.dumps(results, indent=2))


def cmd_view(args):
    """Fetch issue body and comments via gh CLI. Returns compact JSON."""
    # args.repo is 'owner/repo', args.number is the issue number
    try:
        out = subprocess.check_output(
            ["gh", "issue", "view", str(args.number), "-R", args.repo,
             "--json", "title,body,author,state,comments,url"],
            text=True, timeout=30, stderr=subprocess.STDOUT,
        )
        thread = json.loads(out)
    except subprocess.CalledProcessError as e:
        print(json.dumps({"error": "gh_view_failed", "message": (e.output or str(e))[:300]}))
        return
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(json.dumps({"error": "gh_view_failed", "message": str(e)[:300]}))
        return

    comments = []
    for c in (thread.get("comments") or [])[:20]:
        comments.append({
            "author": (c.get("author") or {}).get("login", ""),
            "body": (c.get("body") or "")[:1500],
        })

    compact = {
        "url": thread.get("url", ""),
        "title": thread.get("title", ""),
        "state": thread.get("state", ""),
        "author": (thread.get("author") or {}).get("login", ""),
        "body": (thread.get("body") or "")[:4000],
        "comments": comments,
    }

    text = json.dumps(compact, indent=2)
    if len(text) > THREAD_CHAR_CAP:
        text = text[:THREAD_CHAR_CAP] + f"\n... [truncated {len(text) - THREAD_CHAR_CAP} chars]"
    print(text)


def cmd_already_posted(args):
    """Check if we already posted in a GitHub issue thread."""
    dbmod.load_env()
    conn = dbmod.get_conn()
    cur = conn.execute(
        "SELECT id, LEFT(our_content, 100) FROM posts WHERE platform='github' AND thread_url = %s LIMIT 1",
        [args.url],
    )
    row = cur.fetchone()
    conn.close()
    if row:
        print(json.dumps({"already_posted": True, "post_id": row[0], "content_preview": row[1]}))
    else:
        print(json.dumps({"already_posted": False}))


def cmd_log_post(args):
    """Log a posted GitHub comment to the database.

    Enforces two dedup rules:
      1. Same comment URL is never logged twice (our_url hard dedup).
      2. Only one post per GitHub issue thread (thread_url hard dedup).
    """
    dbmod.load_env()
    conn = dbmod.get_conn()

    if args.our_url:
        cur = conn.execute(
            "SELECT id FROM posts WHERE platform='github' AND our_url = %s LIMIT 1",
            [args.our_url],
        )
        existing = cur.fetchone()
        if existing:
            conn.close()
            print(json.dumps({"error": "DUPLICATE_URL", "message": "Already logged this comment URL", "existing_post_id": existing[0]}))
            return

    cur = conn.execute(
        "SELECT id, LEFT(our_content, 100) FROM posts WHERE platform='github' AND thread_url = %s LIMIT 1",
        [args.thread_url],
    )
    existing = cur.fetchone()
    if existing:
        conn.close()
        print(json.dumps({
            "error": "DUPLICATE_THREAD",
            "message": "Already posted in this thread",
            "existing_post_id": existing[0],
            "content_preview": existing[1],
        }))
        return

    conn.execute(
        """INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
           thread_title, thread_content, our_url, our_content, our_account,
           source_summary, project_name, status, posted_at, feedback_report_used, engagement_style)
           VALUES ('github', %s, %s, %s, %s, '', %s, %s, %s, '', %s, 'active', NOW(), TRUE, %s)""",
        [args.thread_url, args.thread_author, args.thread_author, args.thread_title,
         args.our_url, args.our_text, args.account, args.project,
         getattr(args, "engagement_style", None)],
    )
    conn.commit()
    conn.close()
    print(json.dumps({"logged": True}))


def main():
    parser = argparse.ArgumentParser(description="GitHub tools for Claude")
    sub = parser.add_subparsers(dest="command")

    # search
    p_search = sub.add_parser("search", help="Search GitHub issues")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)

    # view
    p_view = sub.add_parser("view", help="Fetch issue body + comments")
    p_view.add_argument("repo", help="owner/repo")
    p_view.add_argument("number", help="Issue number")

    # already-posted
    p_ap = sub.add_parser("already-posted", help="Check if we posted in this thread")
    p_ap.add_argument("url")

    # log-post
    p_log = sub.add_parser("log-post", help="Log a posted comment to DB")
    p_log.add_argument("thread_url")
    p_log.add_argument("our_url")
    p_log.add_argument("our_text")
    p_log.add_argument("project")
    p_log.add_argument("thread_author")
    p_log.add_argument("thread_title")
    p_log.add_argument("--account", default="m13v")
    p_log.add_argument("--engagement-style", dest="engagement_style", default=None)

    args = parser.parse_args()
    if args.command == "search":
        cmd_search(args)
    elif args.command == "view":
        cmd_view(args)
    elif args.command == "already-posted":
        cmd_already_posted(args)
    elif args.command == "log-post":
        cmd_log_post(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
