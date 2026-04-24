#!/usr/bin/env python3
"""Return top-performing search_topic seeds per project + platform.

Reads `posts.search_topic` (populated by post_github.py and post_reddit.py
starting 2026-04-24, Phase 2). Ranks by composite engagement score, falling
back to raw post count when no posts yet have engagement data. Used as
feedback context in the GitHub and Reddit prompts so the LLM favors seeds
that actually lead to engagement.

Twitter has a separate, earlier feedback loop via `twitter_candidates` —
see `scripts/top_twitter_queries.py`. This tool is the analog for the
`posts` table (Reddit + GitHub + any other platform that logs search_topic).

Usage:
    python3 scripts/top_search_topics.py --project "fazm" --platform github
    python3 scripts/top_search_topics.py --platform reddit --window-days 30
    python3 scripts/top_search_topics.py --project "fazm" --json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

SCORE_SQL = (
    "(COALESCE(comments_count,0) * 3 + "
    "CASE WHEN LOWER(platform) IN ('reddit', 'moltbook') "
    "THEN GREATEST(0, COALESCE(upvotes,0) - 1) "
    "ELSE COALESCE(upvotes,0) END)"
)


def query(project=None, platform=None, window_days=30, limit=10):
    dbmod.load_env()
    conn = dbmod.get_conn()
    filters = [
        "search_topic IS NOT NULL",
        "search_topic <> ''",
        f"posted_at > NOW() - INTERVAL '{int(window_days)} days'",
    ]
    params = []
    if project:
        filters.append("LOWER(project_name) = LOWER(%s)")
        params.append(project)
    if platform:
        filters.append("LOWER(platform) = LOWER(%s)")
        params.append(platform)
    where = " AND ".join(filters)
    sql = (
        f"SELECT search_topic, "
        f"       COUNT(*) AS posts, "
        f"       SUM({SCORE_SQL}) AS total_score, "
        f"       AVG({SCORE_SQL})::numeric(10,2) AS avg_score, "
        f"       MAX(posted_at) AS last_used "
        f"FROM posts "
        f"WHERE {where} "
        f"GROUP BY search_topic "
        f"ORDER BY total_score DESC NULLS LAST, posts DESC, last_used DESC "
        f"LIMIT %s"
    )
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        {
            "search_topic": r[0],
            "posts": int(r[1]),
            "total_score": int(r[2] or 0),
            "avg_score": float(r[3] or 0),
            "last_used": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]


def format_text(results, project=None, platform=None, window_days=30):
    if not results:
        return (
            f"(no search_topic data yet in the last {window_days}d"
            + (f" for {project}" if project else "")
            + (f" on {platform}" if platform else "")
            + ")"
        )
    header = f"Top search_topic seeds (last {window_days}d"
    if project:
        header += f", project={project}"
    if platform:
        header += f", platform={platform}"
    header += ", sorted by total engagement score)"
    lines = [header]
    lines.append(f"  {'posts':>5} {'total':>6} {'avg':>6}  topic")
    for r in results:
        lines.append(
            f"  {r['posts']:>5} {r['total_score']:>6} {r['avg_score']:>6.2f}  {r['search_topic']}"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None)
    ap.add_argument("--platform", default=None)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = ap.parse_args()

    results = query(args.project, args.platform, args.window_days, args.limit)
    if args.json:
        json.dump(results, sys.stdout)
        sys.stdout.write("\n")
    else:
        print(format_text(results, args.project, args.platform, args.window_days))


if __name__ == "__main__":
    main()
