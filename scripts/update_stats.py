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
from datetime import datetime, timedelta, timezone

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


def update_reddit(db, user_agent, config=None, quiet=False):
    config = config or {}
    posts = db.execute(
        "SELECT id, our_url, thread_url, upvotes, comments_count, "
        "COALESCE(scan_no_change_count, 0) as scan_no_change_count, posted_at "
        "FROM posts "
        "WHERE platform='reddit' AND status='active' AND our_url IS NOT NULL ORDER BY id"
    ).fetchall()

    total = updated = deleted = removed = errors = skipped = 0
    results = []

    for post in posts:
        total += 1
        post_id, our_url, thread_url = post[0], post[1], post[2]
        prev_upvotes, prev_comments = post[3], post[4]
        no_change = post[5]
        posted_at = post[6]

        # Skip stable posts: 2+ scans with no change AND older than 3 days
        if no_change >= 2 and posted_at:
            age = datetime.now(timezone.utc) - (posted_at.replace(tzinfo=timezone.utc) if posted_at.tzinfo is None else posted_at)
            if age > timedelta(days=3):
                skipped += 1
                continue

        if not our_url or not our_url.startswith("http"):
            errors += 1
            continue

        # Detect if our_url points to a specific comment or just the thread
        has_comment_id = bool(
            re.search(r"/comment/[a-z0-9]+", our_url) or
            re.search(r"/comments/[a-z0-9]+/[^/]+/[a-z0-9]+", our_url)
        )

        json_url = re.sub(r"www\.reddit\.com", "old.reddit.com", our_url).rstrip("/") + ".json"

        response = fetch_json(json_url, user_agent=user_agent)
        if not response or not isinstance(response, list) or len(response) < 2:
            # Retry once
            time.sleep(5)
            response = fetch_json(json_url, user_agent=user_agent)
            if not response or not isinstance(response, list) or len(response) < 2:
                errors += 1
                continue

        thread_data = response[0].get("data", {}).get("children", [{}])[0].get("data", {})
        thread_score = thread_data.get("score", 0)
        thread_comments = thread_data.get("num_comments", 0)
        thread_title = thread_data.get("title", "")[:60]
        thread_author = thread_data.get("author", "")

        if has_comment_id:
            # our_url has a comment permalink — response[1] contains the specific comment
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

            engagement = json.dumps({"thread_score": thread_score, "thread_comments": thread_comments})
            db.execute(
                "UPDATE posts SET upvotes=%s, comments_count=%s, thread_engagement=%s, "
                "engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s",
                [score, thread_comments, engagement, post_id],
            )
            updated += 1
            results.append({"id": post_id, "score": score, "thread_score": thread_score,
                            "thread_comments": thread_comments, "title": thread_title})
        else:
            # our_url is a thread URL without a comment ID
            # Check if it's our original post (we are the thread author)
            is_our_post = thread_author.lower() == config.get("accounts", {}).get("reddit", {}).get("username", "").lower()

            if is_our_post:
                # Original post — use thread-level stats (they ARE our stats)
                if thread_data.get("removed_by_category"):
                    db.execute("UPDATE posts SET status='removed', status_checked_at=NOW() WHERE id=%s", [post_id])
                    removed += 1
                    if not quiet:
                        print(f"REMOVED (thread) [{post_id}]")
                    continue

                engagement = json.dumps({"thread_score": thread_score, "thread_comments": thread_comments})
                db.execute(
                    "UPDATE posts SET upvotes=%s, comments_count=%s, thread_engagement=%s, "
                    "engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s",
                    [thread_score, thread_comments, engagement, post_id],
                )
                updated += 1
                results.append({"id": post_id, "score": thread_score, "thread_score": thread_score,
                                "thread_comments": thread_comments, "title": thread_title})
            else:
                # Comment without permalink — we can't get comment-specific stats
                # Only update thread engagement metadata, don't touch upvotes/comments_count
                # Check if our comment is still visible by searching response[1]
                our_found = False
                our_removed = False
                our_username = config.get("accounts", {}).get("reddit", {}).get("username", "")
                children = response[1].get("data", {}).get("children", [])
                for child in children:
                    cd = child.get("data", {})
                    if cd.get("author", "").lower() == our_username.lower():
                        our_found = True
                        if cd.get("body") == "[removed]":
                            our_removed = True
                        elif cd.get("body") in ("[deleted]",) or cd.get("author") == "[deleted]":
                            our_removed = True
                        else:
                            # Found our comment with stats — update
                            score = cd.get("score", 0)
                            engagement = json.dumps({"thread_score": thread_score, "thread_comments": thread_comments})
                            db.execute(
                                "UPDATE posts SET upvotes=%s, thread_engagement=%s, "
                                "engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s",
                                [score, engagement, post_id],
                            )
                            updated += 1
                            results.append({"id": post_id, "score": score, "thread_score": thread_score,
                                            "thread_comments": thread_comments, "title": thread_title})
                        break

                if our_removed:
                    db.execute("UPDATE posts SET status='removed', status_checked_at=NOW() WHERE id=%s", [post_id])
                    removed += 1
                    if not quiet:
                        print(f"REMOVED (no permalink) [{post_id}]")
                elif not our_found:
                    # Comment not in top-level replies — just update checked timestamp
                    engagement = json.dumps({"thread_score": thread_score, "thread_comments": thread_comments})
                    db.execute(
                        "UPDATE posts SET thread_engagement=%s, status_checked_at=NOW() WHERE id=%s",
                        [engagement, post_id],
                    )
                    if not quiet:
                        print(f"SKIP (no permalink, comment not in top-level) [{post_id}]")

        # Track whether stats changed for skip optimization
        # Compare current score to previous — if same, increment no-change counter
        if results and results[-1]["id"] == post_id:
            new_score = results[-1]["score"]
            if new_score == prev_upvotes:
                db.execute("UPDATE posts SET scan_no_change_count = COALESCE(scan_no_change_count, 0) + 1 WHERE id = %s", [post_id])
            else:
                db.execute("UPDATE posts SET scan_no_change_count = 0 WHERE id = %s", [post_id])

        time.sleep(5)

    db.commit()
    if skipped and not quiet:
        print(f"  Skipped {skipped} stable posts (2+ scans unchanged, older than 3 days)")
    return {"total": total, "updated": updated, "deleted": deleted, "removed": removed,
            "errors": errors, "skipped": skipped, "results": results}


def update_moltbook(db, api_key, quiet=False):
    if not api_key:
        return {"skipped": True, "reason": "no_api_key"}

    posts = db.execute(
        "SELECT id, our_url, thread_url FROM posts WHERE platform='moltbook' AND status='active' AND our_url IS NOT NULL ORDER BY id"
    ).fetchall()

    total = updated = deleted = errors = 0
    results = []
    headers = {"Authorization": f"Bearer {api_key}"}

    for post in posts:
        total += 1
        post_id, our_url, thread_url = post[0], post[1], post[2]

        # Extract post UUID and optional comment UUID from our_url
        # Format: https://www.moltbook.com/post/{post_uuid}#{comment_uuid}
        uuids = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", our_url)
        if not uuids:
            errors += 1
            continue

        post_uuid = uuids[0]
        comment_uuid = None
        if "#" in our_url and len(uuids) >= 2:
            comment_uuid = uuids[1]
        elif "#" in our_url:
            # Comment UUID might be short (not full UUID) - extract after #
            comment_uuid = our_url.split("#")[-1] if our_url.split("#")[-1] != post_uuid else None

        is_comment = comment_uuid is not None
        is_our_post = our_url == thread_url  # Original post if our_url matches thread_url

        if is_comment:
            # Fetch comment-specific stats via comments endpoint
            data = fetch_json(
                f"https://www.moltbook.com/api/v1/posts/{post_uuid}/comments?sort=new&limit=100",
                headers=headers,
            )
            if not data or not data.get("success"):
                errors += 1
                continue

            # Find our comment by UUID
            our_comment = None
            for c in data.get("comments", []):
                if c.get("id", "").startswith(comment_uuid[:8]):
                    our_comment = c
                    break

            if not our_comment:
                # Comment might not be in top 100 - just mark as checked
                db.execute(
                    "UPDATE posts SET status_checked_at=NOW() WHERE id=%s",
                    [post_id],
                )
                errors += 1
                continue

            if our_comment.get("is_deleted"):
                db.execute("UPDATE posts SET status='deleted', status_checked_at=NOW() WHERE id=%s", [post_id])
                deleted += 1
                continue

            # Comment-specific engagement
            comment_upvotes = our_comment.get("upvotes", 0)
            comment_score = our_comment.get("score", 0)
            comment_replies = our_comment.get("reply_count", len(our_comment.get("replies", [])))
            verification = our_comment.get("verification_status", "unknown")
            thread_comment_count = data.get("count", 0)

            engagement = json.dumps({
                "comment_upvotes": comment_upvotes,
                "comment_score": comment_score,
                "comment_replies": comment_replies,
                "verification": verification,
                "thread_comments": thread_comment_count,
            })

            db.execute(
                "UPDATE posts SET upvotes=%s, comments_count=%s, thread_engagement=%s, "
                "engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s",
                [comment_upvotes, comment_replies, engagement, post_id],
            )
            updated += 1
            results.append({"id": post_id, "upvotes": comment_upvotes,
                            "replies": comment_replies, "verification": verification})
        else:
            # Original post - fetch post-level stats
            data = fetch_json(
                f"https://www.moltbook.com/api/v1/posts/{post_uuid}",
                headers=headers,
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
            comment_count = post_data.get("comment_count", post_data.get("comments_count", 0))
            score = post_data.get("score", 0)
            views = post_data.get("views", 0)
            engagement = json.dumps({"score": score, "upvotes": upvotes, "comment_count": comment_count, "views": views})

            db.execute(
                "UPDATE posts SET upvotes=%s, comments_count=%s, views=%s, thread_engagement=%s, "
                "engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s",
                [upvotes, comment_count, views, engagement, post_id],
            )
            updated += 1
            results.append({"id": post_id, "upvotes": upvotes, "score": score,
                            "comments": comment_count})

    db.commit()
    return {"total": total, "updated": updated, "deleted": deleted, "errors": errors, "results": results}


def get_aggregate_totals(db):
    """Get aggregate stats across all platforms."""
    from datetime import datetime, timezone

    row = db.execute(
        "SELECT SUM(views), SUM(upvotes), SUM(comments_count), COUNT(*), MIN(posted_at) "
        "FROM posts WHERE status='active' AND platform NOT IN ('github_issues', 'moltbook')"
    ).fetchone()

    total_views = row[0] or 0
    total_upvotes = row[1] or 0
    total_comments = row[2] or 0
    total_posts = row[3] or 0
    first_post = row[4]

    days = 0
    if first_post:
        now = datetime.now(first_post.tzinfo) if first_post.tzinfo else datetime.now()
        days = max((now - first_post).days, 1)

    return {
        "total_views": total_views,
        "total_upvotes": total_upvotes,
        "total_comments": total_comments,
        "total_posts": total_posts,
        "days_active": days,
        "views_per_day": round(total_views / days) if days else 0,
        "first_post": str(first_post) if first_post else None,
    }


def print_aggregate_totals(totals):
    """Print a summary line with aggregate totals."""
    print(f"\n--- Totals ({totals['days_active']} days) ---")
    print(f"Posts: {totals['total_posts']}  |  "
          f"Views: {totals['total_views']:,}  |  "
          f"Upvotes: {totals['total_upvotes']:,}  |  "
          f"Comments: {totals['total_comments']:,}  |  "
          f"Views/day: {totals['views_per_day']:,}")


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

    reddit_stats = update_reddit(db, user_agent, config=config, quiet=args.quiet)
    moltbook_stats = update_moltbook(db, os.environ.get("MOLTBOOK_API_KEY", ""), quiet=args.quiet)

    # Gather aggregate totals across all platforms
    totals = get_aggregate_totals(db)

    db.close()

    output = {"reddit": reddit_stats, "moltbook": moltbook_stats, "totals": totals}

    if args.json:
        print(json.dumps(output, indent=2))
    else:
        r = reddit_stats
        print(f"\nReddit: {r['total']} total, {r.get('skipped', 0)} skipped, "
              f"{r['total'] - r.get('skipped', 0)} checked, {r['updated']} updated, "
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

        print_aggregate_totals(totals)


if __name__ == "__main__":
    main()
