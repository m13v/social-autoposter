#!/usr/bin/env python3
"""
top_linkedin_queries.py

Returns the top-performing historical LinkedIn search queries by how many
candidates they produced that actually got posted. Used as STYLE inspiration
for the LLM that drafts new queries, NOT as literal keyword reuse (LinkedIn
SERP shifts daily, so reusing the exact same query is wasteful).

Pair with top_dud_linkedin_queries.py (negative signal).

    python3 scripts/top_linkedin_queries.py [--limit 20] [--window-days 30]

Output: JSON list of {"query": ..., "posts": N, "avg_velocity": X, "avg_serp_quality": Y}

Window default 30 days (vs Twitter's 14): LinkedIn cycle is sparser, longer
window captures enough samples.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--window-days", type=int, default=30)
    args = p.parse_args()

    conn = dbmod.get_conn()
    rows = conn.execute(
        """
        SELECT search_query,
               COUNT(*) FILTER (WHERE status='posted') AS posts,
               AVG(velocity_score) AS avg_velocity,
               AVG(serp_quality_score) AS avg_serp
        FROM linkedin_candidates
        WHERE search_query IS NOT NULL
          AND search_query <> ''
          AND discovered_at > NOW() - (%s || ' days')::interval
        GROUP BY search_query
        HAVING COUNT(*) FILTER (WHERE status='posted') > 0
        ORDER BY posts DESC, avg_velocity DESC
        LIMIT %s
        """,
        [str(args.window_days), args.limit],
    ).fetchall()
    conn.close()

    out = [
        {
            "query": r[0],
            "posts": r[1],
            "avg_velocity": round(float(r[2] or 0), 2),
            "avg_serp_quality": round(float(r[3] or 0), 2) if r[3] is not None else None,
        }
        for r in rows
    ]
    json.dump(out, sys.stdout)
    print("", file=sys.stdout)


if __name__ == "__main__":
    main()
