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


SUPPORTED_SCRIPTS = [
    'serp_seo',
    'gsc_seo',
    'seo_improve',
    'seo_top_pages',
    'seo_weekly_roundup',
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--script', required=True, choices=SUPPORTED_SCRIPTS)
    parser.add_argument('--since', type=int, required=True, help='Unix timestamp of run start')
    parser.add_argument('--failed', type=int, default=0, help='Shell exit code of the wrapping script')
    parser.add_argument('--elapsed', type=float, default=0.0)
    args = parser.parse_args()

    run_start_ts = datetime.fromtimestamp(args.since, tz=timezone.utc).isoformat()

    pages = 0
    skipped = 0
    db_failed = 0
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
            cur.execute(
                "SELECT COALESCE(SUM(total_cost_usd), 0) FROM claude_sessions "
                "WHERE script='seo_generate_page' AND started_at >= %s",
                (run_start_ts,),
            )
            cost = float(cur.fetchone()[0] or 0)

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

        elif args.script == 'seo_improve':
            # posted = pages actually committed to a website repo.
            # failed = pages that started but errored before commit.
            cur.execute(
                "SELECT status, COUNT(*) FROM seo_page_improvements "
                "WHERE completed_at >= %s GROUP BY status",
                (run_start_ts,),
            )
            for status, count in cur.fetchall():
                if status == 'committed':
                    pages = int(count or 0)
                elif status == 'failed':
                    db_failed = int(count or 0)
            cur.execute(
                "SELECT COALESCE(SUM(total_cost_usd), 0) FROM claude_sessions "
                "WHERE script='seo_improve_page' AND started_at >= %s",
                (run_start_ts,),
            )
            cost = float(cur.fetchone()[0] or 0)

        elif args.script in ('seo_top_pages', 'seo_weekly_roundup'):
            source = 'top_page' if args.script == 'seo_top_pages' else 'roundup'
            # posted = rows that reached 'done'. skipped = explicit 'skip'. The
            # remainder of rows touched in the window (still 'pending' or
            # 'in_progress') are lane failures where the generator never marked
            # them done — typecheck bounces, missing success JSON, etc.
            cur.execute(
                "SELECT COUNT(*) FROM seo_keywords "
                "WHERE source=%s AND status='done' AND completed_at >= %s",
                (source, run_start_ts),
            )
            pages = int(cur.fetchone()[0] or 0)
            cur.execute(
                "SELECT COUNT(*) FROM seo_keywords "
                "WHERE source=%s AND status='skip' AND updated_at >= %s",
                (source, run_start_ts),
            )
            skipped = int(cur.fetchone()[0] or 0)
            cur.execute(
                "SELECT COUNT(*) FROM seo_keywords "
                "WHERE source=%s AND status IN ('pending','in_progress') "
                "AND updated_at >= %s",
                (source, run_start_ts),
            )
            db_failed = int(cur.fetchone()[0] or 0)
            # Attribute cost only to sessions whose seo_keywords row matches
            # this source — serp_seo, top_pages, and roundup all share
            # script='seo_generate_page', so we can't filter by script alone.
            cur.execute(
                "SELECT COALESCE(SUM(cs.total_cost_usd), 0) FROM claude_sessions cs "
                "JOIN seo_keywords sk ON sk.claude_session_id = cs.session_id "
                "WHERE cs.script='seo_generate_page' AND cs.started_at >= %s "
                "AND sk.source = %s",
                (run_start_ts, source),
            )
            cost = float(cur.fetchone()[0] or 0)

        cur.close()
        conn.close()
    except Exception as e:
        print(f'[log_seo_run] DB query failed: {e}', file=sys.stderr)

    # Sum content-level failures (from DB) with shell-level failure (non-zero
    # exit from the wrapping script). If the script crashed outright, db_failed
    # is usually 0 but we still want the row flagged.
    total_failed = db_failed + (1 if args.failed else 0)

    log_run = os.path.join(ROOT_DIR, 'scripts', 'log_run.py')
    subprocess.run(
        [
            sys.executable, log_run,
            '--script', args.script,
            '--posted', str(pages),
            '--skipped', str(skipped),
            '--failed', str(total_failed),
            '--cost', f'{cost:.4f}',
            '--elapsed', str(int(args.elapsed)),
        ],
        capture_output=True,
    )


if __name__ == '__main__':
    main()
