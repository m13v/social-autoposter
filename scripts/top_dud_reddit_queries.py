#!/usr/bin/env python3
"""
top_dud_reddit_queries.py

Returns recent Reddit search queries that produced ZERO post-filter
candidates so post_reddit.py:build_prompt can tell the LLM scanner
"do not redraft these phrasings — they were flat in the last N hours".
Counterpart to top_search_topics.py (positive signal): this is the
negative-signal feed.

    python3 scripts/top_dud_reddit_queries.py [--project NAME] [--limit 30] [--window-hours 168]

Output: JSON list of
  {"query": ..., "subreddits": ..., "project": ..., "attempts": N, "last_ran_h_ago": F}
sorted by most-attempted dud first (so the most-wasteful repeats surface
at the top of the prompt anti-list).

Source: reddit_search_attempts (one row per (query, subreddits, project)
per cmd_search call, written by reddit_tools.py:cmd_search).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project", default=None,
                   help="Filter to a single project (matches project_name).")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--window-hours", type=int, default=168,
                   help="Look back this many hours for dud queries (default 7d).")
    args = p.parse_args()

    conn = dbmod.get_conn()
    if args.project:
        rows = conn.execute(
            """
            SELECT query,
                   COALESCE(subreddits, '')   AS subreddits,
                   COALESCE(project_name, '') AS project,
                   COUNT(*) AS attempts,
                   EXTRACT(EPOCH FROM (NOW() - MAX(ran_at)))/3600.0 AS last_ran_h_ago
            FROM reddit_search_attempts
            WHERE candidates_post_filter = 0
              AND ran_at > NOW() - (%s || ' hours')::interval
              AND project_name = %s
            GROUP BY query, COALESCE(subreddits, ''), COALESCE(project_name, '')
            ORDER BY attempts DESC, MAX(ran_at) DESC
            LIMIT %s
            """,
            [str(args.window_hours), args.project, args.limit],
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT query,
                   COALESCE(subreddits, '')   AS subreddits,
                   COALESCE(project_name, '') AS project,
                   COUNT(*) AS attempts,
                   EXTRACT(EPOCH FROM (NOW() - MAX(ran_at)))/3600.0 AS last_ran_h_ago
            FROM reddit_search_attempts
            WHERE candidates_post_filter = 0
              AND ran_at > NOW() - (%s || ' hours')::interval
            GROUP BY query, COALESCE(subreddits, ''), COALESCE(project_name, '')
            ORDER BY attempts DESC, MAX(ran_at) DESC
            LIMIT %s
            """,
            [str(args.window_hours), args.limit],
        ).fetchall()
    conn.close()

    out = [
        {
            "query": r[0],
            "subreddits": r[1] or None,
            "project": r[2],
            "attempts": int(r[3]),
            "last_ran_h_ago": round(float(r[4] or 0), 1),
        }
        for r in rows
    ]
    json.dump(out, sys.stdout)
    print("", file=sys.stdout)


if __name__ == "__main__":
    main()
