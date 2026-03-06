"""MCP server exposing social-autoposter capabilities as tools."""

import json
import os
import sqlite3
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

from .db import get_connection, rows_to_dicts

mcp = FastMCP("social-autoposter")


# ── Read Tools ──────────────────────────────────────────────


@mcp.tool()
def get_posts(
    platform: str | None = None,
    status: str | None = None,
    since_hours: int | None = None,
    limit: int = 20,
) -> str:
    """Get posts from the database with optional filters.

    Args:
        platform: Filter by platform (reddit, x, linkedin, moltbook)
        status: Filter by status (active, inactive, deleted, removed)
        since_hours: Only posts from the last N hours
        limit: Max rows to return (default 20)
    """
    conn = get_connection()
    conditions = []
    params: list = []

    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if since_hours:
        cutoff = (datetime.utcnow() - timedelta(hours=since_hours)).isoformat()
        conditions.append("posted_at >= ?")
        params.append(cutoff)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    rows = conn.execute(
        f"SELECT id, platform, thread_title, our_content, our_url, our_account, "
        f"posted_at, status, upvotes, comments_count, views, source_summary "
        f"FROM posts {where} ORDER BY posted_at DESC LIMIT ?",
        params,
    ).fetchall()
    conn.close()
    return json.dumps(rows_to_dicts(rows), indent=2, default=str)


@mcp.tool()
def get_stats(platform: str | None = None) -> str:
    """Get engagement stats summary across all posts.

    Args:
        platform: Filter by platform, or None for all
    """
    conn = get_connection()
    platform_filter = "WHERE platform = ?" if platform else ""
    params = [platform] if platform else []

    summary = conn.execute(
        f"""SELECT
            platform,
            COUNT(*) as total_posts,
            SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active,
            SUM(CASE WHEN status='deleted' THEN 1 ELSE 0 END) as deleted,
            SUM(CASE WHEN status='removed' THEN 1 ELSE 0 END) as removed,
            SUM(upvotes) as total_upvotes,
            AVG(upvotes) as avg_upvotes,
            MAX(upvotes) as max_upvotes,
            SUM(views) as total_views,
            SUM(comments_count) as total_comments
        FROM posts {platform_filter}
        GROUP BY platform
        ORDER BY total_upvotes DESC""",
        params,
    ).fetchall()

    top_posts = conn.execute(
        f"""SELECT id, platform, thread_title, our_content, our_url,
            upvotes, views, comments_count, posted_at
        FROM posts {platform_filter}
        ORDER BY COALESCE(upvotes, 0) DESC LIMIT 5""",
        params,
    ).fetchall()

    recent_24h = conn.execute(
        "SELECT COUNT(*) as count FROM posts WHERE posted_at >= datetime('now', '-24 hours')"
    ).fetchone()

    conn.close()
    return json.dumps(
        {
            "by_platform": rows_to_dicts(summary),
            "top_posts": rows_to_dicts(top_posts),
            "posts_last_24h": dict(recent_24h)["count"],
        },
        indent=2,
        default=str,
    )


@mcp.tool()
def get_replies(
    status: str | None = None,
    platform: str | None = None,
    limit: int = 20,
) -> str:
    """Get replies to our posts.

    Args:
        status: Filter by reply status (pending, replied, skipped, error)
        platform: Filter by platform
        limit: Max rows to return
    """
    conn = get_connection()
    conditions = []
    params: list = []

    if status:
        conditions.append("r.status = ?")
        params.append(status)
    if platform:
        conditions.append("r.platform = ?")
        params.append(platform)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    rows = conn.execute(
        f"""SELECT r.id, r.platform, r.their_author, r.their_content,
            r.their_comment_url, r.our_reply_content, r.our_reply_url,
            r.status, r.skip_reason, r.depth, r.discovered_at, r.replied_at,
            p.thread_title, p.our_content as original_post
        FROM replies r
        LEFT JOIN posts p ON r.post_id = p.id
        {where}
        ORDER BY r.discovered_at DESC LIMIT ?""",
        params,
    ).fetchall()
    conn.close()
    return json.dumps(rows_to_dicts(rows), indent=2, default=str)


@mcp.tool()
def search_posts(query: str, limit: int = 10) -> str:
    """Full-text search across post content, thread titles, and summaries.

    Args:
        query: Search term
        limit: Max results
    """
    conn = get_connection()
    like = f"%{query}%"
    rows = conn.execute(
        """SELECT id, platform, thread_title, our_content, our_url,
            upvotes, status, posted_at, source_summary
        FROM posts
        WHERE our_content LIKE ? OR thread_title LIKE ? OR source_summary LIKE ?
        ORDER BY posted_at DESC LIMIT ?""",
        [like, like, like, limit],
    ).fetchall()
    conn.close()
    return json.dumps(rows_to_dicts(rows), indent=2, default=str)


@mcp.tool()
def get_post_history(post_id: int) -> str:
    """Get full details for a specific post including all replies.

    Args:
        post_id: The post ID
    """
    conn = get_connection()
    post = conn.execute("SELECT * FROM posts WHERE id = ?", [post_id]).fetchone()
    if not post:
        conn.close()
        return json.dumps({"error": f"Post {post_id} not found"})

    replies = conn.execute(
        """SELECT id, their_author, their_content, their_comment_url,
            our_reply_content, our_reply_url, status, skip_reason, depth,
            discovered_at, replied_at
        FROM replies WHERE post_id = ? ORDER BY discovered_at""",
        [post_id],
    ).fetchall()
    conn.close()

    return json.dumps(
        {"post": dict(post), "replies": rows_to_dicts(replies)},
        indent=2,
        default=str,
    )


# ── Write Tools ─────────────────────────────────────────────


@mcp.tool()
def log_post(
    platform: str,
    thread_url: str,
    our_content: str,
    our_account: str,
    our_url: str | None = None,
    thread_title: str | None = None,
    thread_author: str | None = None,
    thread_author_handle: str | None = None,
    thread_content: str | None = None,
    source_summary: str | None = None,
) -> str:
    """Log a new post to the database. Call this after posting via browser/API.

    Args:
        platform: reddit, x, linkedin, or moltbook
        thread_url: URL of the thread we commented on
        our_content: Text of our comment
        our_account: Account handle used
        our_url: URL of our posted comment (if available)
        thread_title: Title of the thread
        thread_author: Thread author name
        thread_author_handle: Thread author handle
        thread_content: Thread body text
        source_summary: What prompted this post
    """
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
            thread_title, thread_content, our_url, our_content, our_account,
            source_summary, status, posted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', datetime('now'))""",
        [
            platform, thread_url, thread_author, thread_author_handle,
            thread_title, thread_content, our_url, our_content, our_account,
            source_summary,
        ],
    )
    post_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return json.dumps({"success": True, "post_id": post_id})


@mcp.tool()
def check_rate_limit(platform: str | None = None) -> str:
    """Check if we're within posting rate limits.

    Args:
        platform: Check a specific platform, or None for overall
    """
    conn = get_connection()
    results = {}

    # Overall: max 4 posts per 24h
    overall = conn.execute(
        "SELECT COUNT(*) as count FROM posts WHERE posted_at >= datetime('now', '-24 hours')"
    ).fetchone()
    results["posts_last_24h"] = dict(overall)["count"]
    results["daily_limit"] = 4
    results["can_post"] = results["posts_last_24h"] < 4

    if platform == "moltbook":
        mb = conn.execute(
            "SELECT COUNT(*) as count FROM posts WHERE platform='moltbook' AND posted_at >= datetime('now', '-30 minutes')"
        ).fetchone()
        results["moltbook_last_30min"] = dict(mb)["count"]
        results["can_post"] = results["can_post"] and results["moltbook_last_30min"] < 1

    # Check what we already posted to avoid dupes
    recent_urls = conn.execute(
        "SELECT thread_url FROM posts WHERE posted_at >= datetime('now', '-7 days')"
    ).fetchall()
    results["recent_thread_urls"] = [dict(r)["thread_url"] for r in recent_urls]

    conn.close()
    return json.dumps(results, indent=2, default=str)


@mcp.tool()
def update_post_status(
    post_id: int,
    status: str | None = None,
    upvotes: int | None = None,
    comments_count: int | None = None,
    views: int | None = None,
) -> str:
    """Update status or engagement metrics for a post.

    Args:
        post_id: The post ID to update
        status: New status (active, inactive, deleted, removed)
        upvotes: Updated upvote count
        comments_count: Updated comment count
        views: Updated view count
    """
    conn = get_connection()
    sets = ["status_checked_at = datetime('now')"]
    params: list = []

    if status:
        sets.append("status = ?")
        params.append(status)
    if upvotes is not None:
        sets.append("upvotes = ?")
        params.append(upvotes)
    if comments_count is not None:
        sets.append("comments_count = ?")
        params.append(comments_count)
    if views is not None:
        sets.append("views = ?")
        params.append(views)

    if upvotes is not None or comments_count is not None or views is not None:
        sets.append("engagement_updated_at = datetime('now')")

    params.append(post_id)
    conn.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return json.dumps({"success": True, "post_id": post_id})


# ── Resources ───────────────────────────────────────────────


@mcp.resource("social://schema")
def get_schema() -> str:
    """Return the database schema for reference."""
    schema_path = os.path.expanduser("~/social-autoposter/schema.sql")
    if os.path.exists(schema_path):
        with open(schema_path) as f:
            return f.read()
    return "Schema file not found"


@mcp.resource("social://config")
def get_config() -> str:
    """Return the current config (with secrets redacted)."""
    config_path = os.path.expanduser("~/social-autoposter/config.json")
    if not os.path.exists(config_path):
        return json.dumps({"error": "config.json not found"})
    with open(config_path) as f:
        cfg = json.load(f)
    # Redact API keys
    if "accounts" in cfg:
        for acct in cfg["accounts"].values():
            if "api_key_env" in acct:
                acct["api_key"] = "[REDACTED]"
    return json.dumps(cfg, indent=2)


@mcp.resource("social://content-rules")
def get_content_rules() -> str:
    """Return the content rules / style guide for posting."""
    return """Content Rules:
1. Write like you're texting a coworker. Lowercase fine. Sentence fragments fine.
2. First person, specific. Use concrete numbers and real experiences.
3. Reply to top comments, not just OP.
4. Only comment when you have a real angle from your own work.
5. No self-promotion unless it directly solves OP's problem.
6. Add a relevant link at the end when it fits naturally.
7. Comment on existing threads (exception: Moltbook).
8. On Moltbook, write as an agent ("my human" not "I").
9. Log everything to the database."""


def main():
    mcp.run()


if __name__ == "__main__":
    main()
