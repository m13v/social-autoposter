#!/usr/bin/env python3
"""Persist a Phase 2b draft on a twitter_candidates row.

Called by Claude inside Phase 2b BEFORE the twitter_browser.py post attempt,
so a CDP / browser / monthly-cap failure doesn't waste the LLM redraft on the
next cycle. The next cycle's Phase 2b sees draft_reply_text on the salvaged
row and posts it as-is when fresh (DRAFT_TTL).

Usage:
    python3 scripts/log_draft.py \\
        --candidate-id 12345 \\
        --text "your reply text here" \\
        --style curious_probe \\
        [--platform twitter]

Output (JSON):
    {"logged": true, "candidate_id": 12345, "drafted_at": "..."}
    {"error": "CANDIDATE_NOT_FOUND", ...}
    {"error": "ALREADY_POSTED", ...}    # candidate has status != 'pending'
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--candidate-id", type=int, required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--style", default=None)
    p.add_argument(
        "--platform",
        default="twitter",
        choices=["twitter"],
        help="Reserved for future Reddit/LinkedIn drafts; only twitter for now.",
    )
    args = p.parse_args()

    text = args.text.strip()
    if not text:
        print(json.dumps({"error": "EMPTY_TEXT"}))
        sys.exit(1)

    table = "twitter_candidates"  # platform is twitter-only today

    dbmod.load_env()
    conn = dbmod.get_conn()
    try:
        cur = conn.execute(
            f"SELECT status FROM {table} WHERE id = %s",
            (args.candidate_id,),
        )
        row = cur.fetchone()
        if not row:
            print(json.dumps({"error": "CANDIDATE_NOT_FOUND", "candidate_id": args.candidate_id}))
            sys.exit(1)
        if row[0] != "pending":
            print(json.dumps({
                "error": "ALREADY_POSTED",
                "candidate_id": args.candidate_id,
                "status": row[0],
            }))
            sys.exit(1)

        cur = conn.execute(
            f"""
            UPDATE {table}
            SET draft_reply_text = %s,
                draft_engagement_style = %s,
                drafted_at = NOW()
            WHERE id = %s
            RETURNING drafted_at
            """,
            (text, args.style, args.candidate_id),
        )
        drafted_at = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    print(json.dumps({
        "logged": True,
        "candidate_id": args.candidate_id,
        "drafted_at": drafted_at.isoformat(),
    }))


if __name__ == "__main__":
    main()
