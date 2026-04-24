#!/usr/bin/env python3
"""
fetch_twitter_t1.py

Phase 2 of the twitter-cycle. Re-polls fxtwitter for every candidate in a
given batch_id, writes T1 engagement columns and computes delta_score.

    python3 scripts/fetch_twitter_t1.py --batch-id <id>

delta_score formula:
    Δlikes + 3*Δretweets + 2*Δreplies + Δviews/1000 + Δbookmarks
Weights picked so retweets/replies (stronger virality signals) beat raw likes,
views are divided down so they don't dominate.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def fetch_fxtwitter(handle, tweet_id):
    url = f"https://api.fxtwitter.com/{handle}/status/{tweet_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "social-autoposter/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  fxtwitter error for {handle}/{tweet_id}: {e}", file=sys.stderr)
        return None


def parse(url):
    m = re.search(r"x\.com/([^/]+)/status/(\d+)", url or "")
    if not m:
        m = re.search(r"twitter\.com/([^/]+)/status/(\d+)", url or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def compute_delta(t0, t1):
    dl = (t1.get("likes", 0) or 0) - (t0.get("likes", 0) or 0)
    dr = (t1.get("retweets", 0) or 0) - (t0.get("retweets", 0) or 0)
    dp = (t1.get("replies", 0) or 0) - (t0.get("replies", 0) or 0)
    dv = (t1.get("views", 0) or 0) - (t0.get("views", 0) or 0)
    db = (t1.get("bookmarks", 0) or 0) - (t0.get("bookmarks", 0) or 0)
    return dl + 3 * dr + 2 * dp + dv / 1000.0 + db


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--batch-id", required=True)
    args = p.parse_args()

    conn = dbmod.get_conn()
    rows = conn.execute(
        """
        SELECT id, tweet_url,
               likes_t0, retweets_t0, replies_t0, views_t0, bookmarks_t0
        FROM twitter_candidates
        WHERE batch_id = %s AND status = 'pending'
        """,
        [args.batch_id],
    ).fetchall()

    if not rows:
        print(f"No pending rows for batch {args.batch_id}", file=sys.stderr)
        return

    print(f"Re-polling {len(rows)} candidates for batch {args.batch_id}", file=sys.stderr)

    def fetch_row(row):
        cid, url, l0, r0, p0, v0, b0 = row
        handle, tweet_id = parse(url)
        if not handle:
            return None
        data = fetch_fxtwitter(handle, tweet_id)
        if not data or not data.get("tweet"):
            return None
        t = data["tweet"]
        t1 = {
            "likes": t.get("likes", 0),
            "retweets": t.get("retweets", 0),
            "replies": t.get("replies", 0),
            "views": t.get("views", 0),
            "bookmarks": t.get("bookmarks", 0),
        }
        t0 = {"likes": l0 or 0, "retweets": r0 or 0, "replies": p0 or 0, "views": v0 or 0, "bookmarks": b0 or 0}
        return (cid, url, t1, compute_delta(t0, t1))

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(fetch_row, rows))

    for result in results:
        if result is None:
            continue
        cid, url, t1, delta = result
        conn.execute(
            """
            UPDATE twitter_candidates
            SET likes_t1=%s, retweets_t1=%s, replies_t1=%s, views_t1=%s, bookmarks_t1=%s,
                t1_checked_at=NOW(), delta_score=%s,
                likes=%s, retweets=%s, replies=%s, views=%s, bookmarks=%s
            WHERE id=%s
            """,
            [t1["likes"], t1["retweets"], t1["replies"], t1["views"], t1["bookmarks"],
             delta,
             t1["likes"], t1["retweets"], t1["replies"], t1["views"], t1["bookmarks"],
             cid],
        )
        print(f"  #{cid} {url} Δ={delta:.1f}", file=sys.stderr)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
