#!/usr/bin/env python3
"""Pick the next (project, topic_angle) pair for an original Twitter thread.

Mirrors scripts/pick_thread_target.py (Reddit), adapted for Twitter:

Differences vs the Reddit picker:
- No subreddit dimension. The natural floor unit is (project, topic_angle).
- Hard global daily cap. Across all projects, never post more than
  TWITTER_DAILY_CAP original threads in a UTC calendar day. Enforced via a
  COUNT(*) of posts where platform='twitter' AND thread_url=our_url AND
  posted_at::date = CURRENT_DATE. If hit, exit non-zero so the orchestrator
  cleanly skips the launchd fire.
- Per-project per-angle floor window (twitter_threads.topic_floor_days,
  default 2). Picks an angle that is either never-used or older than the
  floor for the given project.
- Project weight + inverse recent-share weighting (same as Reddit picker)
  so we don't pile every fire on one project.

Usage:
  python3 scripts/pick_twitter_thread_target.py              # PROJECT\tANGLE
  python3 scripts/pick_twitter_thread_target.py --json       # full context
  python3 scripts/pick_twitter_thread_target.py --show-all   # debug view
"""

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")
DEFAULT_TOPIC_FLOOR_DAYS = 2
TWITTER_DAILY_CAP = 3      # hard global cap. user requirement, do not raise without explicit ask.


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def daily_count_today():
    """Return the number of original Twitter threads posted in the current
    UTC calendar day (matching posted_at::date = CURRENT_DATE in Postgres).

    Excludes engage-twitter mention-bookkeeping rows that share the
    thread_url=our_url shape but aren't actually our threads. Those rows have
    our_content like "(mention - no original post)" and source_summary IS
    NULL, and exist because the notifications scanner stamps them as a
    placeholder when it sees we were @mentioned without our own reply.
    """
    conn = dbmod.get_conn()
    row = conn.execute(
        """
        SELECT COUNT(*) FROM posts
        WHERE platform='twitter'
          AND thread_url = our_url
          AND posted_at::date = CURRENT_DATE
          AND our_content NOT ILIKE '(mention%'
        """
    ).fetchone()
    conn.close()
    return int(row[0]) if row else 0


def recent_angles_by_project(days=14):
    """For each project, return dict: angle_text -> days_since_last_use.

    Uses source_summary to detect which angle was used, since that is what the
    orchestrator stamps with the chosen topic_angle (same convention the
    Reddit pipeline uses). We do a substring match: an angle counts as recent
    if its first 60 chars appear in source_summary.

    Returns: { project_name: { angle_text: days_ago_float } }
    """
    conn = dbmod.get_conn()
    rows = conn.execute(
        """
        SELECT project_name, source_summary,
               EXTRACT(EPOCH FROM (NOW() - posted_at))/86400.0 AS days_ago
        FROM posts
        WHERE platform='twitter'
          AND thread_url = our_url
          AND posted_at > NOW() - INTERVAL '%s days'
          AND project_name IS NOT NULL
          AND source_summary IS NOT NULL
        ORDER BY posted_at DESC
        """ % int(days)
    ).fetchall()
    conn.close()
    out = {}
    for project_name, summary, days_ago in rows:
        out.setdefault(project_name, {})
        # We don't know exactly which angle text was chosen, so the caller
        # does substring matching against source_summary. For now we just
        # store (summary, days_ago) tuples and let the caller iterate.
        out[project_name].setdefault("_rows", []).append((summary or "", float(days_ago)))
    return out


def angle_recency(project_recents, angle_text):
    """Given project_recents[project] (a dict with '_rows' list of
    (summary, days_ago)), return the smallest days_ago for any row whose
    summary contains the first 60 chars of angle_text. None if never used.
    """
    rows = (project_recents or {}).get("_rows") or []
    needle = (angle_text or "").strip()[:60].lower()
    if not needle:
        return None
    best = None
    for summary, days_ago in rows:
        if needle in (summary or "").lower():
            if best is None or days_ago < best:
                best = days_ago
    return best


def recent_posts_by_project(days=7):
    """Return dict: project_name -> count of original Twitter threads in last N days.

    Excludes mention-placeholder rows (see daily_count_today docstring).
    """
    conn = dbmod.get_conn()
    rows = conn.execute(
        """
        SELECT project_name, COUNT(*)
        FROM posts
        WHERE platform='twitter'
          AND thread_url = our_url
          AND posted_at > NOW() - INTERVAL '%s days'
          AND project_name IS NOT NULL
          AND our_content NOT ILIKE '(mention%%'
        GROUP BY project_name
        """ % int(days)
    ).fetchall()
    conn.close()
    return {name: int(cnt) for name, cnt in rows}


def build_candidates(config):
    project_recents = recent_angles_by_project(days=14)
    candidates = []   # (project_dict, angle_text, floor_days, last_used_days_ago_or_None)
    for p in config.get("projects", []):
        tt = p.get("twitter_threads") or {}
        if not tt.get("enabled"):
            continue
        floor = int(tt.get("topic_floor_days", DEFAULT_TOPIC_FLOOR_DAYS))
        angles = tt.get("topic_angles") or []
        if not angles:
            continue
        recents_for_proj = project_recents.get(p["name"], {})
        for angle in angles:
            last = angle_recency(recents_for_proj, angle)
            if last is not None and last < floor:
                continue  # too recent
            candidates.append((p, angle, floor, last))
    return candidates, project_recents


def pick(candidates, recent_project_counts=None):
    if not candidates:
        return None
    recent_project_counts = recent_project_counts or {}
    by_project = {}
    for p, angle, floor, last in candidates:
        by_project.setdefault(p["name"], {"project": p, "entries": []})
        by_project[p["name"]]["entries"].append((angle, floor, last))
    names = list(by_project.keys())
    # Inverse recent-share: keep config weight as the prior, penalise projects
    # that already posted a lot in the last 7d.
    weights = [
        by_project[n]["project"].get("weight", 1)
        / (1 + recent_project_counts.get(n, 0))
        for n in names
    ]
    chosen_name = random.choices(names, weights=weights, k=1)[0]
    proj = by_project[chosen_name]["project"]
    angle, floor, last = random.choice(by_project[chosen_name]["entries"])
    return (proj, angle, floor, last)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-all", action="store_true")
    args = ap.parse_args()

    config = load_config()

    # Hard daily cap. Check FIRST so the picker exits cheap when the day is
    # already saturated.
    today_count = daily_count_today()
    if today_count >= TWITTER_DAILY_CAP and not args.show_all:
        print(f"DAILY_CAP_REACHED: {today_count}/{TWITTER_DAILY_CAP} posts today",
              file=sys.stderr)
        sys.exit(3)

    candidates, project_recents = build_candidates(config)
    recent_project_counts = recent_posts_by_project(days=7)

    if args.show_all:
        print(f"Daily cap: {today_count}/{TWITTER_DAILY_CAP} posts today (UTC)")
        eligible_projects = {}
        for p, angle, floor, last in candidates:
            eligible_projects.setdefault(p["name"], p)
        print(f"\nProject weights (base / posts_7d / effective):")
        rows = []
        for name, p in eligible_projects.items():
            base = p.get("weight", 1)
            posts_7d = recent_project_counts.get(name, 0)
            eff = base / (1 + posts_7d)
            rows.append((name, base, posts_7d, eff))
        for name, base, posts_7d, eff in sorted(rows, key=lambda r: -r[3]):
            print(f"  {name:25} base={base:>3}  posts_7d={posts_7d:>2}  effective={eff:.3f}")
        print(f"\nEligible candidates: {len(candidates)}")
        for p, angle, floor, last in candidates:
            last_str = f"last={last:.2f}d" if last is not None else "last=never"
            angle_short = (angle[:70] + "...") if len(angle) > 73 else angle
            print(f"  {p['name']:20} floor={floor}d {last_str:14} {angle_short}")
        return

    choice = pick(candidates, recent_project_counts=recent_project_counts)
    if not choice:
        print("NO_ELIGIBLE_TARGET", file=sys.stderr)
        sys.exit(2)

    proj, angle, floor, last = choice
    if args.json:
        print(json.dumps({
            "project": proj,
            "topic_angle": angle,
            "floor_days": floor,
            "last_used_days_ago": last,
            "eligible_count": len(candidates),
            "daily_count_today": today_count,
            "daily_cap": TWITTER_DAILY_CAP,
        }, indent=2))
    else:
        print(f"{proj['name']}\t{angle}")


if __name__ == "__main__":
    main()
