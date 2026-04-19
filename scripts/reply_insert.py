#!/usr/bin/env python3
"""Shared reply-insertion helpers for scan_reddit_replies.py and scan_moltbook_replies.py.

`insert_reply` returns the status string on insert, or None if the row already existed.
Callers use the return value to update their discovered/skipped counters.
"""


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

    db.execute(
        """INSERT INTO replies
        (post_id, platform, their_comment_id, their_author, their_content, their_comment_url,
         parent_reply_id, depth, status, skip_reason, moltbook_post_uuid, moltbook_parent_comment_uuid,
         our_reply_id, our_reply_content, our_reply_url, replied_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (post_id, platform, comment_id, author, content, comment_url,
         parent_reply_id, depth, status, skip_reason, moltbook_post_uuid, moltbook_parent_comment_uuid,
         our_reply_id, our_reply_content, our_reply_url, replied_at),
    )
    db.commit()
    return status
