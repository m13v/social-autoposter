#!/usr/bin/env python3
"""Scan dms table for click-driven follow-up candidates.

INTENT
------
When a DM recipient clicks the booking short link we sent them, that's a
high-fidelity intent signal. The existing pipeline (scan_dm_candidates.py
+ engage-dm-replies.sh) does NOT read short_link_clicks at all, so a
strong lead can sit in `conversation_status='active'` indefinitely with
zero follow-up just because they didn't reply to the last DM.

This scanner finds DMs where the recipient clicked our short link
recently AND we haven't followed up about that click yet, then prints a
JSON candidate list for the click-followup-<platform>.sh driver to act
on.

ELIGIBILITY (per platform)
--------------------------
- dms.status = 'sent'                     -> message actually landed
- dms.short_link_clicks > 0               -> they clicked at least once
- dms.short_link_last_click_at >= NOW() - INTERVAL '7 days'
                                          -> click is recent enough to nudge on
- (last_click_followup_at IS NULL
   OR short_link_last_click_at > last_click_followup_at)
                                          -> there's been a NEW click since
                                             our last click-driven follow-up
- dms.conversation_status NOT IN ('closed', 'converted', 'needs_human')
                                          -> don't touch closed/won/escalated
- dms.booking_link_sent_at IS NULL OR
  short_link_last_click_at > sent_at + INTERVAL '15 minutes'
                                          -> avoid pinging on the click that
                                             happened in the same minute we
                                             sent the link
- author NOT in config.exclusions.authors
- platform matches --platform (or all)

DOES NOT TOUCH:
- status='skipped' rows (permanent skip; surface separately for manual review)
- status='error' / 'pending' rows
- DMs whose target_project is missing
- conversations in 'needs_human' (operator already taking over)

OUTPUT
------
Prints a JSON array of candidate dicts to stdout, one per DM:
  {
    "dm_id": int,
    "platform": "reddit"|"twitter"|"linkedin",
    "their_author": str,
    "target_project": str,
    "chat_url": str | None,
    "short_link_clicks": int,
    "short_link_first_click_at": iso str,
    "short_link_last_click_at": iso str,
    "tier": int,
    "mode": str,
    "qualification_status": str,
    "interest_level": str | None,
    "message_count": int,
    "our_first_dm": str (their_content + our most recent outbound),
    "their_last_msg": str | None,
    "our_last_msg": str | None,
    "click_recency_hours": float,
    "days_since_last_message": float | None,
  }

Summary stats (counts by platform, would-skip reasons) go to stderr.

Usage:
    python3 scripts/scan_click_followups.py [--platform reddit|twitter|linkedin|all] [--max N] [--dry-run]

--dry-run prints the candidate list AND a human-readable summary, then
exits 0. The driver shell scripts always run without --dry-run (the
output JSON is the same either way; --dry-run is a hint for callers but
the script is read-only regardless).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")

CLICK_RECENCY_DAYS = 7
SAME_SEND_GUARD_MIN = 15  # minutes after sent_at before a click counts
DEFAULT_MAX = 50
PLATFORMS = ("reddit", "twitter", "linkedin")
PLATFORM_DB_NORMAL = {"x": "twitter", "twitter": "twitter", "reddit": "reddit", "linkedin": "linkedin"}


def load_excluded_authors():
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return set((cfg.get("exclusions", {}) or {}).get("authors", []) or [])
    except Exception as e:
        print(f"[scan_click_followups] config load error: {e}", file=sys.stderr)
        return set()


def fetch_candidates(db, platform_filter, max_n):
    where_platform = "AND d.platform = %s" if platform_filter else ""
    params = []
    if platform_filter:
        params.append(platform_filter)
    params.extend([CLICK_RECENCY_DAYS, SAME_SEND_GUARD_MIN, max_n])

    sql = f"""
        SELECT
            d.id                        AS dm_id,
            d.platform                  AS platform,
            d.their_author              AS their_author,
            COALESCE(d.target_project, d.project_name) AS target_project,
            d.chat_url                  AS chat_url,
            d.short_link_clicks         AS short_link_clicks,
            d.short_link_first_click_at AS short_link_first_click_at,
            d.short_link_last_click_at  AS short_link_last_click_at,
            d.short_link_target_url     AS short_link_target_url,
            d.tier                      AS tier,
            d.mode                      AS mode,
            d.qualification_status      AS qualification_status,
            d.interest_level            AS interest_level,
            d.message_count             AS message_count,
            d.last_message_at           AS last_message_at,
            d.sent_at                   AS sent_at,
            d.last_click_followup_at    AS last_click_followup_at,
            d.their_content             AS their_first_content,
            d.comment_context           AS comment_context,
            d.our_dm_content            AS our_first_dm
        FROM dms d
        WHERE d.status = 'sent'
          {where_platform}
          AND d.short_link_clicks > 0
          AND d.short_link_last_click_at IS NOT NULL
          AND d.short_link_last_click_at >= NOW() - (%s || ' days')::interval
          AND (
                d.last_click_followup_at IS NULL
                OR d.short_link_last_click_at > d.last_click_followup_at
              )
          AND COALESCE(d.conversation_status, 'active') NOT IN ('closed', 'converted', 'needs_human')
          AND COALESCE(d.target_project, d.project_name) IS NOT NULL
          AND (
                d.sent_at IS NULL
                OR d.short_link_last_click_at >= d.sent_at + (%s || ' minutes')::interval
              )
        ORDER BY d.short_link_clicks DESC, d.short_link_last_click_at DESC
        LIMIT %s
    """
    cur = db.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def fetch_recent_messages(db, dm_id):
    """Return up to 6 most recent dm_messages rows for prompt context."""
    cur = db.execute(
        """
        SELECT direction, content, message_at
          FROM dm_messages
         WHERE dm_id = %s
         ORDER BY message_at DESC
         LIMIT 6
        """,
        (dm_id,),
    )
    rows = cur.fetchall()
    cur.close()
    # rows are DictRow; turn into list of plain dicts in chronological order
    out = []
    for r in reversed(rows):
        out.append({
            "direction": r["direction"],
            "content": (r["content"] or ""),
            "message_at": r["message_at"].isoformat() if r["message_at"] else None,
        })
    return out


def hours_since(ts):
    if not ts:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def main():
    ap = argparse.ArgumentParser(description="Find DMs whose recipient clicked our short link and need a click-driven follow-up.")
    ap.add_argument("--platform", default="all", choices=("all",) + PLATFORMS + ("x",),
                    help="Restrict to one platform. 'x' is normalized to 'twitter'.")
    ap.add_argument("--max", type=int, default=DEFAULT_MAX,
                    help=f"Cap candidates returned (default {DEFAULT_MAX}).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print a human summary in addition to the JSON.")
    args = ap.parse_args()

    pf = None
    if args.platform != "all":
        pf = PLATFORM_DB_NORMAL.get(args.platform, args.platform)

    db = dbmod.get_conn()
    excluded = load_excluded_authors()

    rows = fetch_candidates(db, pf, args.max)

    out = []
    skipped_excluded = 0
    by_platform = {p: 0 for p in PLATFORMS}

    for r in rows:
        if r["their_author"] in excluded:
            skipped_excluded += 1
            continue

        platform = r["platform"]
        if platform == "x":
            platform = "twitter"

        msgs = fetch_recent_messages(db, r["dm_id"])
        their_last = next((m for m in reversed(msgs) if m["direction"] == "inbound"), None)
        our_last = next((m for m in reversed(msgs) if m["direction"] == "outbound"), None)

        cand = {
            "dm_id": r["dm_id"],
            "platform": platform,
            "their_author": r["their_author"],
            "target_project": r["target_project"],
            "chat_url": r["chat_url"],
            "short_link_clicks": r["short_link_clicks"],
            "short_link_first_click_at": r["short_link_first_click_at"].isoformat() if r["short_link_first_click_at"] else None,
            "short_link_last_click_at": r["short_link_last_click_at"].isoformat() if r["short_link_last_click_at"] else None,
            "short_link_target_url": r["short_link_target_url"],
            "tier": r["tier"],
            "mode": r["mode"],
            "qualification_status": r["qualification_status"],
            "interest_level": r["interest_level"],
            "message_count": r["message_count"],
            "their_first_content": (r["their_first_content"] or ""),
            "comment_context": (r["comment_context"] or ""),
            "our_first_dm": (r["our_first_dm"] or ""),
            "their_last_msg": their_last,
            "our_last_msg": our_last,
            "recent_messages": msgs,
            "click_recency_hours": round(hours_since(r["short_link_last_click_at"]) or 0.0, 1),
            "hours_since_last_message": round(hours_since(r["last_message_at"]) or 0.0, 1) if r["last_message_at"] else None,
            "last_click_followup_at": r["last_click_followup_at"].isoformat() if r["last_click_followup_at"] else None,
        }
        out.append(cand)
        if platform in by_platform:
            by_platform[platform] += 1

    db.close()

    print(json.dumps(out, indent=2))

    summary_lines = [
        f"[scan_click_followups] candidates={len(out)} (max={args.max}, platform={args.platform})",
        f"  by platform: " + ", ".join(f"{p}={n}" for p, n in by_platform.items()),
    ]
    if skipped_excluded:
        summary_lines.append(f"  skipped (excluded authors): {skipped_excluded}")
    print("\n".join(summary_lines), file=sys.stderr)

    if args.dry_run and out:
        print("\n--- DRY RUN: candidates ---", file=sys.stderr)
        for c in out:
            print(
                f"  dm_id={c['dm_id']:5d} {c['platform']:8s} u/{c['their_author']:25s} "
                f"clicks={c['short_link_clicks']:2d} last_click={c['short_link_last_click_at']} "
                f"target={c['target_project']} tier={c['tier']} qstat={c['qualification_status']} "
                f"prev_followup={c['last_click_followup_at'] or '-'}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
