#!/usr/bin/env python3
"""Update Reddit view counts in the database.

Reddit doesn't expose view counts via API. Views are scraped from the
profile page by Claude using MCP Playwright, then saved to a JSON file.
This script reads that JSON and updates the `views` column in the DB.

IMPORTANT — Browser scraping notes for Claude:
  Reddit virtualizes the DOM: items scrolled off-screen get removed.
  You MUST collect view data incrementally as you scroll — NOT after
  scrolling to the bottom. Use this pattern:
    1. Collect visible articles + view counts
    2. Scroll down ~600px
    3. Wait 800-1500ms for new content
    4. Collect again (dedup by URL in a Map/dict)
    5. Repeat until no new articles load (check article count, not scroll height)
  View counts appear as text nodes matching /^\d[\d,.]*[KkMm]?\s*views?$/
  inside <article> elements. Parse "1.3K views" -> 1300, "2 views" -> 2.

Usage:
    python3 scripts/scrape_reddit_views.py --from-json /tmp/reddit_views.json
    python3 scripts/scrape_reddit_views.py --from-json /tmp/reddit_views.json --json
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def extract_ids(url):
    """Extract (post_id, comment_id) from any reddit URL format."""
    url = re.sub(r"https?://(old|www|new)\.reddit\.com", "", url)
    url = re.sub(r"\?.*$", "", url).rstrip("/")

    # New format: /r/sub/comments/POST_ID/comment/COMMENT_ID
    m = re.search(r"/comments/([a-z0-9]+)/comment/([a-z0-9]+)", url)
    if m:
        return (m.group(1), m.group(2))

    # Old format: /r/sub/comments/POST_ID/slug/COMMENT_ID
    m = re.search(r"/comments/([a-z0-9]+)/[^/]+/([a-z0-9]+)", url)
    if m:
        return (m.group(1), m.group(2))

    # Post only: /r/sub/comments/POST_ID/...
    m = re.search(r"/comments/([a-z0-9]+)", url)
    if m:
        return (m.group(1), None)

    return (None, None)


def update_views(db, scraped_data, quiet=False):
    """Match scraped view data to DB posts and update."""
    # scraped_data is a list of {url, views} or a dict of {url: views}
    if isinstance(scraped_data, dict):
        items = scraped_data.items()
    else:
        items = [(item["url"], item["views"]) for item in scraped_data]

    # Build lookups by comment_id and post_id
    views_by_comment = {}
    views_by_post = {}  # post_id -> max views (fallback for thread-URL-only DB entries)
    for url, views in items:
        if views is None:
            continue
        post_id, comment_id = extract_ids(url)
        if comment_id:
            views_by_comment[comment_id] = views
        # Always track max views per post_id so DB entries with only a thread URL
        # (no comment_id) can still match via post_id fallback
        if post_id:
            if post_id not in views_by_post or views > views_by_post[post_id]:
                views_by_post[post_id] = views

    posts = db.execute(
        "SELECT id, our_url FROM posts "
        "WHERE platform='reddit' AND status='active' AND our_url IS NOT NULL"
    ).fetchall()

    matched = 0
    unmatched = 0

    for post in posts:
        db_id, our_url = post[0], post[1]
        post_id, comment_id = extract_ids(our_url)

        views = None
        if comment_id and comment_id in views_by_comment:
            views = views_by_comment[comment_id]
        elif post_id and post_id in views_by_post:
            views = views_by_post[post_id]

        if views is not None:
            db.execute(
                "UPDATE posts SET views=%s, engagement_updated_at=NOW() WHERE id=%s",
                [views, db_id],
            )
            matched += 1
        else:
            unmatched += 1

    db.commit()
    return {
        "matched": matched,
        "unmatched": unmatched,
        "scraped_total": len(list(items)) if not isinstance(scraped_data, dict) else len(scraped_data),
        "with_views": len(views_by_comment) + len(views_by_post),
    }


def main():
    parser = argparse.ArgumentParser(description="Update Reddit view counts from scraped JSON")
    parser.add_argument("--from-json", required=True, help="Path to JSON file with scraped views")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if not os.path.exists(args.from_json):
        print(f"ERROR: File not found: {args.from_json}", file=sys.stderr)
        sys.exit(1)

    with open(args.from_json) as f:
        scraped_data = json.load(f)

    if not args.quiet:
        print(f"Loaded {len(scraped_data)} items from {args.from_json}")

    dbmod.load_env()
    db = dbmod.get_conn()
    result = update_views(db, scraped_data, quiet=args.quiet)
    db.close()

    # Get aggregate totals
    from datetime import datetime
    db = dbmod.get_conn()
    row = db.execute(
        "SELECT SUM(views), SUM(upvotes), SUM(comments_count), COUNT(*), MIN(posted_at) "
        "FROM posts WHERE status='active' AND platform NOT IN ('github_issues', 'moltbook')"
    ).fetchone()
    total_views = row[0] or 0
    total_upvotes = row[1] or 0
    total_comments = row[2] or 0
    total_posts = row[3] or 0
    first_post = row[4]
    days = max((datetime.now(first_post.tzinfo) if first_post and first_post.tzinfo else datetime.now()).day, 1)
    if first_post:
        now = datetime.now(first_post.tzinfo) if first_post.tzinfo else datetime.now()
        days = max((now - first_post).days, 1)
    db.close()

    result["totals"] = {
        "total_views": total_views, "total_upvotes": total_upvotes,
        "total_comments": total_comments, "total_posts": total_posts,
        "days_active": days, "views_per_day": round(total_views / days) if days else 0,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"Reddit Views: {result['with_views']} had views, "
            f"{result['matched']} DB posts updated, "
            f"{result['unmatched']} unmatched"
        )
        t = result["totals"]
        print(f"\n--- Totals ({t['days_active']} days) ---")
        print(f"Posts: {t['total_posts']}  |  "
              f"Views: {t['total_views']:,}  |  "
              f"Upvotes: {t['total_upvotes']:,}  |  "
              f"Comments: {t['total_comments']:,}  |  "
              f"Views/day: {t['views_per_day']:,}")


if __name__ == "__main__":
    main()
