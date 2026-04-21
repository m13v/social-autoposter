#!/usr/bin/env python3
"""
Sweep rows stuck in transient states (scoring / in_progress) back to
runnable states. The SERP and GSC pipelines set these states before
invoking Claude or the generator; if the shell process is killed
(launchd timeout, SIGTERM, hung grep on /tmp FIFO, OOM), the row is
never written back and becomes invisible to the picker.

Runs at the start of each cron pass.

Rules (thresholds from --minutes, default 30):
  seo_keywords.scoring      older than N min → unscored
  seo_keywords.in_progress  older than N min → pending if score>=1.5 else unscored
  gsc_queries.in_progress   older than N min → pending

Usage:
  python3 reap_stuck.py                # default 30 min threshold
  python3 reap_stuck.py --minutes 0    # reset everything (one-shot orphan fix)
  python3 reap_stuck.py --dry-run      # show what would change
"""

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)


def load_env():
    env_path = os.path.join(ROOT_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def get_conn():
    import psycopg2
    load_env()
    return psycopg2.connect(os.environ["DATABASE_URL"])


def reap(minutes: int, dry_run: bool = False) -> int:
    """Return number of rows reset."""
    conn = get_conn()
    cur = conn.cursor()
    total = 0

    interval = f"{minutes} minutes"

    # 1. seo_keywords.scoring → unscored
    cur.execute(f"""
        SELECT product, keyword, EXTRACT(EPOCH FROM (NOW()-updated_at))/60
        FROM seo_keywords
        WHERE status = 'scoring' AND updated_at < NOW() - INTERVAL '{interval}'
    """)
    scoring_rows = cur.fetchall()
    for product, kw, age in scoring_rows:
        print(f"  [seo_keywords] scoring→unscored | age={age:.0f}min | [{product}] {kw[:60]}")

    if not dry_run and scoring_rows:
        cur.execute(f"""
            UPDATE seo_keywords SET status='unscored', updated_at=NOW()
            WHERE status='scoring' AND updated_at < NOW() - INTERVAL '{interval}'
        """)
    total += len(scoring_rows)

    # 2. seo_keywords.in_progress → pending (if scored) or unscored
    cur.execute(f"""
        SELECT product, keyword, score, EXTRACT(EPOCH FROM (NOW()-updated_at))/60
        FROM seo_keywords
        WHERE status = 'in_progress' AND updated_at < NOW() - INTERVAL '{interval}'
    """)
    ip_rows = cur.fetchall()
    for product, kw, score, age in ip_rows:
        target = "pending" if (score is not None and score >= 1.5) else "unscored"
        print(f"  [seo_keywords] in_progress→{target} | age={age:.0f}min | score={score} | [{product}] {kw[:60]}")

    if not dry_run and ip_rows:
        cur.execute(f"""
            UPDATE seo_keywords
            SET status = CASE WHEN score IS NOT NULL AND score >= 1.5 THEN 'pending' ELSE 'unscored' END,
                updated_at = NOW()
            WHERE status='in_progress' AND updated_at < NOW() - INTERVAL '{interval}'
        """)
    total += len(ip_rows)

    # 3. gsc_queries.in_progress → pending
    cur.execute(f"""
        SELECT product, query, EXTRACT(EPOCH FROM (NOW()-updated_at))/60
        FROM gsc_queries
        WHERE status = 'in_progress' AND updated_at < NOW() - INTERVAL '{interval}'
    """)
    gsc_rows = cur.fetchall()
    for product, q, age in gsc_rows:
        print(f"  [gsc_queries] in_progress→pending | age={age:.0f}min | [{product}] {q[:60]}")

    if not dry_run and gsc_rows:
        cur.execute(f"""
            UPDATE gsc_queries SET status='pending', updated_at=NOW()
            WHERE status='in_progress' AND updated_at < NOW() - INTERVAL '{interval}'
        """)
    total += len(gsc_rows)

    if not dry_run:
        conn.commit()
    cur.close()
    conn.close()
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=30,
                    help="Reap rows older than this many minutes (default: 30)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    n = reap(args.minutes, args.dry_run)
    prefix = "[reap_stuck] DRY-RUN: would reset" if args.dry_run else "[reap_stuck] reset"
    print(f"{prefix} {n} row(s) (threshold={args.minutes}min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
