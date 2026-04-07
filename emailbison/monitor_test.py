#!/usr/bin/env python3
"""Monitor test campaign 359 and check if the email was delivered."""

import requests
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

API_KEY = os.environ["EMAILBISON_API_KEY"]
BASE_URL = os.environ["EMAILBISON_BASE_URL"]
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
}

CAMPAIGN_ID = 359
LEAD_ID = 1218256


def check():
    # Campaign status
    r = requests.get(f"{BASE_URL}/api/campaigns/{CAMPAIGN_ID}", headers=HEADERS)
    camp = r.json()["data"]
    print(f"Campaign: {camp['name']}")
    print(f"  Status: {camp['status']}")
    print(f"  Emails sent: {camp['emails_sent']}")
    print(f"  Total leads: {camp['total_leads']}")
    print()

    # Sent emails for lead
    r = requests.get(f"{BASE_URL}/api/leads/{LEAD_ID}/sent-emails", headers=HEADERS)
    sent = r.json()["data"]
    if sent:
        print(f"Sent emails to lead {LEAD_ID}:")
        for e in sent:
            print(f"  - {e.get('subject', 'N/A')} (sent: {e.get('created_at', 'N/A')})")
    else:
        print(f"No emails sent to lead {LEAD_ID} yet.")

    # Replies from lead
    r = requests.get(f"{BASE_URL}/api/leads/{LEAD_ID}/replies", headers=HEADERS)
    replies = r.json()["data"]
    if replies:
        print(f"\nReplies from lead:")
        for rep in replies:
            print(f"  - {rep.get('subject', 'N/A')} ({rep.get('date_received', 'N/A')})")
    print()


if __name__ == "__main__":
    check()
