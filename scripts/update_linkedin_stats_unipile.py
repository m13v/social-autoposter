#!/usr/bin/env python3
"""Update LinkedIn engagement stats via Unipile API (no browser required).

Replaces the browser-scrape path in stats-linkedin.sh for cases where
navigating matthew-diakonov's linkedin-agent session is too risky (post-
restriction recovery) or unnecessary (older posts no longer on the
/recent-activity feed).

Architecture:
  1. Read active non-frozen LinkedIn rows from DB, ordered by
     engagement_updated_at ASC (stale-first). Take --limit rows.
  2. For each row, extract the activity URN from our_url.
  3. GET /api/v1/posts/{urn}?account_id=... via Unipile (burner account).
  4. Apply the same freeze/no-change convention as
     update_linkedin_stats_from_feed.py: if reactions+comments unchanged,
     bump scan_no_change_count; else reset it and write new values.
  5. On HTTP 404: mark status='deleted' (same as update_stats.py does for
     Reddit). This is a bonus — browser scrape can't detect deletions.

Rate limit: 30s between calls by default. At 50 calls/fire this is ~25
min/fire. With a 4-6h launchd cadence that covers ~200 posts/day and sweeps
all 984 active rows in ~5 days. DO NOT reduce below 15s without thinking
about LinkedIn behavioral fingerprinting on the burner account.

Output (stdout): one summary line compatible with stats.sh extract_field:
  LinkedIn-Unipile: <T> total, <S> skipped, <C> checked, <U> updated,
                    <D> deleted, <E> errors

Usage:
  python3 scripts/update_linkedin_stats_unipile.py [--limit 50] [--sleep 30]
      [--account-id UNIPILE_ACCOUNT_ID] [--dry-run] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod  # noqa: E402

FREEZE_NO_CHANGE = 3
FREEZE_AGE_DAYS = 5

ACTIVITY_RE = re.compile(r"urn:li:activity:(\d+)")
UGCPOST_RE  = re.compile(r"urn:li:ugcPost:(\d+)")
SHARE_RE    = re.compile(r"urn:li:share:(\d+)")


def keychain(service: str, account: str = "i@m13v.com") -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["/usr/bin/security", "find-generic-password",
             "-a", account, "-s", service, "-w"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip() or None
    except subprocess.CalledProcessError:
        return None


def unipile_get(dsn: str, api_key: str, path: str) -> dict:
    url = f"https://{dsn}{path}"
    req = urllib.request.Request(
        url, headers={"X-API-KEY": api_key, "accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def get_linkedin_account_id(dsn: str, api_key: str) -> str:
    data = unipile_get(dsn, api_key, "/api/v1/accounts")
    for a in data.get("items", []):
        if a.get("type") == "LINKEDIN":
            return a["id"]
    sys.exit("No LinkedIn account connected to Unipile workspace.")


def extract_post_urn(our_url: str) -> Optional[str]:
    """Return the best URN to query Unipile with for a given our_url.

    Preference: activity > ugcPost > share. Unipile accepts all three.
    """
    if not our_url:
        return None
    m = ACTIVITY_RE.search(our_url)
    if m:
        return f"urn:li:activity:{m.group(1)}"
    m = UGCPOST_RE.search(our_url)
    if m:
        return f"urn:li:ugcPost:{m.group(1)}"
    m = SHARE_RE.search(our_url)
    if m:
        return f"urn:li:share:{m.group(1)}"
    return None


def load_active_rows(db, limit: int) -> list[dict]:
    """Active non-frozen LinkedIn rows ordered stale-first."""
    rows = db.execute(
        "SELECT id, our_url, "
        "       COALESCE(upvotes, 0)              AS upvotes, "
        "       COALESCE(comments_count, 0)       AS comments_count, "
        "       COALESCE(scan_no_change_count, 0) AS scan_no_change_count "
        "FROM posts "
        "WHERE platform='linkedin' "
        "  AND status='active' "
        "  AND our_url IS NOT NULL "
        "  AND (our_url ~ 'urn:li:activity:' "
        "       OR our_url ~ 'urn:li:ugcPost:' "
        "       OR our_url ~ 'urn:li:share:') "
        "  AND NOT ( "
        "      COALESCE(scan_no_change_count, 0) >= %s "
        "      AND posted_at < NOW() - INTERVAL '%s days' "
        "  ) "
        "ORDER BY engagement_updated_at ASC NULLS FIRST "
        "LIMIT %s",
        (FREEZE_NO_CHANGE, FREEZE_AGE_DAYS, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit",      type=int,   default=50)
    ap.add_argument("--sleep",      type=float, default=30.0,
                    help="seconds between Unipile calls. Do not go below 15.")
    ap.add_argument("--account-id", default=None)
    ap.add_argument("--dry-run",    action="store_true",
                    help="fetch stats but do not write to DB")
    ap.add_argument("--quiet",      action="store_true")
    args = ap.parse_args()

    dsn = os.environ.get("UNIPILE_DSN") or keychain("unipile-dsn")
    key = os.environ.get("UNIPILE_API_KEY") or keychain("unipile-api-key")
    if not dsn or not key:
        sys.exit("Missing creds. Set UNIPILE_DSN/UNIPILE_API_KEY or add to keychain.")

    account_id = args.account_id or get_linkedin_account_id(dsn, key)

    db = dbmod.get_conn()
    rows = load_active_rows(db, args.limit)
    if not rows:
        print("LinkedIn-Unipile: 0 total, 0 skipped, 0 checked, 0 updated, 0 deleted, 0 errors")
        return 0

    counters = dict(total=len(rows), skipped=0, checked=0,
                    updated=0, deleted=0, errors=0)

    for i, row in enumerate(rows):
        urn = extract_post_urn(row["our_url"])
        if not urn:
            counters["skipped"] += 1
            continue

        encoded = urllib.parse.quote(urn, safe="")
        path = f"/api/v1/posts/{encoded}?account_id={account_id}"

        try:
            data = unipile_get(dsn, key, path)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                if not args.quiet:
                    print(f"  [{i+1}] {row['id']} DELETED (404)")
                if not args.dry_run:
                    db.execute(
                        "UPDATE posts SET status='deleted', "
                        "engagement_updated_at=NOW() WHERE id=%s",
                        (row["id"],),
                    )
                    db.commit()
                counters["deleted"] += 1
            else:
                if not args.quiet:
                    print(f"  [{i+1}] {row['id']} HTTP {e.code}")
                counters["errors"] += 1
            if i < len(rows) - 1:
                time.sleep(args.sleep)
            continue
        except Exception as e:
            if not args.quiet:
                print(f"  [{i+1}] {row['id']} ERR {type(e).__name__}: {e}")
            counters["errors"] += 1
            if i < len(rows) - 1:
                time.sleep(args.sleep)
            continue

        new_rxn = int(data.get("reaction_counter") or 0)
        new_cmt = int(data.get("comment_counter") or 0)
        old_rxn = int(row["upvotes"])
        old_cmt = int(row["comments_count"])
        counters["checked"] += 1

        changed = (new_rxn != old_rxn or new_cmt != old_cmt)
        if not args.quiet:
            tag = "UPDATED" if changed else "same"
            print(f"  [{i+1}] {row['id']}  rxn {old_rxn}->{new_rxn}  "
                  f"cmt {old_cmt}->{new_cmt}  [{tag}]")

        if not args.dry_run:
            if changed:
                db.execute(
                    "UPDATE posts SET upvotes=%s, comments_count=%s, "
                    "engagement_updated_at=NOW(), scan_no_change_count=0 "
                    "WHERE id=%s",
                    (new_rxn, new_cmt, row["id"]),
                )
                counters["updated"] += 1
            else:
                db.execute(
                    "UPDATE posts SET engagement_updated_at=NOW(), "
                    "scan_no_change_count=COALESCE(scan_no_change_count,0)+1 "
                    "WHERE id=%s",
                    (row["id"],),
                )
            db.commit()

        if i < len(rows) - 1:
            time.sleep(args.sleep)

    c = counters
    summary = (
        f"LinkedIn-Unipile: {c['total']} total, {c['skipped']} skipped, "
        f"{c['checked']} checked, {c['updated']} updated, "
        f"{c['deleted']} deleted, {c['errors']} errors"
    )
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
