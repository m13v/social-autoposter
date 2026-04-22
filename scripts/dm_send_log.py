#!/usr/bin/env python3
"""Log a successful DM send. Usage: dm_send_log.py <dm_id> <message>"""
import os
import sys
import subprocess
import psycopg2

def load_env():
    env_path = "/Users/matthewdi/social-autoposter/.env"
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if not line or line.startswith("#"): continue
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def main():
    load_env()
    dm_id = sys.argv[1]
    msg = sys.argv[2]
    db = os.environ["DATABASE_URL"]
    conn = psycopg2.connect(db)
    with conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE dms SET status='sent', our_dm_content=%s, sent_at=NOW(), "
            "claude_session_id='e976a48c-1a87-40c4-8e39-3a2c911e67f5'::uuid WHERE id=%s",
            (msg, dm_id),
        )
    conn.close()
    subprocess.run(["python3", "/Users/matthewdi/social-autoposter/scripts/dm_conversation.py",
                    "log-outbound", "--dm-id", dm_id, "--content", msg], check=True)

if __name__ == "__main__":
    main()
