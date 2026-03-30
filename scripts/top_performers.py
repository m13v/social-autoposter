#!/usr/bin/env python3
"""Generate a feedback report from top/bottom performing posts.

Queries NeonDB for engagement data and outputs a structured report
that Claude reads before drafting new comments. This is the
self-improvement feedback loop.

Usage:
    python3 scripts/top_performers.py [--platform reddit|twitter|linkedin|moltbook]
    python3 scripts/top_performers.py --platform reddit --limit 10
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def get_top_posts(conn, platform=None, limit=10):
    """Get top performing posts by upvotes."""
    where = "WHERE status = 'active' AND upvotes IS NOT NULL AND upvotes > 0"
    params = []
    if platform:
        where += " AND platform = %s"
        params.append(platform)
    cur = conn.execute(
        f"SELECT id, platform, upvotes, comments_count, views, "
        f"our_content, thread_title, project_name, "
        f"LENGTH(our_content) as content_len, posted_at::date "
        f"FROM posts {where} "
        f"ORDER BY upvotes DESC LIMIT %s",
        params + [limit]
    )
    return cur.fetchall()


def get_bottom_posts(conn, platform=None, limit=5):
    """Get worst performing posts (negative or zero upvotes)."""
    where = "WHERE status = 'active' AND upvotes IS NOT NULL AND upvotes < 1"
    params = []
    if platform:
        where += " AND platform = %s"
        params.append(platform)
    cur = conn.execute(
        f"SELECT id, platform, upvotes, comments_count, "
        f"our_content, thread_title, project_name, "
        f"LENGTH(our_content) as content_len, posted_at::date "
        f"FROM posts {where} "
        f"ORDER BY upvotes ASC LIMIT %s",
        params + [limit]
    )
    return cur.fetchall()


def get_platform_stats(conn):
    """Get aggregate stats per platform."""
    cur = conn.execute(
        "SELECT platform, COUNT(*), "
        "AVG(COALESCE(upvotes,0))::numeric(10,1) as avg_up, "
        "AVG(COALESCE(comments_count,0))::numeric(10,1) as avg_comments, "
        "MAX(upvotes) as max_up, "
        "AVG(LENGTH(our_content))::int as avg_len "
        "FROM posts WHERE status = 'active' AND platform NOT IN ('github_issues','hackernews','dev','youtube','github') "
        "GROUP BY platform ORDER BY avg_up DESC"
    )
    return cur.fetchall()


def get_top_subreddits(conn, min_posts=3, limit=10):
    """Get subreddits ranked by avg upvotes."""
    cur = conn.execute(
        "SELECT "
        "  CASE WHEN thread_url LIKE '%%/r/%%' "
        "    THEN SPLIT_PART(SPLIT_PART(thread_url, '/r/', 2), '/', 1) "
        "    ELSE 'unknown' END as subreddit, "
        "COUNT(*) as cnt, "
        "AVG(COALESCE(upvotes,0))::numeric(10,1) as avg_up, "
        "MAX(upvotes) as max_up "
        "FROM posts "
        "WHERE platform = 'reddit' AND status = 'active' AND upvotes IS NOT NULL "
        "GROUP BY subreddit HAVING COUNT(*) >= %s "
        "ORDER BY avg_up DESC LIMIT %s",
        [min_posts, limit]
    )
    return cur.fetchall()


def get_content_length_analysis(conn, platform=None):
    """Analyze performance by content length buckets."""
    where = "WHERE status = 'active' AND upvotes IS NOT NULL AND our_content IS NOT NULL AND LENGTH(our_content) > 0"
    params = []
    if platform:
        where += " AND platform = %s"
        params.append(platform)
    cur = conn.execute(
        f"SELECT "
        f"  CASE "
        f"    WHEN LENGTH(our_content) < 100 THEN 'short (<100 chars)' "
        f"    WHEN LENGTH(our_content) < 250 THEN 'medium (100-250 chars)' "
        f"    WHEN LENGTH(our_content) < 500 THEN 'long (250-500 chars)' "
        f"    ELSE 'very long (500+ chars)' END as bucket, "
        f"  COUNT(*) as cnt, "
        f"  AVG(COALESCE(upvotes,0))::numeric(10,1) as avg_up, "
        f"  MAX(upvotes) as max_up "
        f"FROM posts {where} "
        f"GROUP BY bucket ORDER BY avg_up DESC",
        params
    )
    return cur.fetchall()


def get_project_performance(conn):
    """Get performance by project_name."""
    cur = conn.execute(
        "SELECT COALESCE(project_name, '(no project)') as proj, COUNT(*), "
        "AVG(COALESCE(upvotes,0))::numeric(10,1) as avg_up, "
        "MAX(upvotes) as max_up "
        "FROM posts WHERE status = 'active' AND upvotes IS NOT NULL "
        "AND platform NOT IN ('github_issues') "
        "GROUP BY project_name HAVING COUNT(*) >= 3 "
        "ORDER BY avg_up DESC"
    )
    return cur.fetchall()


def format_report(top, bottom, platform_stats, top_subs, length_analysis, project_perf, platform=None):
    """Format everything into a concise report for Claude."""
    lines = []
    scope = f" ({platform})" if platform else ""
    lines.append(f"## Top Performers Report{scope}")
    lines.append("")

    # Platform stats
    lines.append("### Platform Averages")
    for row in platform_stats:
        lines.append(f"- {row[0]}: {row[1]} posts, avg {row[2]} upvotes, avg {row[3]} comments, max {row[4]}, avg length {row[5]} chars")
    lines.append("")

    # Content length
    lines.append("### What Length Works Best")
    for row in length_analysis:
        lines.append(f"- {row[0]}: {row[1]} posts, avg {row[2]} upvotes, max {row[3]}")
    lines.append("")

    # Top subreddits
    if top_subs:
        lines.append("### Best Subreddits")
        for row in top_subs:
            lines.append(f"- r/{row[0]}: {row[1]} posts, avg {row[2]} upvotes, max {row[3]}")
        lines.append("")

    # Project performance
    if project_perf:
        lines.append("### Project Performance")
        for row in project_perf:
            lines.append(f"- {row[0]}: {row[1]} posts, avg {row[2]} upvotes, max {row[3]}")
        lines.append("")

    # Top posts with content
    lines.append("### Top 10 Posts (learn from these)")
    for row in top:
        content = row[5] or "(empty)"
        # Truncate long content
        if len(content) > 300:
            content = content[:300] + "..."
        lines.append(f"- [{row[2]} upvotes, {row[1]}, {row[8]} chars] thread: \"{row[6] or '(none)'}\"")
        lines.append(f"  content: {content}")
    lines.append("")

    # Bottom posts
    lines.append("### Bottom 5 Posts (avoid these patterns)")
    for row in bottom:
        content = row[4] or "(empty)"
        if len(content) > 200:
            content = content[:200] + "..."
        lines.append(f"- [{row[2]} upvotes, {row[1]}, {row[7]} chars] thread: \"{row[5] or '(none)'}\"")
        lines.append(f"  content: {content}")
    lines.append("")

    # Synthesized rules
    lines.append("### Patterns to Follow")

    # Analyze top posts for patterns
    top_lengths = [row[8] for row in top if row[8] and row[8] > 0]
    avg_top_len = sum(top_lengths) / len(top_lengths) if top_lengths else 200
    bottom_lengths = [row[7] for row in bottom if row[7] and row[7] > 0]
    avg_bottom_len = sum(bottom_lengths) / len(bottom_lengths) if bottom_lengths else 300

    lines.append(f"- Top posts average {int(avg_top_len)} chars, bottom posts average {int(avg_bottom_len)} chars")

    # Check if top posts mention products less
    top_with_links = sum(1 for row in top if row[5] and ('http' in (row[5] or '') or '.ai' in (row[5] or '') or '.com' in (row[5] or '')))
    bottom_with_links = sum(1 for row in bottom if row[4] and ('http' in (row[4] or '') or '.ai' in (row[4] or '') or '.com' in (row[4] or '')))
    if top:
        lines.append(f"- {top_with_links}/{len(top)} top posts contain links vs {bottom_with_links}/{len(bottom)} bottom posts")

    lines.append("- Write like texting a coworker, not writing a blog post")
    lines.append("- Lead with a specific personal experience or observation, not a general statement")
    lines.append("- Avoid 'been building in this space' or 'building a macOS app' without concrete details")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate top performers feedback report")
    parser.add_argument("--platform", default=None, help="Filter to specific platform")
    parser.add_argument("--limit", type=int, default=10, help="Number of top posts to show")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of text")
    args = parser.parse_args()

    conn = dbmod.get_conn()

    top = get_top_posts(conn, platform=args.platform, limit=args.limit)
    bottom = get_bottom_posts(conn, platform=args.platform, limit=5)
    platform_stats = get_platform_stats(conn)
    top_subs = get_top_subreddits(conn) if not args.platform or args.platform == "reddit" else []
    length_analysis = get_content_length_analysis(conn, platform=args.platform)
    project_perf = get_project_performance(conn)

    conn.close()

    if args.json:
        output = {
            "top_posts": [dict(row) for row in top],
            "bottom_posts": [dict(row) for row in bottom],
            "platform_stats": [dict(row) for row in platform_stats],
            "length_analysis": [dict(row) for row in length_analysis],
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        print(format_report(top, bottom, platform_stats, top_subs, length_analysis, project_perf, platform=args.platform))


if __name__ == "__main__":
    main()
