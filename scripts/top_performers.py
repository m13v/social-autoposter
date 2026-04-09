#!/usr/bin/env python3
"""Generate a feedback report from top/bottom performing posts.

Queries NeonDB for engagement data and outputs a factual report
organized by project and platform. This is the self-improvement
feedback loop — Claude reads this before drafting new comments.

Usage:
    python3 scripts/top_performers.py
    python3 scripts/top_performers.py --platform reddit
    python3 scripts/top_performers.py --project Fazm
    python3 scripts/top_performers.py --project Fazm --platform reddit
    python3 scripts/top_performers.py --top 20
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

MIN_CONTENT_LEN = 30  # skip posts with empty/placeholder content
MIN_UPVOTES = 10  # only show posts with meaningful engagement


def get_project_platform_summary(conn, project=None, platform=None):
    """Post count and avg upvotes per project per platform.

    When project/platform are given, show:
    - The filtered project across all platforms (for cross-platform context)
    - The filtered platform across all projects (for competitive context)
    """
    where_clauses = [
        "status = 'active'",
        "platform NOT IN ('github_issues','hackernews','dev','youtube','github')",
        "our_content IS NOT NULL",
        f"LENGTH(our_content) >= {MIN_CONTENT_LEN}",
    ]

    if project and platform:
        # Show this project on all platforms + this platform for all projects
        filter_sql = f"(COALESCE(project_name, '(no project)') = %s OR platform = %s)"
        params = [project, platform]
    elif project:
        filter_sql = "COALESCE(project_name, '(no project)') = %s"
        params = [project]
    elif platform:
        filter_sql = "platform = %s"
        params = [platform]
    else:
        filter_sql = None
        params = []

    if filter_sql:
        where_clauses.append(filter_sql)

    where = " AND ".join(where_clauses)
    cur = conn.execute(
        f"SELECT COALESCE(project_name, '(no project)') as proj, platform, "
        f"COUNT(*) as cnt, "
        f"AVG(COALESCE(upvotes,0))::numeric(10,1) as avg_up, "
        f"MAX(upvotes) as max_up "
        f"FROM posts WHERE {where} "
        f"GROUP BY project_name, platform ORDER BY proj, avg_up DESC",
        params
    )
    return cur.fetchall()


def get_top_posts(conn, project=None, platform=None, limit=15, min_upvotes=None):
    """Top performing posts with full factual details.

    Only returns posts with >= min_upvotes (default MIN_UPVOTES).
    For Twitter, also considers views as the primary reach metric
    (a post with 3K views and 0 likes still reached people).
    If project is given and has no posts meeting the threshold,
    returns None so the caller can fall back to general posts.
    """
    if min_upvotes is None:
        min_upvotes = MIN_UPVOTES

    # For Twitter, use a combined score: views are primary, likes are secondary
    if platform == "twitter":
        where_clauses = [
            "status = 'active'",
            "our_content IS NOT NULL",
            f"LENGTH(our_content) >= {MIN_CONTENT_LEN}",
            "platform = 'twitter'",
            f"(COALESCE(views, 0) >= 200 OR COALESCE(upvotes, 0) >= {min_upvotes})",
        ]
        order_by = "COALESCE(views, 0) + COALESCE(upvotes, 0) * 50 DESC"
    else:
        where_clauses = [
            "status = 'active'",
            "upvotes IS NOT NULL",
            f"upvotes >= {min_upvotes}",
            "our_content IS NOT NULL",
            f"LENGTH(our_content) >= {MIN_CONTENT_LEN}",
            "platform NOT IN ('github_issues')",
        ]
        order_by = "upvotes DESC"

    params = []
    if project:
        where_clauses.append("project_name = %s")
        params.append(project)
    if platform and platform != "twitter":
        where_clauses.append("platform = %s")
        params.append(platform)

    where = " AND ".join(where_clauses)
    cur = conn.execute(
        f"SELECT id, platform, upvotes, comments_count, views, "
        f"our_content, thread_title, thread_content, "
        f"project_name, posted_at::date, our_account "
        f"FROM posts WHERE {where} "
        f"ORDER BY {order_by} LIMIT %s",
        params + [limit]
    )
    return cur.fetchall()


def get_bottom_posts(conn, project=None, platform=None, limit=10):
    """Worst performing posts.

    For Twitter, uses views as the failure signal (< 20 views = nobody saw it).
    For other platforms, uses upvotes < 1.
    """
    if platform == "twitter":
        where_clauses = [
            "status = 'active'",
            "platform = 'twitter'",
            "our_content IS NOT NULL",
            f"LENGTH(our_content) >= {MIN_CONTENT_LEN}",
            "COALESCE(views, 0) < 20",
            "COALESCE(upvotes, 0) < 1",
            "posted_at < NOW() - INTERVAL '3 days'",
        ]
        order_by = "COALESCE(views, 0) ASC"
    else:
        where_clauses = [
            "status = 'active'",
            "upvotes IS NOT NULL",
            "upvotes < 1",
            "our_content IS NOT NULL",
            f"LENGTH(our_content) >= {MIN_CONTENT_LEN}",
            "platform NOT IN ('github_issues')",
        ]
        order_by = "upvotes ASC"

    params = []
    if project:
        where_clauses.append("project_name = %s")
        params.append(project)
    if platform and platform != "twitter":
        where_clauses.append("platform = %s")
        params.append(platform)

    where = " AND ".join(where_clauses)
    cur = conn.execute(
        f"SELECT id, platform, upvotes, comments_count, views, "
        f"our_content, thread_title, thread_content, "
        f"project_name, posted_at::date, our_account "
        f"FROM posts WHERE {where} "
        f"ORDER BY {order_by} LIMIT %s",
        params + [limit]
    )
    return cur.fetchall()


def format_post(row, include_thread_content=True):
    """Format a single post as factual text."""
    lines = []
    upvotes = row[2] if row[2] is not None else 0
    comments = row[3] if row[3] is not None else 0
    views = row[4] if row[4] is not None else 0
    our_content = row[5] or ""
    thread_title = row[6] or ""
    thread_content = row[7] or ""
    project = row[8] or "(no project)"
    date = row[9]
    account = row[10] or ""

    header = f"[{upvotes} upvotes, {comments} comments, {views} views] {row[1]} | {project} | {date}"
    lines.append(header)

    if thread_title:
        lines.append(f"  Thread: {thread_title[:150]}")
    if include_thread_content and thread_content:
        snippet = thread_content[:200].replace('\n', ' ')
        lines.append(f"  Thread body: {snippet}")
    lines.append(f"  Our comment: {our_content[:400]}")
    return "\n".join(lines)


def format_report(summary, top, bottom, project=None, platform=None,
                   top_by_group=None, fallback_top=None):
    """Format the full report."""
    lines = []
    filters = []
    if project:
        filters.append(f"project={project}")
    if platform:
        filters.append(f"platform={platform}")
    scope = f" ({', '.join(filters)})" if filters else ""
    lines.append(f"## Performance Feedback Report{scope}")
    lines.append("")

    # Summary table
    lines.append("### Posts per Project per Platform")
    for row in summary:
        lines.append(f"  {row[0]:<20} {row[1]:<12} {row[2]:>5} posts  avg_upvotes={row[3]}  best={row[4]}")
    lines.append("")

    # Per-project top performers (when no project filter)
    if top_by_group:
        lines.append(f"### Top Posts by Project (>= {MIN_UPVOTES} upvotes)")
        for group_name, posts in top_by_group.items():
            if not posts:
                continue
            lines.append(f"\n#### {group_name}")
            for p in posts:
                lines.append(format_post(p))
                lines.append("")
    elif top:
        # Filtered view with results
        lines.append(f"### Top {len(top)} Posts for {project or 'all projects'} (>= {MIN_UPVOTES} upvotes)")
        for p in top:
            lines.append(format_post(p))
            lines.append("")
    elif fallback_top:
        # No project-specific posts met threshold — show general high performers
        platform_label = f" on {platform}" if platform else ""
        lines.append(f"### No {project} posts with >= {MIN_UPVOTES} upvotes{platform_label}.")
        lines.append(f"### Showing top posts from OTHER projects{platform_label} as reference:")
        lines.append("")
        for p in fallback_top:
            lines.append(format_post(p))
            lines.append("")

    # Bottom posts
    if bottom:
        lines.append(f"### Bottom {len(bottom)} Posts (avoid these patterns)")
        for p in bottom:
            lines.append(format_post(p, include_thread_content=False))
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate top performers feedback report")
    parser.add_argument("--platform", default=None, help="Filter to specific platform")
    parser.add_argument("--project", default=None, help="Filter to specific project")
    parser.add_argument("--top", type=int, default=15, help="Number of top posts to show (per group or total)")
    parser.add_argument("--bottom", type=int, default=10, help="Number of bottom posts to show")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    conn = dbmod.get_conn()

    summary = get_project_platform_summary(conn, project=args.project, platform=args.platform)
    top = get_top_posts(conn, project=args.project, platform=args.platform, limit=args.top)
    bottom = get_bottom_posts(conn, project=args.project, platform=args.platform, limit=args.bottom)

    # If project was specified but no posts met the threshold, fall back to
    # general high-performing posts on the same platform
    fallback_top = None
    if args.project and not top:
        fallback_top = get_top_posts(conn, project=None, platform=args.platform, limit=args.top)

    # When no project filter, also get top 5 per project for focused examples
    top_by_group = None
    if not args.project:
        top_by_group = {}
        platform_filter = "AND platform = %s" if args.platform else ""
        platform_params = [args.platform] if args.platform else []
        # Get distinct projects (respecting platform filter)
        cur = conn.execute(
            f"SELECT DISTINCT COALESCE(project_name, '(no project)') FROM posts "
            f"WHERE status = 'active' AND platform NOT IN ('github_issues') "
            f"AND our_content IS NOT NULL AND LENGTH(our_content) >= %s "
            f"AND upvotes IS NOT NULL AND upvotes >= %s "
            f"{platform_filter} "
            f"ORDER BY 1",
            [MIN_CONTENT_LEN, MIN_UPVOTES] + platform_params
        )
        projects = [row[0] for row in cur.fetchall()]
        for proj in projects:
            proj_filter = proj if proj != "(no project)" else None
            where_extra = "AND project_name = %s" if proj_filter else "AND project_name IS NULL"
            params = ([proj_filter] if proj_filter else []) + platform_params
            cur = conn.execute(
                f"SELECT id, platform, upvotes, comments_count, views, "
                f"our_content, thread_title, thread_content, "
                f"project_name, posted_at::date, our_account "
                f"FROM posts WHERE status = 'active' AND upvotes >= {MIN_UPVOTES} "
                f"AND our_content IS NOT NULL AND LENGTH(our_content) >= {MIN_CONTENT_LEN} "
                f"AND platform NOT IN ('github_issues') "
                f"{where_extra} {platform_filter} "
                f"ORDER BY upvotes DESC LIMIT 5",
                params
            )
            top_by_group[proj] = cur.fetchall()

    conn.close()

    if args.json:
        output = {
            "summary": [dict(row) for row in summary],
            "top_posts": [dict(row) for row in top],
            "bottom_posts": [dict(row) for row in bottom],
            "fallback_top": [dict(row) for row in fallback_top] if fallback_top else [],
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        print(format_report(summary, top, bottom,
                            project=args.project, platform=args.platform,
                            top_by_group=top_by_group, fallback_top=fallback_top))


if __name__ == "__main__":
    main()
