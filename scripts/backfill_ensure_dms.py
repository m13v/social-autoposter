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


def bulk_insert_orphans(conn, platform):
    """Single INSERT ... SELECT that creates dms rows for every orphan author.

    Mirrors dm_conversation.ensure_dm's auto-link semantics: pick the most
    recent replies row per author, link reply_id + post_id + comment_context
    from it. Drops the 720-hour lookback (backfill wants to link historical
    rows even if old; the original lookback exists to prevent cold DMs from
    auto-linking to ancient unrelated thread engagement, which doesn't apply
    when we're walking the replies table itself). Atomic, idempotent — safe
    to re-run.

    Returns the number of rows inserted.
    """
    cur = conn.execute(
        """
        WITH latest_per_author AS (
          SELECT DISTINCT ON (their_author)
                 id, platform, their_author, post_id, their_content
          FROM replies
          WHERE platform = %s
            AND status IN ('replied','sent')
            AND their_author IS NOT NULL AND their_author <> ''
          ORDER BY their_author, discovered_at DESC
        )
        INSERT INTO dms (platform, their_author, reply_id, post_id,
                         comment_context, status, conversation_status, tier,
                         discovered_at)
        SELECT r.platform, r.their_author, r.id, r.post_id,
               LEFT(r.their_content, 1000),
               'sent', 'active', 1, NOW()
        FROM latest_per_author r
        WHERE NOT EXISTS (
          SELECT 1 FROM dms d
          WHERE d.platform = r.platform AND d.their_author = r.their_author
        )
        RETURNING id, their_author
        """,
        (platform,),
    )
    inserted = cur.fetchall()
    conn.commit()
    return inserted


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--platform", default="reddit", choices=["reddit", "linkedin", "x", "twitter"])
    ap.add_argument("--apply", action="store_true",
                    help="Actually run the bulk insert. Without this flag, only the count is printed.")
    args = ap.parse_args()

    dbmod.load_env()
    conn = dbmod.get_conn()
    try:
        authors = find_orphan_authors(conn, args.platform)
        if not authors:
            print(f"No orphan authors found for platform={args.platform}. Nothing to do.")
            return 0

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
        inserted = bulk_insert_orphans(conn, args.platform)
        elapsed = time.time() - start
        print(f"Backfill complete: inserted={len(inserted)} elapsed={elapsed:.2f}s")
        if inserted:
            print(f"  first 5: {[r['their_author'] for r in inserted[:5]]}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
