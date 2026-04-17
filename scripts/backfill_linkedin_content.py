#!/usr/bin/env python3
"""Backfill real comment body text for LinkedIn reply rows whose their_content
is still the notification headline ('X replied to your comment.', etc.).

Takes a list of reply IDs, live-fetches each comment, and UPDATEs their_content.
Does not touch status — run SQL to flip status separately.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod
import linkedin_comment_fetch as lcf


def main():
    if len(sys.argv) < 2:
        print("Usage: backfill_linkedin_content.py <id1> [id2] ...", file=sys.stderr)
        sys.exit(2)

    ids = [int(x) for x in sys.argv[1:]]
    conn = dbmod.get_conn()

    rows = conn.execute(
        "SELECT id, their_author, their_comment_id, their_comment_url, their_content "
        "FROM replies WHERE platform='linkedin' AND id = ANY(%s)",
        (ids,),
    ).fetchall()

    for row in rows:
        rid = row[0] if isinstance(row, (list, tuple)) else row["id"]
        author = row[1] if isinstance(row, (list, tuple)) else row["their_author"]
        urn = row[2] if isinstance(row, (list, tuple)) else row["their_comment_id"]
        url = row[3] if isinstance(row, (list, tuple)) else row["their_comment_url"]
        old = row[4] if isinstance(row, (list, tuple)) else row["their_content"]

        m = re.search(r"urn:li:(?:activity|ugcPost):(\d+)", urn or "")
        activity_id = m.group(1) if m else None
        if not activity_id:
            m = re.search(r"(?:activity|ugcPost)[:%]3A(\d+)", url or "")
            activity_id = m.group(1) if m else None

        if not activity_id or not urn:
            print(f"  SKIP id={rid} ({author}): no activity_id or URN")
            continue

        print(f"  fetching id={rid} ({author}) activity={activity_id}...")
        live = lcf.fetch_live_content(activity_id, urn, target_author=author)
        if not live:
            print(f"    FAIL id={rid}: no content returned")
            continue

        conn.execute(
            "UPDATE replies SET their_content=%s WHERE id=%s",
            (live, rid),
        )
        conn.commit()
        print(f"    OK id={rid}: {len(live)} chars (was {len(old or '')} chars)")

    conn.close()


if __name__ == "__main__":
    main()
