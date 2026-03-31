#!/usr/bin/env python3
"""LinkedIn API wrapper for Social Autoposter.

Replaces browser automation for posting, commenting, replying, and reacting.
Browser is still needed for discovery (notifications, search) since LinkedIn
has no content discovery API.

Usage:
    # Post a comment on a LinkedIn post
    python3 linkedin_api.py comment <activity_id> "comment text"

    # Reply to a comment on a LinkedIn post
    python3 linkedin_api.py reply <activity_id> <parent_comment_urn> "reply text"

    # Create a new post
    python3 linkedin_api.py post "post text"

    # Like a post
    python3 linkedin_api.py like <activity_id>

    # Get user profile info
    python3 linkedin_api.py whoami

Environment:
    LINKEDIN_ACCESS_TOKEN - OAuth 2.0 access token (w_member_social scope)
    LINKEDIN_PERSON_URN   - Optional. Auto-detected from token if not set.
"""

import json
import os
import sys
import urllib.parse

import requests


def get_env():
    """Load .env if needed and return token + person URN."""
    env_path = os.path.expanduser("~/social-autoposter/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k, v)

    token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
    if not token:
        print("ERROR: LINKEDIN_ACCESS_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    return token


def get_person_urn(token):
    """Get the authenticated user's person URN."""
    cached = os.environ.get("LINKEDIN_PERSON_URN")
    if cached:
        return cached
    r = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    sub = r.json()["sub"]
    return f"urn:li:person:{sub}"


def rest_headers(token):
    """Headers for /rest/ endpoints (versioned)."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202503",
    }


def v2_headers(token):
    """Headers for /v2/ endpoints (unversioned)."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }


def create_post(token, person_urn, text):
    """Create a new LinkedIn post. Returns the post URN."""
    data = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "visibility": "PUBLIC",
        "commentary": text,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
    }
    r = requests.post(
        "https://api.linkedin.com/rest/posts",
        headers=rest_headers(token),
        json=data,
    )
    if r.status_code == 201:
        post_urn = r.headers.get("x-restli-id", "")
        print(json.dumps({"ok": True, "post_urn": post_urn}))
        return post_urn
    else:
        print(json.dumps({"ok": False, "status": r.status_code, "error": r.text}))
        sys.exit(1)


def resolve_post_urn(identifier):
    """Convert an activity ID or share ID to the appropriate URN for API calls.

    The pipeline extracts activity IDs from browser data-urn attributes.
    LinkedIn's socialActions API accepts both urn:li:activity: and urn:li:share: URNs.
    """
    if identifier.startswith("urn:li:"):
        return identifier
    return f"urn:li:activity:{identifier}"


def extract_activity_id_from_response(resp, fallback_id):
    """Extract the real activity ID from a comment response's $URN field."""
    urn = resp.get("$URN", "")
    import re
    m = re.search(r"activity:(\d+)", urn)
    return m.group(1) if m else fallback_id


def comment_on_post(token, person_urn, activity_id, text):
    """Comment on a LinkedIn post. Returns the comment URN.

    Accepts activity IDs (from browser data-urn), share IDs, or full URNs.
    If urn:li:activity fails with 404, retries with urn:li:share.
    """
    post_urn = resolve_post_urn(activity_id)
    encoded_urn = urllib.parse.quote(post_urn, safe="")
    data = {
        "actor": person_urn,
        "message": {"text": text},
    }
    r = requests.post(
        f"https://api.linkedin.com/v2/socialActions/{encoded_urn}/comments",
        headers=v2_headers(token),
        json=data,
    )
    # If activity URN 404s, retry with share URN (post API returns share IDs)
    if r.status_code == 404 and not activity_id.startswith("urn:li:"):
        share_urn = f"urn:li:share:{activity_id}"
        encoded_share = urllib.parse.quote(share_urn, safe="")
        r = requests.post(
            f"https://api.linkedin.com/v2/socialActions/{encoded_share}/comments",
            headers=v2_headers(token),
            json=data,
        )
    if r.status_code == 201:
        resp = r.json()
        comment_id = resp.get("id", "")
        real_activity_id = extract_activity_id_from_response(resp, activity_id)
        comment_urn = f"urn:li:comment:(activity:{real_activity_id},{comment_id})"
        our_url = f"https://www.linkedin.com/feed/update/urn:li:activity:{real_activity_id}/"
        print(json.dumps({"ok": True, "comment_urn": comment_urn, "our_url": our_url, "activity_id": real_activity_id}))
        return comment_urn
    else:
        print(json.dumps({"ok": False, "status": r.status_code, "error": r.text}))
        sys.exit(1)


def reply_to_comment(token, person_urn, activity_id, parent_comment_urn, text):
    """Reply to a specific comment on a LinkedIn post."""
    post_urn = resolve_post_urn(activity_id)
    encoded_urn = urllib.parse.quote(post_urn, safe="")
    data = {
        "actor": person_urn,
        "message": {"text": text},
        "parentComment": parent_comment_urn,
    }
    r = requests.post(
        f"https://api.linkedin.com/v2/socialActions/{encoded_urn}/comments",
        headers=v2_headers(token),
        json=data,
    )
    if r.status_code == 201:
        resp = r.json()
        reply_id = resp.get("id", "")
        real_activity_id = extract_activity_id_from_response(resp, activity_id)
        reply_urn = f"urn:li:comment:(activity:{real_activity_id},{reply_id})"
        permalink = (
            f"https://www.linkedin.com/feed/update/urn:li:activity:{real_activity_id}"
            f"?commentUrn={urllib.parse.quote(reply_urn, safe='')}"
        )
        print(json.dumps({"ok": True, "reply_urn": reply_urn, "permalink": permalink}))
        return reply_urn
    else:
        print(json.dumps({"ok": False, "status": r.status_code, "error": r.text}))
        sys.exit(1)


def like_post(token, person_urn, activity_id):
    """Like/react to a LinkedIn post."""
    post_urn = resolve_post_urn(activity_id)
    encoded_urn = urllib.parse.quote(post_urn, safe="")
    data = {"actor": person_urn}
    r = requests.post(
        f"https://api.linkedin.com/v2/socialActions/{encoded_urn}/likes",
        headers=v2_headers(token),
        json=data,
    )
    if r.status_code == 201:
        print(json.dumps({"ok": True, "activity_id": activity_id}))
    else:
        print(json.dumps({"ok": False, "status": r.status_code, "error": r.text}))
        sys.exit(1)


def delete_post(token, post_urn):
    """Delete a LinkedIn post."""
    encoded = urllib.parse.quote(post_urn, safe="")
    r = requests.delete(
        f"https://api.linkedin.com/rest/posts/{encoded}",
        headers=rest_headers(token),
    )
    if r.status_code == 204:
        print(json.dumps({"ok": True, "deleted": post_urn}))
    else:
        print(json.dumps({"ok": False, "status": r.status_code, "error": r.text}))
        sys.exit(1)


def whoami(token):
    """Print authenticated user info."""
    r = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    info = r.json()
    print(json.dumps({"ok": True, "name": info.get("name"), "email": info.get("email"), "sub": info.get("sub")}))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    token = get_env()
    person_urn = get_person_urn(token)

    if cmd == "comment":
        if len(sys.argv) < 4:
            print("Usage: linkedin_api.py comment <activity_id> <text>", file=sys.stderr)
            sys.exit(1)
        comment_on_post(token, person_urn, sys.argv[2], sys.argv[3])

    elif cmd == "reply":
        if len(sys.argv) < 5:
            print("Usage: linkedin_api.py reply <activity_id> <parent_comment_urn> <text>", file=sys.stderr)
            sys.exit(1)
        reply_to_comment(token, person_urn, sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == "post":
        if len(sys.argv) < 3:
            print("Usage: linkedin_api.py post <text>", file=sys.stderr)
            sys.exit(1)
        create_post(token, person_urn, sys.argv[2])

    elif cmd == "like":
        if len(sys.argv) < 3:
            print("Usage: linkedin_api.py like <activity_id>", file=sys.stderr)
            sys.exit(1)
        like_post(token, person_urn, sys.argv[2])

    elif cmd == "delete":
        if len(sys.argv) < 3:
            print("Usage: linkedin_api.py delete <post_urn>", file=sys.stderr)
            sys.exit(1)
        delete_post(token, sys.argv[2])

    elif cmd == "whoami":
        whoami(token)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
