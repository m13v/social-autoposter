#!/usr/bin/env python3
"""
log_twitter_search_attempts.py

Insert one row per (query, project, tweets_found) into twitter_search_attempts.
Reads a JSON array on stdin shaped like:

    [
      {"query": "...", "project": "fazm", "tweets_found": 0},
      {"query": "...", "project": "mediar", "tweets_found": 3},
      ...
    ]

Used by run-twitter-cycle.sh after Phase 1 scan parses queries_used out of the
LLM envelope. Logging zero-result queries here is the whole point — the
twitter_candidates table only has rows for tweets that were actually scraped,
so duds were previously invisible. Pair with top_dud_twitter_queries.py.

    python3 scripts/log_twitter_search_attempts.py --batch-id <id> < queries.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--batch-id", default=None)
    args = p.parse_args()

    raw = sys.stdin.read().strip()
    if not raw:
        print("log_twitter_search_attempts: empty stdin, nothing to log", file=sys.stderr)
        return 0

    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"log_twitter_search_attempts: bad JSON on stdin: {e}", file=sys.stderr)
        return 1

    if not isinstance(rows, list) or not rows:
        print("log_twitter_search_attempts: not a list or empty list, nothing to log", file=sys.stderr)
        return 0

    conn = dbmod.get_conn()
    inserted = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        query = (r.get("query") or "").strip()
        project = (r.get("project") or "").strip() or None
        tweets_found = r.get("tweets_found")
        try:
            tweets_found = int(tweets_found if tweets_found is not None else 0)
        except (TypeError, ValueError):
            tweets_found = 0
        if not query:
            continue
        conn.execute(
            """
            INSERT INTO twitter_search_attempts (query, project_name, tweets_found, batch_id)
            VALUES (%s, %s, %s, %s)
            """,
            [query, project, tweets_found, args.batch_id],
        )
        inserted += 1
    conn.commit()
    conn.close()
    duds = sum(1 for r in rows if isinstance(r, dict) and not int(r.get("tweets_found") or 0))
    print(
        f"log_twitter_search_attempts: inserted {inserted} rows ({duds} duds) "
        f"for batch={args.batch_id}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
