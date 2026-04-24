#!/usr/bin/env python3
"""
historical_engagement.py

Per-(project, engagement_style) median engagement from the posts table.
Returned as a compact markdown block to inject into posting prompts, so
Claude can see which patterns earn upvotes/comments vs. which are dead.

Used by run_moltbook_cycle.py and run_github_cycle.py for the feedback-loop
reduction lever: stop drafting for patterns whose median engagement is 0
over >=5 past posts.

Usage:
    python3 scripts/historical_engagement.py --platform moltbook
    python3 scripts/historical_engagement.py --platform github --lookback-days 14
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def fetch_per_project_style(platform, lookback_days=14, min_posts=3):
    dbmod.load_env()
    conn = dbmod.get_conn()
    rows = conn.execute(
        """
        SELECT
            COALESCE(project_name, '(none)') AS project,
            COALESCE(engagement_style, '(none)') AS style,
            COUNT(*) AS n,
            COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY COALESCE(upvotes, 0)), 0) AS median_up,
            COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY COALESCE(comments_count, 0)), 0) AS median_cm,
            COALESCE(MAX(upvotes), 0) AS max_up,
            COALESCE(MAX(comments_count), 0) AS max_cm
        FROM posts
        WHERE platform = %s
          AND posted_at >= NOW() - (%s || ' days')::interval
          AND engagement_updated_at IS NOT NULL
        GROUP BY project_name, engagement_style
        HAVING COUNT(*) >= %s
        ORDER BY median_up DESC, median_cm DESC
        """,
        [platform, str(lookback_days), min_posts],
    ).fetchall()
    conn.close()
    return rows


def render_block(rows, platform):
    if not rows:
        return (
            f"## Historical engagement (platform={platform})\n"
            f"(no scored posts in lookback window)\n"
        )

    lines = [
        f"## Historical engagement per (project, style) for {platform}",
        "Median engagement over posts with status tracked. Prioritize rows labeled [good];",
        "skip drafting for rows labeled [dead] unless the thread is an obvious on-topic fit.",
        "",
        f"{'project':<22} {'style':<20} {'n':>4} {'med_up':>7} {'med_cm':>7} {'best_up':>7} {'best_cm':>7}  label",
    ]
    for project, style, n, med_up, med_cm, max_up, max_cm in rows:
        med_up = float(med_up or 0)
        med_cm = float(med_cm or 0)
        # Self-upvote inflates med_up by 1 on platforms like MoltBook;
        # lean on max_up (organic high-water) and med_cm (replies) instead.
        if max_cm >= 2 or max_up >= 3 or med_cm >= 1:
            label = "[good]"
        elif max_up <= 1 and med_cm == 0 and n >= 5:
            label = "[dead]"
        else:
            label = ""
        lines.append(
            f"{project[:22]:<22} {style[:20]:<20} {n:>4} "
            f"{med_up:>7.2f} {med_cm:>7.2f} {max_up:>7} {max_cm:>7}  {label}"
        )
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--platform", required=True, choices=["moltbook", "github", "reddit", "twitter", "linkedin"])
    p.add_argument("--lookback-days", type=int, default=14)
    p.add_argument("--min-posts", type=int, default=3)
    args = p.parse_args()

    rows = fetch_per_project_style(args.platform, args.lookback_days, args.min_posts)
    sys.stdout.write(render_block(rows, args.platform))


if __name__ == "__main__":
    main()
