#!/usr/bin/env python3
"""Quick DB operations for the engage bot. Single persistent connection."""
import sys, json, os
sys.path.insert(0, os.path.dirname(__file__))
from db import load_env, get_conn
load_env()
db = get_conn()

cmd = sys.argv[1]
if cmd == "processing":
    # reply_db.py processing ID
    # Mark as in-progress BEFORE browser action to prevent re-processing on crash
    rid = int(sys.argv[2])
    db.execute("UPDATE replies SET status='processing', processing_at=NOW() WHERE id=%s AND status='pending'", [rid])
    db.commit()
    print(f"ok {rid}")
elif cmd == "replied":
    # reply_db.py replied ID "content" [url]
    rid, content = int(sys.argv[2]), sys.argv[3]
    url = sys.argv[4] if len(sys.argv) > 4 else None
    db.execute("UPDATE replies SET status='replied', our_reply_content=%s, our_reply_url=%s, replied_at=NOW() WHERE id=%s", [content, url, rid])
    db.commit()
    print(f"ok {rid}")
elif cmd == "skipped":
    # reply_db.py skipped ID "reason"
    rid, reason = int(sys.argv[2]), sys.argv[3]
    db.execute("UPDATE replies SET status='skipped', skip_reason=%s WHERE id=%s", [reason, rid])
    db.commit()
    print(f"ok {rid}")
elif cmd == "skip_batch":
    # reply_db.py skip_batch '{"ids":[1,2,3],"reason":"..."}'
    data = json.loads(sys.argv[2])
    for rid in data["ids"]:
        db.execute("UPDATE replies SET status='skipped', skip_reason=%s WHERE id=%s", [data["reason"], rid])
    db.commit()
    print(f"ok {len(data['ids'])}")
elif cmd == "audit":
    # reply_db.py audit [platform]
    # Show author+thread pairs where we replied more than once
    platform = sys.argv[2] if len(sys.argv) > 2 else None
    where = "WHERE r.status='replied'"
    params = []
    if platform:
        where += " AND r.platform=%s"
        params.append(platform)
    cur = db.execute(f"""
        SELECT r.their_author, r.platform, p.our_url, COUNT(*) as cnt,
               array_agg(r.id ORDER BY r.replied_at) as reply_ids
        FROM replies r JOIN posts p ON r.post_id = p.id
        {where}
        GROUP BY r.their_author, r.platform, p.our_url
        HAVING COUNT(*) > 1
        ORDER BY COUNT(*) DESC
    """, params)
    rows = cur.fetchall()
    if not rows:
        print("No duplicate author-thread engagements found.")
    else:
        print(f"Found {len(rows)} author-thread pairs with multiple replies:")
        for row in rows:
            print(f"  {row[0]} ({row[1]}) x{row[3]} on {row[2][:80]}... ids={row[4]}")
elif cmd == "status":
    cur = db.execute("SELECT status, COUNT(*) FROM replies GROUP BY status ORDER BY status")
    for row in cur.fetchall():
        print(f"{row[0]} {row[1]}")
