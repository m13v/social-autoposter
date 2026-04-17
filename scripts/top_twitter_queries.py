#!/usr/bin/env python3
"""
top_twitter_queries.py

Returns the top-performing historical search queries by how many candidates
they produced that actually got posted. Used as STYLE inspiration for the
LLM that drafts new queries, not as literal keyword reuse.

    python3 scripts/top_twitter_queries.py [--limit 20] [--window-days 14]

Output: JSON list of {"query": ..., "posts": N, "avg_virality": X}
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
    p.add_argument("--window-days", type=int, default=14)
    args = p.parse_args()

    conn = dbmod.get_conn()
    rows = conn.execute(
        """
        SELECT search_topic,
               COUNT(*) FILTER (WHERE status='posted') AS posts,
               AVG(virality_score) AS avg_score
        FROM twitter_candidates
        WHERE search_topic IS NOT NULL
          AND search_topic <> ''
          AND discovered_at > NOW() - (%s || ' days')::interval
        GROUP BY search_topic
        HAVING COUNT(*) FILTER (WHERE status='posted') > 0
        ORDER BY posts DESC, avg_score DESC
        LIMIT %s
        """,
        [str(args.window_days), args.limit],
    ).fetchall()
    conn.close()

    out = [{"query": r[0], "posts": r[1], "avg_virality": round(float(r[2] or 0), 2)} for r in rows]
    json.dump(out, sys.stdout)
    print("", file=sys.stdout)


if __name__ == "__main__":
    main()
