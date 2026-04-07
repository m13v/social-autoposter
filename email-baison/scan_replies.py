#!/usr/bin/env python3
"""Scan EmailBison for new campaign replies and insert into email_replies table.

Usage:
    python3 email-baison/scan_replies.py [--since HOURS]

Requires EMAILBISON_API_KEY in ~/social-autoposter/.env
API docs: https://emailbison.com/developers
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import db as dbmod

ENV_PATH = os.path.expanduser("~/social-autoposter/.env")
BASE_URL = "https://emailbison.com/api"


def load_env():
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())


def api_get(endpoint, api_key, params=None):
    """GET request to EmailBison API."""
    url = f"{BASE_URL}{endpoint}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if qs:
            url += f"?{qs}"

    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "social-autoposter/1.0",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"ERROR: EmailBison API {e.code}: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"ERROR: EmailBison API request failed: {e}", file=sys.stderr)
        return None


def get_existing_reply_ids(conn):
    """Get set of bison_reply_id values already in the DB."""
    rows = conn.execute("SELECT bison_reply_id FROM email_replies").fetchall()
    return {row[0] for row in rows}


def insert_reply(conn, reply, campaign_name=None):
    """Insert a single reply into email_replies table."""
    conn.execute("""
        INSERT INTO email_replies
            (bison_reply_id, campaign_id, campaign_name, sequence_id,
             from_email, from_name, to_email, subject,
             body_text, body_html, received_at, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
        ON CONFLICT (bison_reply_id) DO NOTHING
    """, (
        reply.get("id", reply.get("reply_id", "")),
        reply.get("campaign_id", ""),
        campaign_name,
        reply.get("sequence_id", ""),
        reply.get("from_email", reply.get("from", "")),
        reply.get("from_name", ""),
        reply.get("to_email", reply.get("to", "")),
        reply.get("subject", ""),
        reply.get("body_text", reply.get("body", reply.get("text", ""))),
        reply.get("body_html", reply.get("html", "")),
        reply.get("received_at", reply.get("created_at", None)),
    ))


def scan(api_key, since_hours=24):
    """Fetch replies from EmailBison and insert new ones into DB."""
    conn = dbmod.connect()
    existing_ids = get_existing_reply_ids(conn)
    print(f"Already tracked: {len(existing_ids)} replies")

    # Fetch replies from API
    data = api_get("/replies", api_key, params={"limit": "100"})
    if not data:
        print("No data returned from EmailBison API")
        return 0

    replies = data if isinstance(data, list) else data.get("data", data.get("replies", []))
    print(f"Fetched {len(replies)} replies from EmailBison")

    # Also try to fetch campaign names for context
    campaigns_data = api_get("/campaigns", api_key)
    campaign_map = {}
    if campaigns_data:
        campaigns = campaigns_data if isinstance(campaigns_data, list) else campaigns_data.get("data", campaigns_data.get("campaigns", []))
        for c in campaigns:
            cid = c.get("id", c.get("campaign_id", ""))
            campaign_map[str(cid)] = c.get("name", c.get("title", ""))

    inserted = 0
    for reply in replies:
        rid = str(reply.get("id", reply.get("reply_id", "")))
        if rid in existing_ids:
            continue

        campaign_id = str(reply.get("campaign_id", ""))
        campaign_name = campaign_map.get(campaign_id, "")

        insert_reply(conn, reply, campaign_name)
        inserted += 1
        from_addr = reply.get("from_email", reply.get("from", "unknown"))
        print(f"  NEW: {from_addr} (campaign: {campaign_name or campaign_id})")

    conn.commit()
    print(f"Inserted {inserted} new replies ({len(replies) - inserted} already tracked)")
    return inserted


def main():
    parser = argparse.ArgumentParser(description="Scan EmailBison for campaign replies")
    parser.add_argument("--since", type=int, default=24, help="Look back N hours (default: 24)")
    args = parser.parse_args()

    load_env()
    api_key = os.environ.get("EMAILBISON_API_KEY", "")
    if not api_key:
        print("ERROR: EMAILBISON_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    scan(api_key, args.since)


if __name__ == "__main__":
    main()
