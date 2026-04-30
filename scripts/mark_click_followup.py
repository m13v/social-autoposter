#!/usr/bin/env python3
"""Stamp dms.last_click_followup_at = NOW() after a click-driven follow-up
DM has been verified-sent.

Why a separate script (not a psql one-liner): mirrors the dm_send_log.py
pattern. Centralizing the write makes it easy to audit which call sites
flip the column and to add side-effects later (PostHog event, dm_messages
log row, etc.) without grepping every shell script.

Usage:
    python3 scripts/mark_click_followup.py --dm-id N [--verified]

The --verified flag is REQUIRED, mirroring dm_send_log.py. The shell
driver passes it only after the platform browser tool returned ok=true
AND verified=true on the actual DM send. Without it, this script refuses
to write.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def main():
    ap = argparse.ArgumentParser(description="Mark a DM's last_click_followup_at after a verified click-driven follow-up send.")
    ap.add_argument("--dm-id", type=int, required=True)
    ap.add_argument(
        "--verified",
        action="store_true",
        help="REQUIRED. Confirms the platform send_dm/compose_dm tool returned ok=true AND verified=true.",
    )
    ap.add_argument("--note", default=None, help="Optional one-line audit note (printed to stderr only).")
    args = ap.parse_args()

    if not args.verified:
        print(
            "ERROR: --verified is required. Do not call this script unless the platform send_dm tool returned ok=true AND verified=true.",
            file=sys.stderr,
        )
        sys.exit(2)

    db = dbmod.get_conn()
    cur = db.execute(
        """
        UPDATE dms
           SET last_click_followup_at = NOW()
         WHERE id = %s
         RETURNING id, their_author, platform, short_link_clicks, short_link_last_click_at, last_click_followup_at
        """,
        (args.dm_id,),
    )
    row = cur.fetchone()
    cur.close()
    if not row:
        db.close()
        print(f"ERROR: dm_id {args.dm_id} not found", file=sys.stderr)
        sys.exit(1)
    db.commit()
    db.close()

    msg = (
        f"[mark_click_followup] dm_id={row['id']} platform={row['platform']} "
        f"author={row['their_author']} clicks={row['short_link_clicks']} "
        f"last_click_at={row['short_link_last_click_at']} stamped_followup_at={row['last_click_followup_at']}"
    )
    if args.note:
        msg += f" note={args.note!r}"
    print(msg, file=sys.stderr)
    print(f"DM_ID={row['id']} CLICK_FOLLOWUP_STAMPED")


if __name__ == "__main__":
    main()
