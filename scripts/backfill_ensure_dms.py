#!/usr/bin/env python3
"""Backfill dms rows for historical replies authors.

The post-reply ensure-dm wired into engage_reddit.py only runs going forward.
This script closes the historical gap: for every (platform, their_author)
where replies has a row with status in ('replied','sent') and no matching
dms row exists, call dm_conversation.py ensure-dm. Idempotent and safe to
re-run.

Usage:
    python3 scripts/backfill_ensure_dms.py                # reddit, dry-run preview
    python3 scripts/backfill_ensure_dms.py --apply        # reddit, actually run
    python3 scripts/backfill_ensure_dms.py --apply --platform x

Always prints the count first; pass --apply to actually invoke ensure-dm.
"""

import argparse
import os
import subprocess
import sys
import time

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_DIR, "scripts"))
import db as dbmod  # noqa: E402

DM_CONV = os.path.join(REPO_DIR, "scripts", "dm_conversation.py")


def find_orphan_authors(conn, platform):
    cur = conn.execute(
        """
        SELECT DISTINCT r.their_author
        FROM replies r
        LEFT JOIN dms d
          ON d.platform = r.platform AND d.their_author = r.their_author
        WHERE r.platform = %s
          AND r.status IN ('replied','sent')
          AND r.their_author IS NOT NULL
          AND r.their_author <> ''
          AND d.id IS NULL
        ORDER BY r.their_author
        """,
        (platform,),
    )
    return [row["their_author"] for row in cur.fetchall()]


def ensure_dm(platform, author):
    try:
        out = subprocess.run(
            ["python3", DM_CONV, "ensure-dm", "--platform", platform, "--author", author],
            capture_output=True, text=True, timeout=30,
        )
        return out.returncode == 0, (out.stdout or out.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, f"EXC: {e}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--platform", default="reddit", choices=["reddit", "linkedin", "x", "twitter"])
    ap.add_argument("--apply", action="store_true",
                    help="Actually call ensure-dm. Without this flag, only the count is printed.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap the number of authors to backfill in this run (for staged rollout).")
    args = ap.parse_args()

    dbmod.load_env()
    conn = dbmod.get_conn()
    authors = find_orphan_authors(conn, args.platform)
    conn.close()

    if not authors:
        print(f"No orphan authors found for platform={args.platform}. Nothing to do.")
        return 0

    if args.limit:
        authors = authors[: args.limit]

    print(f"Found {len(authors)} orphan {args.platform} authors (have replies rows but no dms row).")

    if not args.apply:
        sample = authors[:10]
        print("Sample (first 10):")
        for a in sample:
            print(f"  - {a}")
        print()
        print("Dry-run only. Re-run with --apply to actually create dms rows.")
        return 0

    start = time.time()
    ok = 0
    fail = 0
    for i, author in enumerate(authors, 1):
        success, out = ensure_dm(args.platform, author)
        if success:
            ok += 1
        else:
            fail += 1
            print(f"  [FAIL] @{author}: {out[:200]}")
        if i % 25 == 0 or i == len(authors):
            elapsed = time.time() - start
            print(f"  {i}/{len(authors)} done ok={ok} fail={fail} ({elapsed:.1f}s)")

    elapsed = time.time() - start
    print()
    print(f"Backfill complete: ok={ok} fail={fail} elapsed={elapsed:.1f}s")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
