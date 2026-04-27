#!/usr/bin/env python3
"""Attach a single outbound action to a campaign and increment its counter.

Usage:
    python3 campaign_bump.py --table posts       --id 123 --campaign-id 3
    python3 campaign_bump.py --table replies     --id 456 --campaign-id 3
    python3 campaign_bump.py --table dm_messages --id 789 --campaign-id 3

The named row's campaign_id column is set to the given campaign, and the
campaign's posts_made counter advances by one. Idempotent: if the row already
references this campaign, no counter bump happens.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

ALLOWED_TABLES = {"posts", "replies", "dm_messages"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True, choices=sorted(ALLOWED_TABLES))
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--campaign-id", type=int, required=True)
    args = ap.parse_args()

    conn = db.get_conn()
    try:
        cur = conn.execute(
            f"UPDATE {args.table} SET campaign_id = %s "
            f"WHERE id = %s AND (campaign_id IS NULL OR campaign_id <> %s) "
            f"RETURNING id",
            [args.campaign_id, args.id, args.campaign_id],
        )
        bumped = cur.fetchone() is not None
        if bumped:
            conn.execute(
                "UPDATE campaigns SET posts_made = posts_made + 1, updated_at = NOW() "
                "WHERE id = %s",
                [args.campaign_id],
            )
        conn.commit()
        print(f"table={args.table} id={args.id} campaign={args.campaign_id} bumped={bumped}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
