#!/usr/bin/env python3
"""
EmailBison Auto-Draft Reply Pipeline

Fetches new (unread, non-automated) replies from EmailBison,
generates draft responses using Claude, and stores them for review.
On approval, sends via the EmailBison API.

Usage:
    python3 emailbison/draft_replies.py                  # Fetch new replies & generate drafts
    python3 emailbison/draft_replies.py --review          # Review & send pending drafts
    python3 emailbison/draft_replies.py --reply-id 12345  # Draft for a specific reply
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

API_KEY = os.environ["EMAILBISON_API_KEY"]
BASE_URL = os.environ["EMAILBISON_BASE_URL"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

DRAFTS_FILE = Path(__file__).parent / "pending_drafts.json"
SENT_LOG = Path(__file__).parent / "sent_log.json"
SYSTEM_PROMPT_FILE = Path(__file__).parent / "reply_system_prompt.md"


# ---------------------------------------------------------------------------
# EmailBison API helpers
# ---------------------------------------------------------------------------

def eb_get(endpoint, params=None):
    r = requests.get(f"{BASE_URL}/api/{endpoint}", headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def eb_post(endpoint, data):
    r = requests.post(f"{BASE_URL}/api/{endpoint}", headers=HEADERS, json=data)
    r.raise_for_status()
    return r.json()


def fetch_new_replies(pages=2):
    """Fetch unread, non-automated inbox replies."""
    all_replies = []
    for page in range(1, pages + 1):
        data = eb_get("replies", {
            "folder": "inbox",
            "per_page": 15,
            "page": page,
        })
        replies = data.get("data", [])
        # Filter out automated replies and already-read replies
        replies = [r for r in replies if not r.get("automated_reply") and not r.get("read")]
        all_replies.extend(replies)
        meta = data.get("meta", {})
        if page >= meta.get("last_page", 1):
            break
    return all_replies


def fetch_thread(reply_id):
    """Get the full conversation thread for a reply."""
    data = eb_get(f"replies/{reply_id}/conversation-thread")
    return data.get("data", {})


def fetch_lead(lead_id):
    """Get lead details."""
    try:
        return eb_get(f"leads/{lead_id}").get("data", {})
    except Exception:
        return {}


def fetch_campaign(campaign_id):
    """Get campaign details."""
    try:
        return eb_get(f"campaigns/{campaign_id}").get("data", {})
    except Exception:
        return {}


def send_reply(reply_id, message, sender_email_id, to_emails):
    """Send a reply via EmailBison."""
    return eb_post(f"replies/{reply_id}/reply", {
        "message": message,
        "sender_email_id": sender_email_id,
        "to_emails": to_emails,
        "content_type": "html",
        "inject_previous_email_body": True,
    })


def compose_new(subject, message, sender_email_id, to_emails):
    """Send a new one-off email."""
    return eb_post("replies/new", {
        "subject": subject,
        "message": message,
        "sender_email_id": sender_email_id,
        "to_emails": to_emails,
        "content_type": "html",
    })


# ---------------------------------------------------------------------------
# Claude draft generation
# ---------------------------------------------------------------------------

def load_system_prompt():
    return SYSTEM_PROMPT_FILE.read_text()


def clean_reply_body(text, max_len=2000):
    """Strip quoted text and signatures for context."""
    if not text:
        return ""
    lines = text.split("\n")
    clean = []
    for line in lines:
        if line.strip().startswith(">"):
            break
        if re.match(r"^On .+ wrote:$", line.strip()):
            break
        if "Sent from my iPhone" in line:
            continue
        clean.append(line)
    return "\n".join(clean).strip()[:max_len]


def build_thread_context(thread):
    """Build a readable conversation history from thread data."""
    parts = []
    older = thread.get("older_messages", [])
    current = thread.get("current_reply", {})
    newer = thread.get("newer_messages", [])

    for m in older:
        direction = "OUTBOUND (us)" if m.get("folder") == "Sent" else "INBOUND (them)"
        body = clean_reply_body(m.get("text_body", ""))
        parts.append(f"[{direction} | {m.get('date_received', '')[:16]}]\n{body}")

    body = clean_reply_body(current.get("text_body", ""))
    parts.append(f"[INBOUND (them) | {current.get('date_received', '')[:16]}]\n{body}")

    # Include any existing responses (to avoid duplicates)
    for m in newer:
        if m.get("folder") == "Sent":
            body = clean_reply_body(m.get("text_body", ""))
            parts.append(f"[OUTBOUND (us) | {m.get('date_received', '')[:16]}]\n{body}")

    return "\n\n---\n\n".join(parts)


def generate_draft(reply_data, lead, campaign, thread):
    """Call Claude to generate a draft reply."""
    system_prompt = load_system_prompt()
    current = thread.get("current_reply", {})
    thread_context = build_thread_context(thread)

    # Check if we already replied
    newer = thread.get("newer_messages", [])
    already_replied = any(m.get("folder") == "Sent" for m in newer)
    if already_replied:
        return None, "Already replied to this thread"

    user_message = f"""Draft a reply to this lead.

**Lead info:**
- Name: {current.get('from_name', reply_data.get('from_name', 'Unknown'))}
- Email: {current.get('from_email_address', '')}
- Company: {lead.get('company', 'Unknown')}
- Title: {lead.get('title', 'Unknown')}

**Campaign:** {campaign.get('name', 'Unknown')}
**Subject:** {current.get('subject', '')}

**Conversation history:**
{thread_context}

**Their latest reply (the one to respond to):**
{clean_reply_body(current.get('text_body', ''))}

Draft the reply now. Return ONLY the reply body text."""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    response = client.messages.create(
        model="claude-opus-4-20250514",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    draft = response.content[0].text.strip()
    return draft, None


# ---------------------------------------------------------------------------
# Draft storage
# ---------------------------------------------------------------------------

def load_drafts():
    if DRAFTS_FILE.exists():
        return json.loads(DRAFTS_FILE.read_text())
    return []


def save_drafts(drafts):
    DRAFTS_FILE.write_text(json.dumps(drafts, indent=2))


def load_sent_log():
    if SENT_LOG.exists():
        return json.loads(SENT_LOG.read_text())
    return []


def save_sent_log(log):
    SENT_LOG.write_text(json.dumps(log, indent=2))


def is_already_drafted(reply_id, drafts):
    return any(d["reply_id"] == reply_id for d in drafts)


def is_already_sent(reply_id):
    log = load_sent_log()
    return any(s["reply_id"] == reply_id for s in log)


# ---------------------------------------------------------------------------
# Main workflows
# ---------------------------------------------------------------------------

def run_fetch_and_draft(max_drafts=10, reply_id=None):
    """Fetch new replies and generate drafts."""
    drafts = load_drafts()

    if reply_id:
        # Draft for a specific reply
        replies = [eb_get(f"replies/{reply_id}").get("data", {})]
    else:
        print("Fetching new unread replies...")
        replies = fetch_new_replies(pages=3)
        print(f"Found {len(replies)} unread non-automated replies")

    drafted = 0
    for reply in replies:
        rid = reply.get("id")
        if not rid:
            continue

        if is_already_drafted(rid, drafts) or is_already_sent(rid):
            continue

        lead_id = reply.get("lead_id")
        campaign_id = reply.get("campaign_id")

        print(f"\n--- Processing reply {rid} from {reply.get('from_email_address', '?')} ---")

        lead = fetch_lead(lead_id) if lead_id else {}
        campaign = fetch_campaign(campaign_id) if campaign_id else {}
        thread = fetch_thread(rid)

        if not thread:
            print("  Could not fetch thread, skipping")
            continue

        draft_text, skip_reason = generate_draft(reply, lead, campaign, thread)

        if skip_reason:
            print(f"  Skipped: {skip_reason}")
            continue

        current = thread.get("current_reply", {})
        draft_record = {
            "reply_id": rid,
            "lead_id": lead_id,
            "lead_name": current.get("from_name", ""),
            "lead_email": current.get("from_email_address", ""),
            "lead_company": lead.get("company", ""),
            "subject": current.get("subject", ""),
            "their_reply": clean_reply_body(current.get("text_body", "")),
            "draft_response": draft_text,
            "sender_email_id": current.get("sender_email_id"),
            "campaign_id": campaign_id,
            "created_at": datetime.utcnow().isoformat(),
            "status": "pending",
        }
        drafts.append(draft_record)
        save_drafts(drafts)

        print(f"  Lead: {draft_record['lead_name']} ({draft_record['lead_email']})")
        print(f"  Their reply: {draft_record['their_reply'][:100]}")
        print(f"  Draft: {draft_text[:150]}...")
        print(f"  Status: PENDING")

        drafted += 1
        if drafted >= max_drafts:
            break

    print(f"\n{'='*60}")
    print(f"Generated {drafted} new drafts. Total pending: {sum(1 for d in drafts if d['status'] == 'pending')}")
    print(f"Run with --review to review and send.")


def run_review():
    """Interactive review of pending drafts."""
    drafts = load_drafts()
    pending = [d for d in drafts if d["status"] == "pending"]

    if not pending:
        print("No pending drafts to review.")
        return

    print(f"\n{len(pending)} pending drafts to review.\n")

    for i, draft in enumerate(pending):
        print(f"{'='*60}")
        print(f"Draft {i+1}/{len(pending)}")
        print(f"{'='*60}")
        print(f"To: {draft['lead_name']} <{draft['lead_email']}> | {draft.get('lead_company', '')}")
        print(f"Subject: {draft['subject']}")
        print(f"Campaign: {draft.get('campaign_id', '?')}")
        print(f"\nTHEIR REPLY:")
        print(f"  {draft['their_reply'][:500]}")
        print(f"\nDRAFT RESPONSE:")
        print(f"  {draft['draft_response']}")
        print()

        while True:
            action = input("[S]end / [E]dit / [R]egenerate / [D]elete / [N]ext / [Q]uit? ").strip().lower()

            if action == "s":
                # Send the draft
                try:
                    to_emails = [{"name": draft["lead_name"], "email_address": draft["lead_email"]}]
                    result = send_reply(
                        draft["reply_id"],
                        draft["draft_response"],
                        draft["sender_email_id"],
                        to_emails,
                    )
                    print(f"  SENT! {result.get('data', {}).get('message', '')}")
                    draft["status"] = "sent"
                    draft["sent_at"] = datetime.utcnow().isoformat()

                    log = load_sent_log()
                    log.append(draft)
                    save_sent_log(log)
                except Exception as e:
                    print(f"  ERROR sending: {e}")
                break

            elif action == "e":
                print("Enter new reply (end with an empty line):")
                lines = []
                while True:
                    line = input()
                    if line == "":
                        break
                    lines.append(line)
                draft["draft_response"] = "\n".join(lines)
                print("\nUpdated draft:")
                print(f"  {draft['draft_response']}")
                print()
                continue

            elif action == "r":
                print("Regenerating...")
                lead = fetch_lead(draft["lead_id"]) if draft["lead_id"] else {}
                campaign = fetch_campaign(draft["campaign_id"]) if draft["campaign_id"] else {}
                thread = fetch_thread(draft["reply_id"])
                new_draft, err = generate_draft({"from_email_address": draft["lead_email"]}, lead, campaign, thread)
                if err:
                    print(f"  Error: {err}")
                else:
                    draft["draft_response"] = new_draft
                    print(f"\nNew draft:")
                    print(f"  {new_draft}")
                    print()
                continue

            elif action == "d":
                draft["status"] = "deleted"
                print("  Deleted.")
                break

            elif action == "n":
                break

            elif action == "q":
                save_drafts(drafts)
                print("Saved. Exiting.")
                return

        save_drafts(drafts)

    remaining = sum(1 for d in drafts if d["status"] == "pending")
    print(f"\nDone. {remaining} drafts still pending.")


def run_status():
    """Show status of all drafts."""
    drafts = load_drafts()
    from collections import Counter
    statuses = Counter(d["status"] for d in drafts)
    print(f"Drafts: {dict(statuses)}")
    pending = [d for d in drafts if d["status"] == "pending"]
    for d in pending:
        print(f"  [{d['reply_id']}] {d['lead_name']} <{d['lead_email']}> | {d['subject'][:40]}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="EmailBison Auto-Draft Reply Pipeline")
    parser.add_argument("--review", action="store_true", help="Review and send pending drafts")
    parser.add_argument("--status", action="store_true", help="Show draft status")
    parser.add_argument("--reply-id", type=int, help="Generate draft for a specific reply ID")
    parser.add_argument("--max-drafts", type=int, default=10, help="Max new drafts to generate")
    args = parser.parse_args()

    if args.review:
        run_review()
    elif args.status:
        run_status()
    else:
        run_fetch_and_draft(max_drafts=args.max_drafts, reply_id=args.reply_id)


if __name__ == "__main__":
    main()
