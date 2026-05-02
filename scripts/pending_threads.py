#!/usr/bin/env python3
"""
pending_threads.py — persistence layer for thread drafts that may need retry.

Why this exists:
  Pre-2026-05-01 the run-reddit-threads pipeline drafted a post inside one
  Claude session and submitted it via the reddit-agent MCP within the same
  session. If the MCP child process died mid-flow (e.g. flair-click step on
  r/AutoHotkey, 2026-05-01), the entire $4-24 of work was lost: the title +
  body lived only in the Claude transcript JSON, not in the DB. Subsequent
  pipeline runs regenerated everything from scratch.

  pending_threads is a durable holding pen. The shell wrapper writes a row
  here BEFORE attempting to submit, and the row's `status` tracks lifecycle:

      pending  - drafted, not yet submitted (or submit aborted before permalink)
      posted   - submit succeeded, posted_post_id + posted_permalink filled
      abandoned - too many failed retries, or permanent_block on the sub

  Recovery flow (next pipeline run): pick the oldest pending row for the
  project before generating a fresh draft.

Sub-commands (called from shell pipelines):
  create        Insert a draft row, print id
  mark-posted   status=posted, fill posted_post_id + posted_permalink
  mark-aborted  bump attempts, fill abort_reason / abort_stage; keep pending
  abandon       status=abandoned (e.g. sub got permanent_block)
  list-pending  print all pending rows for a project (or all if no project)

This file is intentionally NOT chflags-locked. New columns are easy to add
because the helpers use SQL fragments rather than ORM models.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

# scripts/ is on sys.path when called from skill/*.sh; ensure it works
# standalone too.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402


def _conn():
    return db.get_conn()


def create(
    *,
    project: str,
    subreddit: str,
    account: str,
    title: str,
    body: str,
    flair_target: Optional[str] = None,
    engagement_style: Optional[str] = None,
    topic_angle: Optional[str] = None,
    source_summary: Optional[str] = None,
    claude_session_id: Optional[str] = None,
    cost_usd: Optional[float] = None,
) -> int:
    conn = _conn()
    row = conn.execute(
        """
        INSERT INTO pending_threads
          (project_name, subreddit, our_account, title, body,
           flair_target, engagement_style, topic_angle, source_summary,
           claude_session_id, cost_usd, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, %s, 'pending')
        RETURNING id
        """,
        (project, subreddit, account, title, body,
         flair_target, engagement_style, topic_angle, source_summary,
         claude_session_id, cost_usd),
    ).fetchone()
    conn.commit()
    return int(row[0])


def mark_posted(*, pending_id: int, post_id: int, permalink: str) -> None:
    conn = _conn()
    conn.execute(
        """
        UPDATE pending_threads
        SET status='posted',
            posted_post_id=%s,
            posted_permalink=%s,
            updated_at=NOW()
        WHERE id=%s
        """,
        (post_id, permalink, pending_id),
    )
    conn.commit()


def mark_aborted(*, pending_id: int, abort_reason: str, abort_stage: Optional[str] = None) -> None:
    conn = _conn()
    conn.execute(
        """
        UPDATE pending_threads
        SET attempts = attempts + 1,
            last_attempt_at = NOW(),
            abort_reason = %s,
            abort_stage = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (abort_reason, abort_stage, pending_id),
    )
    conn.commit()


def abandon(*, pending_id: int, reason: str) -> None:
    conn = _conn()
    conn.execute(
        """
        UPDATE pending_threads
        SET status='abandoned',
            abort_reason=COALESCE(abort_reason, '') || E'\n[abandoned] ' || %s,
            updated_at=NOW()
        WHERE id=%s
        """,
        (reason, pending_id),
    )
    conn.commit()


def list_pending(project: Optional[str] = None) -> list[dict[str, Any]]:
    conn = _conn()
    if project:
        rows = conn.execute(
            """
            SELECT id, project_name, subreddit, our_account, title,
                   flair_target, engagement_style, attempts, abort_reason,
                   created_at, updated_at
            FROM pending_threads
            WHERE status='pending' AND project_name=%s
            ORDER BY created_at ASC
            """,
            (project,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, project_name, subreddit, our_account, title,
                   flair_target, engagement_style, attempts, abort_reason,
                   created_at, updated_at
            FROM pending_threads
            WHERE status='pending'
            ORDER BY created_at ASC
            """
        ).fetchall()
    cols = [
        "id", "project_name", "subreddit", "our_account", "title",
        "flair_target", "engagement_style", "attempts", "abort_reason",
        "created_at", "updated_at",
    ]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        # JSON-friendly serialization for timestamps
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
        d["updated_at"] = d["updated_at"].isoformat() if d["updated_at"] else None
        out.append(d)
    return out


def get(pending_id: int) -> Optional[dict[str, Any]]:
    conn = _conn()
    row = conn.execute(
        """
        SELECT id, project_name, subreddit, our_account, title, body,
               flair_target, engagement_style, topic_angle, source_summary,
               abort_reason, abort_stage, status, attempts, posted_post_id,
               posted_permalink, claude_session_id, cost_usd,
               created_at, updated_at
        FROM pending_threads WHERE id=%s
        """,
        (pending_id,),
    ).fetchone()
    if not row:
        return None
    cols = [
        "id", "project_name", "subreddit", "our_account", "title", "body",
        "flair_target", "engagement_style", "topic_angle", "source_summary",
        "abort_reason", "abort_stage", "status", "attempts", "posted_post_id",
        "posted_permalink", "claude_session_id", "cost_usd",
        "created_at", "updated_at",
    ]
    d = dict(zip(cols, row))
    for k in ("created_at", "updated_at"):
        if d[k]:
            d[k] = d[k].isoformat()
    if d.get("claude_session_id"):
        d["claude_session_id"] = str(d["claude_session_id"])
    if d.get("cost_usd") is not None:
        d["cost_usd"] = float(d["cost_usd"])
    return d


def main() -> int:
    p = argparse.ArgumentParser(description="pending_threads helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("create")
    pc.add_argument("--project", required=True)
    pc.add_argument("--subreddit", required=True)
    pc.add_argument("--account", required=True)
    pc.add_argument("--title", required=True)
    pc.add_argument("--body", required=True)
    pc.add_argument("--flair-target")
    pc.add_argument("--engagement-style")
    pc.add_argument("--topic-angle")
    pc.add_argument("--source-summary")
    pc.add_argument("--claude-session-id")
    pc.add_argument("--cost-usd", type=float)

    pp = sub.add_parser("mark-posted")
    pp.add_argument("--id", required=True, type=int)
    pp.add_argument("--post-id", required=True, type=int)
    pp.add_argument("--permalink", required=True)

    pa = sub.add_parser("mark-aborted")
    pa.add_argument("--id", required=True, type=int)
    pa.add_argument("--abort-reason", required=True)
    pa.add_argument("--abort-stage")

    pab = sub.add_parser("abandon")
    pab.add_argument("--id", required=True, type=int)
    pab.add_argument("--reason", required=True)

    pl = sub.add_parser("list-pending")
    pl.add_argument("--project")

    pg = sub.add_parser("get")
    pg.add_argument("--id", required=True, type=int)

    args = p.parse_args()

    if args.cmd == "create":
        i = create(
            project=args.project,
            subreddit=args.subreddit,
            account=args.account,
            title=args.title,
            body=args.body,
            flair_target=args.flair_target,
            engagement_style=args.engagement_style,
            topic_angle=args.topic_angle,
            source_summary=args.source_summary,
            claude_session_id=args.claude_session_id,
            cost_usd=args.cost_usd,
        )
        print(json.dumps({"ok": True, "id": i}))
    elif args.cmd == "mark-posted":
        mark_posted(pending_id=args.id, post_id=args.post_id, permalink=args.permalink)
        print(json.dumps({"ok": True}))
    elif args.cmd == "mark-aborted":
        mark_aborted(pending_id=args.id, abort_reason=args.abort_reason, abort_stage=args.abort_stage)
        print(json.dumps({"ok": True}))
    elif args.cmd == "abandon":
        abandon(pending_id=args.id, reason=args.reason)
        print(json.dumps({"ok": True}))
    elif args.cmd == "list-pending":
        rows = list_pending(args.project)
        print(json.dumps(rows, indent=2))
    elif args.cmd == "get":
        rec = get(args.id)
        print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
