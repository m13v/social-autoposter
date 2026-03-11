#!/usr/bin/env python3
"""Update Reddit view counts in the database.

Reddit doesn't expose view counts via API. Views are scraped from the
profile page by Claude using MCP Playwright, then saved to a JSON file.
This script reads that JSON and updates the `views` column in the DB.

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
    views_by_post = {}
    for url, views in items:
        if views is None:
            continue
        post_id, comment_id = extract_ids(url)
        if comment_id:
            views_by_comment[comment_id] = views
        elif post_id:
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

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"Reddit Views: {result['with_views']} had views, "
            f"{result['matched']} DB posts updated, "
            f"{result['unmatched']} unmatched"
        )


if __name__ == "__main__":
    main()
