#!/usr/bin/env python3
"""Test script to read data from EmailBison API."""

import requests
import json
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

API_KEY = os.environ["EMAILBISON_API_KEY"]
BASE_URL = os.environ["EMAILBISON_BASE_URL"]

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def api_get(endpoint, params=None):
    url = f"{BASE_URL}/api/{endpoint}"
    print(f"\nGET {url}")
    if params:
        print(f"  params: {params}")
    resp = requests.get(url, headers=HEADERS, params=params)
    print(f"  status: {resp.status_code}")
    try:
        data = resp.json()
        print(json.dumps(data, indent=2)[:2000])
        return data
    except Exception:
        print(f"  body: {resp.text[:500]}")
        return None


def main():
    print("=" * 60)
    print("EmailBison API Read Tests")
    print(f"Base URL: {BASE_URL}")
    print("=" * 60)

    # 1. Test auth with /users
    print("\n--- 1. Users (auth check) ---")
    api_get("users")

    # 2. List campaigns
    print("\n--- 2. Campaigns ---")
    api_get("campaigns")

    # 3. List leads
    print("\n--- 3. Leads ---")
    api_get("leads")

    # 4. List leads with pagination
    print("\n--- 4. Leads (page 1, 5 per page) ---")
    api_get("leads", {"per_page": 5, "page": 1})

    # 5. List sender emails
    print("\n--- 5. Sender Emails ---")
    api_get("sender-emails")

    # 6. List tags
    print("\n--- 6. Tags ---")
    api_get("tags")

    # 7. List workspaces/workspace info
    print("\n--- 7. Workspace ---")
    api_get("workspace")


if __name__ == "__main__":
    main()
