#!/usr/bin/env python3
"""promote_engagement_styles.py — graduate candidate styles to active.

Reads scripts/engagement_styles_extra.json and, for each candidate, decides:

  - PROMOTE to active: candidate has been used by >= MIN_POSTS distinct posts
    AND its median engagement score is at or above the platform median for
    posts in the same calendar window. Sets status=active and promoted_at.

  - RETIRE: candidate has been in the sidecar for > MAX_AGE_DAYS, has
    accumulated >= MIN_POSTS posts, and its median is materially below
    platform median (worse than the worst hardcoded style on its primary
    platform). Sets status=retired so it stops appearing in prompts.

  - LEAVE: anything else (still gathering data).

Engagement score: upvotes for reddit/linkedin/twitter, comments_count for
github, total reactions+comments for moltbook. Falls back to 0 when null.

Run nightly via launchd. Idempotent and safe to re-run.

Output is a short status summary per candidate; full reasoning goes to
launchd-promote-engagement-styles-stdout.log.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod
from engagement_styles import SIDECAR_PATH, STYLES, _atomic_write_sidecar, _load_extra_styles

# Tuning knobs. Conservative defaults: a candidate needs real signal before
# entering the trusted weighting in compute_target_distribution.
MIN_POSTS = 3                  # need at least this many posts using the style
MAX_AGE_DAYS_FOR_RETIRE = 14   # retire poorly-performing candidates after this
PROMOTE_MEDIAN_FACTOR = 1.0    # candidate median must be >= platform median
                               # times this to promote (1.0 = at parity)


def _engagement_expr(platform):
    """SQL expression that yields the engagement score for a row on `platform`.

    Different platforms expose different signals; we pick the most-meaningful
    one per platform so cross-platform comparisons don't compare upvotes to
    GitHub thumbs-ups.
    """
    if platform in ("reddit", "twitter", "linkedin"):
        return "COALESCE(upvotes, 0)"
    if platform == "github":
        return "COALESCE(comments_count, 0)"
    if platform == "moltbook":
        return "COALESCE(upvotes, 0) + COALESCE(comments_count, 0)"
    return "COALESCE(upvotes, 0)"


def _platform_median(conn, platform):
    """Median engagement score for active posts on this platform (excluding
    this candidate). Used as the parity bar for promotion.
    """
    expr = _engagement_expr(platform)
    cur = conn.execute(
        f"SELECT PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY {expr}) "
        "FROM posts WHERE platform = %s AND status = 'active' "
        "AND our_content IS NOT NULL AND LENGTH(our_content) >= 30",
        [platform],
    )
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def _candidate_stats(conn, style):
    """Return [(platform, n_posts, median_score), ...] for posts using this style.

    A candidate may be used on multiple platforms; we evaluate each platform
    separately so a style that works on moltbook but not reddit can still
    promote on its strong platform.
    """
    cur = conn.execute(
        "SELECT platform, COUNT(*) "
        "FROM posts WHERE engagement_style = %s AND status = 'active' "
        "AND our_content IS NOT NULL AND LENGTH(our_content) >= 30 "
        "GROUP BY platform",
        [style],
    )
    out = []
    for plat, n in cur.fetchall():
        expr = _engagement_expr(plat)
        cur2 = conn.execute(
            f"SELECT PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY {expr}) "
            "FROM posts WHERE engagement_style = %s AND platform = %s "
            "AND status = 'active' "
            "AND our_content IS NOT NULL AND LENGTH(our_content) >= 30",
            [style, plat],
        )
        row = cur2.fetchone()
        median = float(row[0]) if row and row[0] is not None else 0.0
        out.append((plat, int(n), median))
    return out


def _decide(conn, name, entry):
    """Return (action, reason, updated_entry) for one candidate."""
    if entry.get("status") != "candidate":
        return "skip", f"status={entry.get('status')}, not a candidate", entry

    stats = _candidate_stats(conn, name)
    total_n = sum(n for _, n, _ in stats)
    if total_n < MIN_POSTS:
        return "leave", f"only {total_n} posts so far (need {MIN_POSTS})", entry

    # Promote on the strongest platform if median >= platform median.
    promote_platforms = []
    for plat, n, median in stats:
        if n < MIN_POSTS:
            continue
        plat_median = _platform_median(conn, plat)
        bar = plat_median * PROMOTE_MEDIAN_FACTOR
        if median >= bar:
            promote_platforms.append((plat, n, median, plat_median))

    if promote_platforms:
        new_entry = dict(entry)
        new_entry["status"] = "active"
        new_entry["promoted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Record best_in hints from the platforms where it cleared the bar.
        best_in = dict(new_entry.get("best_in") or {})
        for plat, n, median, plat_median in promote_platforms:
            best_in.setdefault(plat, []).append(
                f"promoted (n={n}, median={median:.1f} vs platform {plat_median:.1f})"
            )
        new_entry["best_in"] = best_in
        reason = "; ".join(
            f"{plat}: n={n} median={median:.1f} >= platform {plat_median:.1f}"
            for plat, n, median, plat_median in promote_platforms
        )
        return "promote", reason, new_entry

    # Consider retiring if too old AND consistently underperforming.
    invented_at_str = entry.get("invented_at")
    age_days = None
    if invented_at_str:
        try:
            invented_at = datetime.fromisoformat(invented_at_str.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - invented_at).days
        except ValueError:
            pass

    if age_days is not None and age_days >= MAX_AGE_DAYS_FOR_RETIRE:
        # Retire if EVERY platform's median is below 0.7 * platform median.
        underperforming = True
        details = []
        for plat, n, median in stats:
            plat_median = _platform_median(conn, plat)
            if median >= 0.7 * plat_median:
                underperforming = False
            details.append(f"{plat}: median={median:.1f} vs platform {plat_median:.1f}")
        if underperforming:
            new_entry = dict(entry)
            new_entry["status"] = "retired"
            new_entry["retired_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return "retire", "; ".join(details) + f" (age {age_days}d)", new_entry

    detail = "; ".join(f"{plat}: n={n} median={median:.1f}" for plat, n, median in stats)
    return "leave", f"underperforming or insufficient: {detail}", entry


def main():
    sidecar = _load_extra_styles()
    if not sidecar:
        print("[promote] no candidates in sidecar; nothing to do")
        return 0

    dbmod.load_env()
    conn = dbmod.get_conn()

    summary = {"promote": [], "retire": [], "leave": [], "skip": []}
    updated = dict(sidecar)
    changed = False

    for name, entry in sidecar.items():
        if name in STYLES:
            # Sidecar entry shadows a hardcoded style; promoter ignores those.
            summary["skip"].append((name, "shadows hardcoded style"))
            continue

        action, reason, new_entry = _decide(conn, name, entry)
        summary[action].append((name, reason))
        if new_entry is not entry and new_entry != entry:
            updated[name] = new_entry
            changed = True

    if changed:
        _atomic_write_sidecar(updated)
        print(f"[promote] sidecar updated; {len(summary['promote'])} promoted, "
              f"{len(summary['retire'])} retired")
    else:
        print("[promote] no changes")

    for action in ("promote", "retire", "leave", "skip"):
        for name, reason in summary[action]:
            print(f"  [{action}] {name}: {reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
