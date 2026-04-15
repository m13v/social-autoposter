#!/usr/bin/env python3
"""Attach a post to one or more campaigns and increment their counters.

Called by posting scripts right after `INSERT INTO posts ... RETURNING id`.

Usage:
    python3 campaign_bump.py --post-id 123 --campaign-ids 1,2
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--post-id", type=int, required=True)
    ap.add_argument("--campaign-ids", required=True, help="Comma-separated campaign IDs")
    args = ap.parse_args()

    ids = [int(x.strip()) for x in args.campaign_ids.split(",") if x.strip()]
    if not ids:
        print("No campaign IDs provided.")
        return 0

    conn = db.get_conn()
    try:
        for cid in ids:
            conn.execute(
                "INSERT INTO post_campaigns (post_id, campaign_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                [args.post_id, cid],
            )
            conn.execute(
                "UPDATE campaigns SET posts_made = posts_made + 1, updated_at = NOW() "
                "WHERE id = %s",
                [cid],
            )
        conn.commit()
        print(f"Attached post {args.post_id} to campaigns: {','.join(str(i) for i in ids)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
