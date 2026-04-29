#!/usr/bin/env python3
"""Query claude_sessions to get total cost for sessions started after a given Unix timestamp.

Usage:
    python3 scripts/get_run_cost.py --since <unix_ts> --scripts tag1 tag2 ...

Prints the total cost as a float (4 decimal places), or 0.0000 on any error.
Designed to be called from shell script EXIT traps to get real cost per run.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env():
    env_path = os.path.join(ROOT_DIR, '.env')
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--since', type=int, required=True,
                   help='Unix timestamp of run start')
    p.add_argument('--scripts', nargs='+', required=True,
                   help='claude_sessions.script values to sum')
    args = p.parse_args()

    since_ts = datetime.fromtimestamp(args.since, tz=timezone.utc).isoformat()
    _load_env()

    try:
        import psycopg2
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        cur = conn.cursor()
        placeholders = ','.join(['%s'] * len(args.scripts))
        cur.execute(
            f"SELECT COALESCE(SUM(total_cost_usd), 0) FROM claude_sessions "
            f"WHERE script IN ({placeholders}) AND started_at >= %s",
            args.scripts + [since_ts],
        )
        cost = float(cur.fetchone()[0] or 0)
        cur.close()
        conn.close()
        print(f"{cost:.4f}")
    except Exception:
        print("0.0000")


if __name__ == '__main__':
    main()
