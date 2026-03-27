#!/usr/bin/env python3
"""Update LinkedIn engagement stats in the database.

LinkedIn doesn't expose a public API for post stats, so engagement data
is scraped from the browser by Claude using MCP Playwright (linkedin-agent).
The browser scraper navigates to the parent post, finds OUR comment within it,
and extracts the reaction count on our specific comment (not the parent post).
Results are saved to a JSON file, then this script reads that file and updates the DB.

Expected JSON format (list of objects):
  [
    {
      "url": "https://www.linkedin.com/feed/update/urn:li:activity:...",
      "reactions": 5,
      "found": true
    },
    ...
  ]

Usage:
    python3 scripts/scrape_linkedin_stats.py --from-json /tmp/linkedin_stats.json
    python3 scripts/scrape_linkedin_stats.py --from-json /tmp/linkedin_stats.json --json
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def normalize_linkedin_url(url):
    """Normalize LinkedIn post URL for matching.

    Extracts the URN type and ID so we can match regardless of trailing
    slashes, query params, or URL variations.
    Handles urn:li:activity, urn:li:ugcPost, and urn:li:share.
    """
    m = re.search(r"urn:li:(activity|ugcPost|share):(\d+)", url)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    return None


def update_linkedin_stats(db, scraped_data, quiet=False):
    """Match scraped LinkedIn data to DB posts and update.

    Stats are for OUR COMMENT's reactions (not the parent post).
    The scraper finds our comment within the parent post and extracts
    the reaction count on our specific comment.
    """
    # Build lookup by activity ID
    stats_by_activity = {}
    for item in scraped_data:
        url = item.get("url", "")
        activity_id = normalize_linkedin_url(url)
        if activity_id:
            stats_by_activity[activity_id] = item

    # Fetch all active LinkedIn posts from DB
    posts = db.execute(
        "SELECT id, our_url FROM posts "
        "WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL "
        "AND our_url LIKE '%%linkedin.com/%%' "
        "ORDER BY id"
    ).fetchall()

    matched = 0
    unmatched = 0

    for post in posts:
        db_id, our_url = post[0], post[1]
        activity_id = normalize_linkedin_url(our_url)
        if not activity_id:
            unmatched += 1
            continue

        if activity_id in stats_by_activity:
            item = stats_by_activity[activity_id]
            # Only update if the scraper actually found our comment
            if not item.get("found", False):
                unmatched += 1
                continue

            reactions = item.get("reactions", 0) or 0

            engagement = json.dumps({
                "comment_reactions": reactions,
            })

            db.execute(
                "UPDATE posts SET upvotes=%s, comments_count=NULL, views=NULL, "
                "thread_engagement=%s, engagement_updated_at=NOW(), "
                "status_checked_at=NOW() WHERE id=%s",
                [reactions, engagement, db_id],
            )
            matched += 1
            if not quiet:
                print(f"  [{db_id}] comment_reactions={reactions}")
        else:
            unmatched += 1

    db.commit()
    return {
        "matched": matched,
        "unmatched": unmatched,
        "scraped_total": len(scraped_data),
        "db_total": len(posts),
    }


def main():
    parser = argparse.ArgumentParser(description="Update LinkedIn stats from scraped JSON")
    parser.add_argument("--from-json", required=True, help="Path to JSON file with scraped stats")
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
    result = update_linkedin_stats(db, scraped_data, quiet=args.quiet)
    db.close()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"LinkedIn Stats: {result['scraped_total']} scraped, "
            f"{result['matched']} DB posts updated, "
            f"{result['unmatched']} unmatched"
        )


if __name__ == "__main__":
    main()
