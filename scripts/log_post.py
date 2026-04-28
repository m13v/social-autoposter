#!/usr/bin/env python3
"""Log a posted comment/reply to the database.

Single tool for all platforms. Enforces:
  - status='active' for successful posts
  - our_url must start with http for successful posts (validated)
  - dedup on thread_url per platform

Usage (INSERT — default mode):
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

Usage (REJECTED — record a server-rejected attempt):
    python3 scripts/log_post.py --rejected \\
        --platform linkedin \\
        --thread-url URL \\
        --our-content TEXT \\
        --project PROJECT \\
        [--rejection-reason TEXT] \\
        [--network-response TEXT]

    Inserts with status='rejected_by_platform'. Skips our_url validation
    (no permalink exists). Counts toward dedup so we don't retry the same
    thread. rejection-reason and network-response go into source_summary.

Usage (UPDATE — record a self-reply / link follow-up on an existing post):
    python3 scripts/log_post.py --mark-self-reply \\
        --post-id 12345 \\
        --self-reply-url URL \\
        --self-reply-content TEXT

    Writes to posts.link_edited_at / link_edit_content so the
    link-edit-* sweeps skip this row on the next pass.

Output (JSON):
    {"logged": true, "post_id": 12345}
    {"rejected": true, "post_id": 12345}
    {"marked": true, "post_id": 12345}
    {"error": "DUPLICATE_THREAD", ...}
    {"error": "INVALID_URL", ...}
    {"error": "POST_NOT_FOUND", ...}
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod
import linkedin_url as li_url

URN_ID_RE = re.compile(r"\b(\d{16,19})\b")


def parse_urn_ids(*sources):
    """Extract all 16-19-digit URN IDs from the given strings, dedupe,
    preserve insertion order. Used to merge --urns CLI input with IDs
    found in thread_url / our_url so we always store the full URN set
    we know about for a LinkedIn post."""
    seen = []
    for s in sources:
        if not s:
            continue
        for m in URN_ID_RE.finditer(s):
            v = m.group(1)
            if v not in seen:
                seen.append(v)
    return seen

VALID_PLATFORMS = ("reddit", "twitter", "linkedin", "github_issues", "moltbook")

DEFAULT_ACCOUNTS = {
    "reddit": "Deep_Ad1959",
    "twitter": "m13v_",
    "linkedin": "Matthew Diakonov",
    "github_issues": "m13v",
    "moltbook": "matthew-autoposter",
}


def mark_self_reply(args):
    if args.post_id is None or not args.self_reply_url or args.self_reply_content is None:
        print(json.dumps({
            "error": "MISSING_ARGS",
            "message": "--mark-self-reply requires --post-id, --self-reply-url, --self-reply-content",
        }))
        sys.exit(1)
    if not args.self_reply_url.startswith("http"):
        print(json.dumps({
            "error": "INVALID_URL",
            "message": f"self-reply-url must start with http, got: {args.self_reply_url[:50]}",
        }))
        sys.exit(1)

    dbmod.load_env()
    conn = dbmod.get_conn()
    cur = conn.execute(
        "UPDATE posts SET link_edited_at=NOW(), link_edit_content=%s "
        "WHERE id=%s RETURNING id",
        [f"{args.self_reply_content} {args.self_reply_url}".strip(), args.post_id],
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        print(json.dumps({"error": "POST_NOT_FOUND", "post_id": args.post_id}))
        sys.exit(1)
    conn.commit()
    conn.close()
    print(json.dumps({"marked": True, "post_id": args.post_id}))


def log_rejected(args):
    """Record a comment attempt that the platform rejected server-side.

    Writes status='rejected_by_platform' so dedup blocks retries on the same
    thread, and stashes the rejection reason + network response in
    source_summary for diagnostics.
    """
    missing = [f for f in ("platform", "thread_url", "our_content", "project")
               if getattr(args, f) is None]
    if missing:
        print(json.dumps({
            "error": "MISSING_ARGS",
            "message": f"--rejected requires: {', '.join('--' + m.replace('_', '-') for m in missing)}",
        }))
        sys.exit(1)

    account = args.account or DEFAULT_ACCOUNTS.get(args.platform, "")

    summary_parts = []
    if args.rejection_reason:
        summary_parts.append(f"REASON: {args.rejection_reason}")
    if args.network_response:
        # Cap to keep row size sane — we only need the response shape.
        summary_parts.append(f"NETWORK: {args.network_response[:4000]}")
    summary = "\n".join(summary_parts) if summary_parts else "rejected_by_platform"

    dbmod.load_env()
    conn = dbmod.get_conn()

    cur = conn.execute(
        "SELECT id, status FROM posts "
        "WHERE platform = %s AND thread_url = %s LIMIT 1",
        [args.platform, args.thread_url],
    )
    existing = cur.fetchone()
    if existing:
        conn.close()
        print(json.dumps({
            "error": "DUPLICATE_THREAD",
            "message": "Already have a row for this thread",
            "existing_post_id": existing[0],
            "existing_status": existing[1],
        }))
        return

    claude_session_id = os.environ.get("CLAUDE_SESSION_ID") or None

    cur = conn.execute(
        """INSERT INTO posts (
            platform, thread_url, thread_author, thread_author_handle,
            thread_title, thread_content, our_url, our_content, our_account,
            source_summary, project_name, status, posted_at,
            feedback_report_used, engagement_style, is_recommendation,
            language, claude_session_id
        ) VALUES (
            %s, %s, %s, %s,
            %s, '', '', %s, %s,
            %s, %s, 'rejected_by_platform', NOW(),
            FALSE, %s, FALSE,
            %s, %s
        ) RETURNING id""",
        [
            args.platform, args.thread_url, args.thread_author, args.thread_author,
            args.thread_title, args.our_content, account,
            summary, args.project, args.engagement_style,
            args.language, claude_session_id,
        ],
    )
    row = cur.fetchone()
    post_id = row[0] if row else None
    conn.commit()
    conn.close()
    print(json.dumps({"rejected": True, "post_id": post_id}))


def main():
    parser = argparse.ArgumentParser(description="Log a posted comment to the database")
    parser.add_argument("--mark-self-reply", action="store_true",
                        help="UPDATE mode: mark link_edited_at on an existing post. "
                             "Requires --post-id, --self-reply-url, --self-reply-content.")
    parser.add_argument("--rejected", action="store_true",
                        help="REJECTED mode: record a server-rejected attempt with "
                             "status='rejected_by_platform'. Skips our_url validation. "
                             "Use when the platform silently swallowed the comment.")
    parser.add_argument("--rejection-reason", default=None,
                        help="Brief reason text (e.g. 'TOAST: comment could not be created'). "
                             "Goes into source_summary.")
    parser.add_argument("--network-response", default=None,
                        help="Captured XHR response from the comment-create endpoint. "
                             "Goes into source_summary (truncated to 4000 chars).")
    parser.add_argument("--post-id", type=int, default=None,
                        help="posts.id to update (only with --mark-self-reply)")
    parser.add_argument("--self-reply-url", default=None,
                        help="URL of the self-reply that carries the project link")
    parser.add_argument("--self-reply-content", default=None,
                        help="Text of the self-reply (goes into link_edit_content)")
    parser.add_argument("--platform", choices=VALID_PLATFORMS)
    parser.add_argument("--thread-url")
    parser.add_argument("--our-url",
                        help="Permalink to our posted comment (must start with http)")
    parser.add_argument("--our-content")
    parser.add_argument("--project")
    parser.add_argument("--thread-author", default="")
    parser.add_argument("--thread-title", default="")
    parser.add_argument("--account", default=None,
                        help="Override default account for the platform")
    parser.add_argument("--engagement-style", default=None,
                        help="Tone style (e.g. critic, storyteller). Separate from "
                             "--is-recommendation, which is intent.")
    parser.add_argument("--is-recommendation", action="store_true",
                        help="Mark this post as a project mention/recommendation. "
                             "Composes with --engagement-style; tone and intent are "
                             "independent dimensions.")
    parser.add_argument("--language", default=None,
                        help="ISO 639-1 language code (e.g. en, ja, zh, es)")
    parser.add_argument("--urns", default=None,
                        help="LinkedIn-only: comma- or whitespace-separated list "
                             "of 16-19 digit URN IDs that identify this post "
                             "(activity, ugcPost, share). Pass everything you "
                             "captured from the createComment network response. "
                             "log_post.py merges these with IDs extracted from "
                             "thread_url and our_url before INSERT, so dedup "
                             "via posts.urns catches future cross-URN collisions.")
    args = parser.parse_args()

    if args.mark_self_reply:
        mark_self_reply(args)
        return

    if args.rejected:
        log_rejected(args)
        return

    # INSERT mode — enforce required fields that argparse can't conditionally require.
    missing = [f for f in ("platform", "thread_url", "our_url", "our_content", "project")
               if getattr(args, f) is None]
    if missing:
        print(json.dumps({
            "error": "MISSING_ARGS",
            "message": f"INSERT mode requires: {', '.join('--' + m.replace('_', '-') for m in missing)}",
        }))
        sys.exit(1)

    # Validate our_url
    if not args.our_url.startswith("http"):
        print(json.dumps({
            "error": "INVALID_URL",
            "message": f"our_url must start with http, got: {args.our_url[:50]}",
        }))
        sys.exit(1)

    account = args.account or DEFAULT_ACCOUNTS.get(args.platform, "")

    # LinkedIn: same post surfaces under multiple URL shapes (/feed/update/
    # vs /posts/...-share-...) with different numeric URNs. Canonicalize
    # our_url to /feed/update/urn:li:activity:<id>/ so the comment-permalink
    # captured after posting drops its commentUrn query string.
    urn_ids = []
    if args.platform == "linkedin":
        args.our_url = li_url.canonicalize(args.our_url)
        # Build the full URN-ID set for this post: --urns input plus
        # everything we can extract from thread_url and our_url. Stored in
        # posts.urns so future dedup queries catch any URN form (activity,
        # ugcPost, share) regardless of which one the candidate-page DOM
        # renders. Without this, the search-page only exposes the ugcPost
        # URN while we stored only the activity URN, so the cross-URN
        # collision check missed and we double-posted.
        urn_ids = parse_urn_ids(args.urns, args.thread_url, args.our_url)

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
            feedback_report_used, engagement_style, is_recommendation,
            language, claude_session_id
        ) VALUES (
            %s, %s, %s, %s,
            %s, '', %s, %s, %s,
            '', %s, 'active', NOW(),
            TRUE, %s, %s,
            %s, %s
        ) RETURNING id""",
        [
            args.platform, args.thread_url, args.thread_author, args.thread_author,
            args.thread_title, args.our_url, args.our_content, account,
            args.project, args.engagement_style, bool(args.is_recommendation),
            args.language, claude_session_id,
        ],
    )
    row = cur.fetchone()
    post_id = row[0] if row else None
    conn.commit()
    conn.close()
    print(json.dumps({"logged": True, "post_id": post_id}))


if __name__ == "__main__":
    main()
