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
elif cmd == "status":
    cur = db.execute("SELECT status, COUNT(*) FROM replies GROUP BY status ORDER BY status")
    for row in cur.fetchall():
        print(f"{row[0]} {row[1]}")
