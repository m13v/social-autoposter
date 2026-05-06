#!/usr/bin/env python3
"""Update LinkedIn engagement stats from an activity-feed scrape.

Reads a JSON file produced by skill/stats-linkedin.sh (Claude-driven MCP
linkedin-agent) containing a list of post engagement readings extracted in
ONE DOM evaluate from /in/me/recent-activity/all/. Applies them to the
posts table, with the same scan_no_change_count freeze convention used by
update_stats.py for Twitter/Reddit/Moltbook.

Input JSON shape:
  [
    {
      "activity_id": "7438226125077549056",
      "url":         "https://www.linkedin.com/feed/update/urn:li:activity:7438226125077549056/",
      "reactions":   12,
      "comments":    3,
      "reposts":     0
    },
    ...
  ]

Freeze rule (mirrors Twitter):
  Skip refresh entirely if scan_no_change_count >= 3 AND posted_at older
  than 5 days. Reactions/comments on a LinkedIn post are essentially frozen
  by then; continuing to refresh just burns scrape budget.

Behavioral rules:
  - On match: if (upvotes, comments_count) unchanged from the input,
    increment scan_no_change_count. Else reset to 0 and write the new
    counts. Always set engagement_updated_at = NOW() when we look at the
    row (so an unchanged row's "last verified" stamp still advances).
  - Unmatched feed rows (URN not in our DB): logged, no DB write.
  - Unmatched DB rows (active, not frozen, not in feed): logged as
    'not_in_feed'. Do NOT bump scan_no_change_count just because the row
    fell off the feed page; the post might still exist, the feed is just
    paginated and this fire didn't scroll far enough. The next fire with
    a deeper scroll will catch it.

Usage:
  python3 scripts/update_linkedin_stats_from_feed.py \\
      --from-json /tmp/li-feed.json \\
      [--summary /tmp/li-summary.json] \\
      [--quiet]

Output (stdout): a single line in the format stats.sh's extract_field
parses for Twitter/Reddit:
  LinkedIn: <T> total, <S> skipped, <C> checked, <U> updated, <D> deleted, <E> errors
"""

import argparse
import json
import os
import re
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod  # noqa: E402


# Freeze rule constants. Keep in sync with the Twitter pattern in
# update_stats.py (3+ unchanged + 5d old). If the rule needs to change,
# update both spots; the post is mostly frozen by day 5-7 on LinkedIn so
# 5d is a slightly aggressive but reasonable lower bound.
FREEZE_NO_CHANGE_THRESHOLD = 3
FREEZE_AGE_DAYS = 5


def normalize_activity_id(value: str) -> Optional[str]:
    """Pull a bare activity numeric ID from any of:
      - 'urn:li:activity:7438226125077549056'
      - '/feed/update/urn:li:activity:7438226125077549056/'
      - 'https://www.linkedin.com/feed/update/urn:li:activity:.../?...'
      - bare '7438226125077549056'
    Returns None if no activity ID can be parsed.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = re.search(r"urn:li:activity:(\d+)", s)
    if m:
        return m.group(1)
    if s.isdigit():
        return s
    # Last resort: numeric run inside a URL path
    m = re.search(r"/(\d{15,})/?", s)
    if m:
        return m.group(1)
    return None


def load_feed(path: str) -> list:
    """Load + sanitize the feed JSON. Drops rows without an activity_id;
    coerces missing reactions/comments/reposts to 0."""
    with open(path) as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError(
            f"feed file must contain a JSON array, got {type(raw).__name__}"
        )
    cleaned = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        aid = normalize_activity_id(r.get("activity_id") or r.get("url") or "")
        if not aid:
            continue
        cleaned.append({
            "activity_id": aid,
            "url":       r.get("url"),
            "reactions": int(r.get("reactions") or 0),
            "comments":  int(r.get("comments")  or 0),
            "reposts":   int(r.get("reposts")   or 0),
        })
    return cleaned


def load_active_db_rows(db) -> dict:
    """Return {activity_id: {id, our_url, upvotes, comments_count,
    scan_no_change_count, posted_at_age_days, frozen}} for every active
    LinkedIn post.

    'frozen' is precomputed so we can SHORT-CIRCUIT before touching the
    DB on a row whose stats won't change anyway. The pipeline's whole
    point is to avoid burning browser surface area on already-settled
    posts.
    """
    cur = db.execute(
        "SELECT id, our_url, "
        "       COALESCE(upvotes, 0)              AS upvotes, "
        "       COALESCE(comments_count, 0)       AS comments_count, "
        "       COALESCE(scan_no_change_count, 0) AS scan_no_change_count, "
        "       posted_at, "
        "       EXTRACT(EPOCH FROM (NOW() - posted_at)) / 86400.0 AS age_days "
        "FROM posts "
        "WHERE platform='linkedin' "
        "  AND status='active' "
        "  AND our_url IS NOT NULL "
        "  AND our_url ~ 'urn:li:activity:'"
    )
    out = {}
    for r in cur.fetchall():
        aid_match = re.search(r"urn:li:activity:(\d+)", r["our_url"] or "")
        if not aid_match:
            continue
        aid = aid_match.group(1)
        age = float(r["age_days"] or 0.0)
        no_change = int(r["scan_no_change_count"] or 0)
        frozen = (
            no_change >= FREEZE_NO_CHANGE_THRESHOLD
            and age >= FREEZE_AGE_DAYS
        )
        out[aid] = {
            "id":                   r["id"],
            "our_url":              r["our_url"],
            "upvotes":              int(r["upvotes"] or 0),
            "comments_count":       int(r["comments_count"] or 0),
            "scan_no_change_count": no_change,
            "age_days":             age,
            "frozen":               frozen,
        }
    return out


def apply_one(db, db_row: dict, feed_row: dict, quiet: bool) -> str:
    """Apply one feed reading to one DB row. Returns 'updated', 'unchanged',
    or 'frozen-skip'."""
    if db_row["frozen"]:
        return "frozen-skip"

    new_reactions = feed_row["reactions"]
    new_comments  = feed_row["comments"]
    # We persist 'reposts' on the row's views column? No — schema has no
    # reposts field, only views. Reposts aren't directly stored; downstream
    # consumers pull them from a separate column if added. For now we only
    # write upvotes (reactions) and comments_count. Reposts captured in
    # feed JSON for future use without touching this writer.

    changed = (
        new_reactions != db_row["upvotes"]
        or new_comments != db_row["comments_count"]
    )

    if changed:
        db.execute(
            "UPDATE posts SET "
            "   upvotes = %s, "
            "   comments_count = %s, "
            "   engagement_updated_at = NOW(), "
            "   scan_no_change_count = 0 "
            "WHERE id = %s",
            [new_reactions, new_comments, db_row["id"]],
        )
        if not quiet:
            print(
                f"  [{db_row['id']}] updated "
                f"reactions {db_row['upvotes']}→{new_reactions} "
                f"comments {db_row['comments_count']}→{new_comments}",
                flush=True,
            )
        return "updated"
    else:
        db.execute(
            "UPDATE posts SET "
            "   engagement_updated_at = NOW(), "
            "   scan_no_change_count = COALESCE(scan_no_change_count, 0) + 1 "
            "WHERE id = %s",
            [db_row["id"]],
        )
        return "unchanged"


def run(from_json: str, summary_path: Optional[str], quiet: bool) -> dict:
    feed = load_feed(from_json)
    if not feed:
        return {
            "ok": True,
            "total": 0, "skipped": 0, "checked": 0,
            "updated": 0, "deleted": 0, "errors": 0,
            "note": "empty_feed",
        }

    dbmod.load_env()
    db = dbmod.get_conn()
    try:
        db_rows = load_active_db_rows(db)
        if not quiet:
            print(
                f"[stats] feed_rows={len(feed)} db_active={len(db_rows)}",
                flush=True,
            )

        feed_by_aid = {r["activity_id"]: r for r in feed}
        seen_in_feed = set()
        unmatched_feed = []
        updated  = 0
        unchanged = 0
        frozen   = 0
        errors   = 0

        # Apply feed rows that map to a DB row.
        for aid, feed_row in feed_by_aid.items():
            db_row = db_rows.get(aid)
            if db_row is None:
                unmatched_feed.append(aid)
                continue
            seen_in_feed.add(aid)
            try:
                outcome = apply_one(db, db_row, feed_row, quiet=quiet)
            except Exception as e:
                errors += 1
                if not quiet:
                    print(f"  ERROR id={db_row['id']} {e}", flush=True)
                continue
            if outcome == "updated":
                updated += 1
            elif outcome == "unchanged":
                unchanged += 1
            elif outcome == "frozen-skip":
                frozen += 1

        # Active DB rows we DIDN'T see in the feed (page didn't scroll
        # far enough, or post was deleted, or URN namespace mismatch).
        # We don't bump scan_no_change_count for these — see header note.
        active_not_frozen_aids = {
            aid for aid, r in db_rows.items() if not r["frozen"]
        }
        not_in_feed = sorted(active_not_frozen_aids - seen_in_feed)

        db.commit()

        total   = len(feed)
        checked = updated + unchanged + frozen
        skipped = frozen + len(unmatched_feed)
        # 'deleted' isn't applicable here — that's a separate detection
        # path (the activity feed showing a post means it's not deleted;
        # not showing it means we didn't scroll far enough, NOT that it's
        # deleted). Emit 0.
        deleted = 0

        result = {
            "ok": True,
            "total":            total,
            "skipped":          skipped,
            "checked":          checked,
            "updated":          updated,
            "unchanged":        unchanged,
            "frozen":           frozen,
            "deleted":          deleted,
            "errors":           errors,
            "unmatched_feed":   unmatched_feed,
            "not_in_feed":      not_in_feed,
        }

        if summary_path:
            try:
                with open(summary_path, "w") as f:
                    json.dump({
                        "refreshed":   updated,
                        "removed":     deleted,
                        "unavailable": 0,
                        "not_found":   len(unmatched_feed),
                    }, f)
            except Exception as e:
                print(
                    f"WARN: failed to write summary {summary_path}: {e}",
                    file=sys.stderr,
                )

        return result
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Apply LinkedIn activity-feed engagement readings to the posts "
            "table with scan_no_change_count freeze."
        )
    )
    p.add_argument("--from-json", required=True,
                   help="Path to JSON file produced by stats-linkedin.sh feed scrape.")
    p.add_argument("--summary", default=None,
                   help="Path to write {refreshed,removed,unavailable,not_found} sidecar.")
    p.add_argument("--quiet", action="store_true", help="Minimal output.")
    args = p.parse_args()

    try:
        result = run(args.from_json, args.summary, args.quiet)
    except Exception as e:
        print(json.dumps({"ok": False, "error": "fatal", "detail": str(e)}),
              file=sys.stderr)
        sys.exit(1)

    if not result.get("ok"):
        print(json.dumps(result, indent=2), file=sys.stderr)
        sys.exit(1)

    print(
        f"LinkedIn: {result['total']} total, "
        f"{result['skipped']} skipped, "
        f"{result['checked']} checked, "
        f"{result['updated']} updated, "
        f"{result['deleted']} deleted, "
        f"{result['errors']} errors"
    )


if __name__ == "__main__":
    main()
