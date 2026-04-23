#!/usr/bin/env python3
"""Query Postgres for SEO run stats and log to run_monitor.log.

Called from EXIT traps in cron_seo.sh and cron_gsc.sh to report
actual pages produced and Claude cost instead of hardcoded zeros.

Usage:
    python3 seo/log_seo_run.py --script serp_seo --since <unix_ts> --failed <exit_code> --elapsed <secs>
    python3 seo/log_seo_run.py --script gsc_seo  --since <unix_ts> --failed <exit_code> --elapsed <secs>
"""
import argparse
import os
import subprocess
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--script', required=True, choices=['serp_seo', 'gsc_seo'])
    parser.add_argument('--since', type=int, required=True, help='Unix timestamp of run start')
    parser.add_argument('--failed', type=int, default=0)
    parser.add_argument('--elapsed', type=float, default=0.0)
    args = parser.parse_args()

    run_start_ts = datetime.fromtimestamp(args.since, tz=timezone.utc).isoformat()

    pages = 0
    skipped = 0
    cost = 0.0

    _load_env()
    try:
        import psycopg2
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        cur = conn.cursor()

        if args.script == 'serp_seo':
            cur.execute(
                "SELECT COUNT(*) FROM seo_keywords WHERE status='done' AND completed_at >= %s",
                (run_start_ts,),
            )
            pages = int(cur.fetchone()[0] or 0)
            cur.execute(
                "SELECT COUNT(*) FROM seo_keywords WHERE status='skip' AND scored_at >= %s",
                (run_start_ts,),
            )
            skipped = int(cur.fetchone()[0] or 0)
        elif args.script == 'gsc_seo':
            cur.execute(
                "SELECT COUNT(*) FROM gsc_queries WHERE status='done' AND completed_at >= %s",
                (run_start_ts,),
            )
            pages = int(cur.fetchone()[0] or 0)

        cur.execute(
            "SELECT COALESCE(SUM(total_cost_usd), 0) FROM claude_sessions "
            "WHERE script='seo_generate_page' AND started_at >= %s",
            (run_start_ts,),
        )
        cost = float(cur.fetchone()[0] or 0)
        cur.close()
        conn.close()
    except Exception as e:
        print(f'[log_seo_run] DB query failed: {e}', file=sys.stderr)

    log_run = os.path.join(ROOT_DIR, 'scripts', 'log_run.py')
    subprocess.run(
        [
            sys.executable, log_run,
            '--script', args.script,
            '--posted', str(pages),
            '--skipped', str(skipped),
            '--failed', str(args.failed),
            '--cost', f'{cost:.4f}',
            '--elapsed', str(int(args.elapsed)),
        ],
        capture_output=True,
    )


if __name__ == '__main__':
    main()
