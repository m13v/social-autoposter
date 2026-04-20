#!/usr/bin/env python3
"""Backfill dms.target_project for historical rows where it is NULL.

Uses the same precedence as scan_dm_candidates.py:
  1. Inherit from the originating post's project_name.
  2. Fall back to topic-phrase overlap against thread_title + their_content +
     our_reply_content + thread_content, using config.json per-platform topic fields.

Usage:
    python3 scripts/backfill_target_project.py --dry-run
    python3 scripts/backfill_target_project.py --platform linkedin
    python3 scripts/backfill_target_project.py                 # all platforms, commit
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod
from scan_dm_candidates import (
    load_config,
    build_project_topic_index,
    infer_target_project,
)


def fetch_rows(conn, platform=None):
    q = """
        SELECT d.id, d.platform, d.their_author,
               p.project_name AS post_project,
               p.thread_title, p.thread_content,
               r.their_content, r.our_reply_content
        FROM dms d
        LEFT JOIN replies r ON d.reply_id = r.id
        LEFT JOIN posts p   ON d.post_id  = p.id
        WHERE d.target_project IS NULL
          AND d.message_count > 1
    """
    params = []
    if platform in ("x", "twitter"):
        q += " AND d.platform IN ('x','twitter')"
    elif platform:
        q += " AND d.platform = %s"
        params.append(platform)
    q += " ORDER BY d.platform, d.id"
    return conn.execute(q, params).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["reddit", "linkedin", "twitter", "x"], default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dbmod.load_env()
    conn = dbmod.get_conn()
    config = load_config()

    # Per-platform topic indices (reddit uses "topics", linkedin/x layer their own first).
    indices = {
        "reddit":   build_project_topic_index(config, "reddit"),
        "linkedin": build_project_topic_index(config, "linkedin"),
        "x":        build_project_topic_index(config, "x"),
        "twitter":  build_project_topic_index(config, "x"),
    }

    rows = fetch_rows(conn, platform=args.platform)
    print(f"Rows with target_project IS NULL and message_count>1: {len(rows)}")
    if not rows:
        return

    counts = {"via_post": 0, "via_topic": 0, "unresolved": 0}
    updates = []
    for row in rows:
        target = row["post_project"]
        source = "post"
        if not target:
            idx = indices.get(row["platform"], [])
            target = infer_target_project(
                [row["thread_title"], row["thread_content"],
                 row["their_content"], row["our_reply_content"]],
                idx,
            )
            source = "topic" if target else None

        if not target:
            counts["unresolved"] += 1
            continue
        counts[f"via_{source}"] += 1
        updates.append((row["id"], target, source, row["platform"], row["their_author"]))

    for dm_id, target, source, platform, author in updates:
        print(f"  DM #{dm_id} [{platform}] {author} -> {target}  ({source})")

    print("\n=== Summary ===")
    print(f"  via post.project_name : {counts['via_post']}")
    print(f"  via topic match       : {counts['via_topic']}")
    print(f"  unresolved            : {counts['unresolved']}")
    print(f"  total to update       : {len(updates)}")

    if args.dry_run:
        print("\n(dry-run, no writes)")
        return

    for dm_id, target, _source, _p, _a in updates:
        conn.execute("UPDATE dms SET target_project=%s WHERE id=%s", (target, dm_id))
    conn.commit()
    print(f"\nCommitted {len(updates)} updates.")


if __name__ == "__main__":
    main()
