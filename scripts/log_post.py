#!/usr/bin/env python3
"""Log a posted comment/reply to the database.

Single tool for all platforms. Enforces:
  - status='active' (always)
  - our_url must start with http (validated)
  - dedup on thread_url per platform

Usage:
    python3 scripts/log_post.py \\
        --platform reddit \\
        --thread-url URL \\
        --our-url URL \\
        --our-content TEXT \\
        --project PROJECT \\
        --thread-author AUTHOR \\
        --thread-title TITLE \\
        [--account ACCOUNT] \\
        [--engagement-style STYLE] \\
        [--language LANG]

Output (JSON):
    {"logged": true, "post_id": 12345}
    {"error": "DUPLICATE_THREAD", ...}
    {"error": "INVALID_URL", ...}
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

VALID_PLATFORMS = ("reddit", "twitter", "linkedin", "github_issues", "moltbook")

DEFAULT_ACCOUNTS = {
    "reddit": "Deep_Ad1959",
    "twitter": "m13v_",
    "linkedin": "Matthew Diakonov",
    "github_issues": "m13v",
    "moltbook": "matthew-autoposter",
}


def main():
    parser = argparse.ArgumentParser(description="Log a posted comment to the database")
    parser.add_argument("--platform", required=True, choices=VALID_PLATFORMS)
    parser.add_argument("--thread-url", required=True)
    parser.add_argument("--our-url", required=True,
                        help="Permalink to our posted comment (must start with http)")
    parser.add_argument("--our-content", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--thread-author", default="")
    parser.add_argument("--thread-title", default="")
    parser.add_argument("--account", default=None,
                        help="Override default account for the platform")
    parser.add_argument("--engagement-style", default=None)
    parser.add_argument("--language", default=None,
                        help="ISO 639-1 language code (e.g. en, ja, zh, es)")
    args = parser.parse_args()

    # Validate our_url
    if not args.our_url.startswith("http"):
        print(json.dumps({
            "error": "INVALID_URL",
            "message": f"our_url must start with http, got: {args.our_url[:50]}",
        }))
        sys.exit(1)

    account = args.account or DEFAULT_ACCOUNTS.get(args.platform, "")

    dbmod.load_env()
    conn = dbmod.get_conn()

    # Dedup: refuse if we already posted in this thread on this platform
    cur = conn.execute(
        "SELECT id, LEFT(our_content, 100) FROM posts "
        "WHERE platform = %s AND thread_url = %s LIMIT 1",
        [args.platform, args.thread_url],
    )
    existing = cur.fetchone()
    if existing:
        conn.close()
        print(json.dumps({
            "error": "DUPLICATE_THREAD",
            "message": "Already posted in this thread",
            "existing_post_id": existing[0],
            "content_preview": existing[1],
        }))
        return

    claude_session_id = os.environ.get("CLAUDE_SESSION_ID") or None

    cur = conn.execute(
        """INSERT INTO posts (
            platform, thread_url, thread_author, thread_author_handle,
            thread_title, thread_content, our_url, our_content, our_account,
            source_summary, project_name, status, posted_at,
            feedback_report_used, engagement_style, language, claude_session_id
        ) VALUES (
            %s, %s, %s, %s,
            %s, '', %s, %s, %s,
            '', %s, 'active', NOW(),
            TRUE, %s, %s, %s
        ) RETURNING id""",
        [
            args.platform, args.thread_url, args.thread_author, args.thread_author,
            args.thread_title, args.our_url, args.our_content, account,
            args.project, args.engagement_style, args.language, claude_session_id,
        ],
    )
    row = cur.fetchone()
    post_id = row[0] if row else None
    conn.commit()
    conn.close()
    print(json.dumps({"logged": True, "post_id": post_id}))


if __name__ == "__main__":
    main()
