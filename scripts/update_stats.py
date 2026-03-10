#!/usr/bin/env python3
"""Fetch engagement stats for Reddit + Moltbook posts via public APIs.

Updates upvotes, comments_count, thread_engagement, and status in the DB.
No browser needed.

Usage:
    python3 scripts/update_stats.py [--db PATH] [--quiet]
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def fetch_json(url, headers=None, user_agent="social-autoposter/1.0"):
    hdrs = {"User-Agent": user_agent}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None


def update_reddit(db, user_agent, quiet=False):
    posts = db.execute(
        "SELECT id, our_url, thread_url FROM posts "
        "WHERE platform='reddit' AND status='active' AND our_url IS NOT NULL ORDER BY id"
    ).fetchall()

    total = updated = deleted = removed = errors = 0
    results = []

    for post in posts:
        total += 1
        post_id, our_url = post[0], post[1]
        if not our_url or not our_url.startswith("http"):
            errors += 1
            continue
        json_url = re.sub(r"www\.reddit\.com", "old.reddit.com", our_url).rstrip("/") + ".json"

        response = fetch_json(json_url, user_agent=user_agent)
        if not response or not isinstance(response, list) or len(response) < 2:
            # Retry once
            time.sleep(5)
            response = fetch_json(json_url, user_agent=user_agent)
            if not response or not isinstance(response, list) or len(response) < 2:
                errors += 1
                continue

        children = response[1].get("data", {}).get("children", [])
        if not children:
            errors += 1
            continue
        comment_data = children[0].get("data")
        if not comment_data:
            errors += 1
            continue

        body = comment_data.get("body", "")
        author = comment_data.get("author", "")
        score = comment_data.get("score", 0)

        if body in ("[deleted]",) or author == "[deleted]":
            db.execute("UPDATE posts SET status='deleted', status_checked_at=NOW() WHERE id=%s", [post_id])
            deleted += 1
            if not quiet:
                print(f"DELETED [{post_id}]")
            continue

        if body == "[removed]":
            db.execute("UPDATE posts SET status='removed', status_checked_at=NOW() WHERE id=%s", [post_id])
            removed += 1
            if not quiet:
                print(f"REMOVED [{post_id}]")
            continue

        thread_score = response[0].get("data", {}).get("children", [{}])[0].get("data", {}).get("score", 0)
        thread_comments = response[0].get("data", {}).get("children", [{}])[0].get("data", {}).get("num_comments", 0)
        thread_title = response[0].get("data", {}).get("children", [{}])[0].get("data", {}).get("title", "")[:60]
        engagement = json.dumps({"thread_score": thread_score, "thread_comments": thread_comments})

        db.execute(
            "UPDATE posts SET upvotes=%s, comments_count=%s, thread_engagement=%s, "
            "engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s",
            [score, thread_comments, engagement, post_id],
        )
        updated += 1
        results.append({"id": post_id, "score": score, "thread_score": thread_score,
                        "thread_comments": thread_comments, "title": thread_title})
        time.sleep(5)

    db.commit()
    return {"total": total, "updated": updated, "deleted": deleted, "removed": removed,
            "errors": errors, "results": results}


def update_moltbook(db, api_key, quiet=False):
    if not api_key:
        return {"skipped": True, "reason": "no_api_key"}

    posts = db.execute(
        "SELECT id, our_url FROM posts WHERE platform='moltbook' AND status='active' AND our_url IS NOT NULL ORDER BY id"
    ).fetchall()

    total = updated = deleted = errors = 0
    results = []

    for post in posts:
        total += 1
        post_id, our_url = post[0], post[1]
        uuid_match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", our_url)
        if not uuid_match:
            errors += 1
            continue

        data = fetch_json(
            f"https://www.moltbook.com/api/v1/posts/{uuid_match.group()}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if not data or not data.get("success"):
            errors += 1
            continue

        post_data = data.get("post", {})
        if post_data.get("is_deleted"):
            db.execute("UPDATE posts SET status='deleted', status_checked_at=NOW() WHERE id=%s", [post_id])
            deleted += 1
            continue

        upvotes = post_data.get("upvotes", 0)
        comment_count = post_data.get("comment_count", 0)
        score = post_data.get("score", 0)
        title = post_data.get("title", "")[:60]
        engagement = json.dumps({"score": score, "upvotes": upvotes, "comment_count": comment_count})

        db.execute(
            "UPDATE posts SET upvotes=%s, comments_count=%s, thread_engagement=%s, "
            "engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s",
            [upvotes, comment_count, engagement, post_id],
        )
        updated += 1
        results.append({"id": post_id, "upvotes": upvotes, "score": score,
                        "comments": comment_count, "title": title})

    db.commit()
    return {"total": total, "updated": updated, "deleted": deleted, "errors": errors, "results": results}


def main():
    parser = argparse.ArgumentParser(description="Update engagement stats for social posts")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    config = load_config()
    reddit_username = config.get("accounts", {}).get("reddit", {}).get("username", "")
    user_agent = f"social-autoposter/1.0 (u/{reddit_username})" if reddit_username else "social-autoposter/1.0"

    dbmod.load_env()
    db = dbmod.get_conn()

    reddit_stats = update_reddit(db, user_agent, quiet=args.quiet)
    moltbook_stats = update_moltbook(db, os.environ.get("MOLTBOOK_API_KEY", ""), quiet=args.quiet)

    db.close()

    output = {"reddit": reddit_stats, "moltbook": moltbook_stats}

    if args.json:
        print(json.dumps(output, indent=2))
    else:
        r = reddit_stats
        print(f"\nReddit: {r['total']} checked, {r['updated']} updated, "
              f"{r['deleted']} deleted, {r['removed']} removed, {r['errors']} errors")
        if not args.quiet and r["results"]:
            print(f"{'ID':>4} {'Score':>5} {'Thread':>7} {'Comments':>8}  Title")
            for row in sorted(r["results"], key=lambda x: x["score"], reverse=True):
                print(f"{row['id']:>4} {row['score']:>5} {row['thread_score']:>7} "
                      f"{row['thread_comments']:>8}  {row['title']}")

        if not moltbook_stats.get("skipped"):
            m = moltbook_stats
            print(f"\nMoltbook: {m['total']} checked, {m['updated']} updated, "
                  f"{m['deleted']} deleted, {m['errors']} errors")


if __name__ == "__main__":
    main()
