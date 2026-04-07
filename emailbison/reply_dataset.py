#!/usr/bin/env python3
"""
Pull reply threads from EmailBison API and save as a structured dataset.
Shows: initial outreach -> their reply -> our response
"""

import requests
import json
import re
import csv
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

OUTPUT_JSON = Path(__file__).parent / "reply_threads.json"
OUTPUT_CSV = Path(__file__).parent / "reply_threads.csv"


def clean_body(text, max_len=1000):
    """Strip quoted text, signatures, and clean up."""
    if not text:
        return ""
    lines = text.split('\n')
    clean = []
    for line in lines:
        if line.strip().startswith('>'):
            break
        if re.match(r'^On .+ wrote:$', line.strip()):
            break
        if 'Sent from my iPhone' in line or 'Sent from my Galaxy' in line:
            continue
        clean.append(line)
    result = '\n'.join(clean).strip()
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result[:max_len]


def fetch_interested_replies(pages=3):
    """Fetch replies tagged as interested."""
    all_replies = []
    for page in range(1, pages + 1):
        r = requests.get(
            f"{BASE_URL}/api/replies",
            headers=HEADERS,
            params={"folder": "inbox", "status": "interested", "per_page": 15, "page": page}
        )
        data = r.json()
        all_replies.extend(data.get("data", []))
        meta = data.get("meta", {})
        if page >= meta.get("last_page", 1):
            break
    return all_replies


def fetch_thread(reply_id):
    """Fetch the full conversation thread for a reply."""
    r = requests.get(
        f"{BASE_URL}/api/replies/{reply_id}/conversation-thread",
        headers=HEADERS,
    )
    return r.json().get("data", {})


def fetch_lead(lead_id):
    """Fetch lead details."""
    r = requests.get(f"{BASE_URL}/api/leads/{lead_id}", headers=HEADERS)
    return r.json().get("data", {})


def build_dataset():
    print("Fetching interested replies...")
    replies = fetch_interested_replies(pages=5)
    print(f"Found {len(replies)} interested replies")

    dataset = []
    for i, reply in enumerate(replies):
        reply_id = reply["id"]
        lead_id = reply.get("lead_id")
        print(f"  [{i+1}/{len(replies)}] Processing reply {reply_id} from {reply.get('from_email_address')}...")

        # Fetch thread
        thread = fetch_thread(reply_id)
        if not thread:
            continue

        current = thread.get("current_reply", {})
        newer = thread.get("newer_messages", [])
        older = thread.get("older_messages", [])

        # Find our response (first outbound in newer)
        our_reply = None
        for m in newer:
            if m.get("folder") == "Sent":
                our_reply = m
                break

        # Fetch lead info
        lead = {}
        if lead_id:
            lead = fetch_lead(lead_id)

        record = {
            "reply_id": reply_id,
            "lead_id": lead_id,
            "lead_name": current.get("from_name", ""),
            "lead_email": current.get("from_email_address", ""),
            "lead_company": lead.get("company", ""),
            "lead_title": lead.get("title", ""),
            "campaign_id": current.get("campaign_id"),
            "subject": current.get("subject", ""),
            "their_reply_date": current.get("date_received", ""),
            "their_reply_body": clean_body(current.get("text_body", "")),
            "our_response_date": our_reply.get("date_received", "") if our_reply else "",
            "our_response_body": clean_body(our_reply.get("text_body", "")) if our_reply else "",
            "has_our_response": our_reply is not None,
            "reply_type": categorize_reply(current.get("text_body", "")),
        }
        dataset.append(record)

    return dataset


def categorize_reply(text):
    """Simple categorization of reply type."""
    if not text:
        return "empty"
    text_lower = text.lower()
    if any(w in text_lower for w in ["unsubscribe", "stop", "remove me", "take me off", "opt out"]):
        return "unsubscribe"
    if any(w in text_lower for w in ["not interested", "no thanks", "no thank", "pass on this", "not for me"]):
        return "not_interested"
    if any(w in text_lower for w in ["out of office", "automatic reply", "auto-reply", "i am out"]):
        return "ooo"
    if any(w in text_lower for w in ["interested", "yes", "sure", "send", "please", "love to", "happy to"]):
        return "interested"
    if "?" in text:
        return "question"
    return "other"


def save_dataset(dataset):
    # JSON
    with open(OUTPUT_JSON, "w") as f:
        json.dump(dataset, f, indent=2)
    print(f"\nSaved {len(dataset)} records to {OUTPUT_JSON}")

    # CSV
    if dataset:
        keys = dataset[0].keys()
        with open(OUTPUT_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(dataset)
        print(f"Saved {len(dataset)} records to {OUTPUT_CSV}")


def print_summary(dataset):
    print(f"\n{'='*60}")
    print(f"DATASET SUMMARY")
    print(f"{'='*60}")
    print(f"Total threads: {len(dataset)}")
    print(f"With our response: {sum(1 for d in dataset if d['has_our_response'])}")

    from collections import Counter
    types = Counter(d["reply_type"] for d in dataset)
    print(f"\nReply types:")
    for t, count in types.most_common():
        print(f"  {t}: {count}")

    print(f"\n{'='*60}")
    print("SAMPLE THREADS")
    print(f"{'='*60}")
    for d in dataset[:5]:
        print(f"\n--- {d['lead_name']} ({d['lead_email']}) | {d['lead_company']} ---")
        print(f"Subject: {d['subject']}")
        print(f"Type: {d['reply_type']}")
        print(f"Their reply ({d['their_reply_date'][:10]}):")
        print(f"  {d['their_reply_body'][:200]}")
        if d['has_our_response']:
            print(f"Our response ({d['our_response_date'][:10]}):")
            print(f"  {d['our_response_body'][:200]}")
        else:
            print("  [NO RESPONSE YET]")


if __name__ == "__main__":
    dataset = build_dataset()
    save_dataset(dataset)
    print_summary(dataset)
