#!/usr/bin/env python3
"""Shared reply-insertion helpers for scan_reddit_replies.py and scan_moltbook_replies.py.

`insert_reply` returns the status string on insert, or None if the row already existed.
Callers use the return value to update their discovered/skipped counters.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def already_tracked(db, platform, comment_id):
    row = db.execute(
        "SELECT COUNT(*) FROM replies WHERE platform=%s AND their_comment_id=%s",
        (platform, str(comment_id)),
    ).fetchone()
    return row[0] > 0


def insert_reply(
    db,
    post_id,
    platform,
    comment_id,
    author,
    content,
    comment_url,
    parent_reply_id=None,
    depth=1,
    status="pending",
    skip_reason=None,
    moltbook_post_uuid=None,
    moltbook_parent_comment_uuid=None,
    our_reply_id=None,
    our_reply_content=None,
    our_reply_url=None,
    replied_at=None,
):
    comment_id = str(comment_id)
    if already_tracked(db, platform, comment_id):
        return None

    from http_api import api_post
    body = {
        "platform": platform,
        "their_comment_id": comment_id,
        "status": status,
    }
    if post_id is not None:
        body["post_id"] = post_id
    if author is not None:
        body["their_author"] = author
    if content is not None:
        body["their_content"] = content
    if comment_url is not None:
        body["their_comment_url"] = comment_url
    if parent_reply_id is not None:
        body["parent_reply_id"] = parent_reply_id
    if depth != 1:
        body["depth"] = depth
    if skip_reason is not None:
        body["skip_reason"] = skip_reason
    if moltbook_post_uuid is not None:
        body["moltbook_post_uuid"] = moltbook_post_uuid
    if moltbook_parent_comment_uuid is not None:
        body["moltbook_parent_comment_uuid"] = moltbook_parent_comment_uuid
    if our_reply_id is not None:
        body["our_reply_id"] = our_reply_id
    if our_reply_content is not None:
        body["our_reply_content"] = our_reply_content
    if our_reply_url is not None:
        body["our_reply_url"] = our_reply_url
    if replied_at is not None:
        body["replied_at"] = (
            replied_at.isoformat() if hasattr(replied_at, "isoformat") else str(replied_at)
        )

    resp = api_post("/api/v1/replies", body, ok_on_conflict=True)
    if resp is None or resp.get("error"):
        return None
    return status
