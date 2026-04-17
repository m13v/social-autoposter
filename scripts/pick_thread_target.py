#!/usr/bin/env python3
"""Pick the next (project, subreddit) pair for an original Reddit thread.

Rules:
- Only consider projects with threads.enabled=true.
- A project's own_community (if set) is a candidate every run (subject to its
  own floor_days override, default 1 day for own community).
- External subreddits are subject to the default 3-day floor (configurable via
  threads.external_floor_days).
- Entry filter: skip any subreddit where this account has posted an original
  thread (thread_url == our_url) within that sub's floor window.
- Also skip any subreddit listed in subreddit_bans.thread_blocked.
- Among eligible candidates, prefer own_community if present. Otherwise, weight
  projects by config weight.

Usage:
  python3 scripts/pick_thread_target.py              # stdout: PROJECT\tSUBREDDIT
  python3 scripts/pick_thread_target.py --json       # full context
  python3 scripts/pick_thread_target.py --show-all   # debug view
"""

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")
DEFAULT_OWN_FLOOR_DAYS = 1
DEFAULT_EXTERNAL_FLOOR_DAYS = 3


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def norm_sub(s):
    if not s:
        return ""
    s = s.strip()
    if s.lower().startswith("r/"):
        s = s[2:]
    return s.lower()


def load_thread_blocked_subs(config):
    """Load subreddits where we cannot create new threads.

    Reads subreddit_bans.thread_blocked. For the thread-creation pipeline
    only — the comment pipeline uses subreddit_bans.comment_blocked via
    reddit_tools._load_comment_blocked_subs().
    """
    bans = config.get("subreddit_bans") or {}
    out = set()
    if isinstance(bans, dict):
        for s in bans.get("thread_blocked") or []:
            out.add(norm_sub(s))
    elif isinstance(bans, list):
        # Legacy flat-list form — treat as thread_blocked.
        for s in bans:
            out.add(norm_sub(s))
    return out


def recent_posts_by_sub(max_days):
    """Return dict: sub_slug (lowercased) -> days_since_last_our_thread."""
    conn = dbmod.get_conn()
    rows = conn.execute(
        """
        SELECT thread_url,
               EXTRACT(EPOCH FROM (NOW() - posted_at))/86400.0 AS days_ago
        FROM posts
        WHERE platform='reddit'
          AND thread_url = our_url
          AND posted_at > NOW() - INTERVAL '%s days'
        ORDER BY posted_at DESC
        """ % max_days
    ).fetchall()
    conn.close()
    latest = {}
    for url, days_ago in rows:
        if not url or "/r/" not in url:
            continue
        sub = url.split("/r/", 1)[1].split("/", 1)[0].lower()
        if sub not in latest or days_ago < latest[sub]:
            latest[sub] = float(days_ago)
    return latest


def build_candidates(config):
    recent = recent_posts_by_sub(max_days=max(
        DEFAULT_OWN_FLOOR_DAYS, DEFAULT_EXTERNAL_FLOOR_DAYS, 14))
    thread_blocked = load_thread_blocked_subs(config)
    candidates = []
    for p in config.get("projects", []):
        t = p.get("threads") or {}
        if not t.get("enabled"):
            continue
        ext_floor = int(t.get("external_floor_days", DEFAULT_EXTERNAL_FLOOR_DAYS))
        # Own community
        own = t.get("own_community")
        if own:
            if isinstance(own, dict):
                sub_display = own.get("subreddit")
                own_floor = int(own.get("floor_days", DEFAULT_OWN_FLOOR_DAYS))
            else:
                sub_display = own
                own_floor = DEFAULT_OWN_FLOOR_DAYS
            slug = norm_sub(sub_display)
            if sub_display and slug not in thread_blocked:
                last = recent.get(slug)
                if last is None or last >= own_floor:
                    candidates.append((p, sub_display, True, own_floor, last))
        # External subs
        for sub in t.get("external_subreddits") or []:
            slug = norm_sub(sub)
            if slug in thread_blocked:
                continue
            last = recent.get(slug)
            if last is not None and last < ext_floor:
                continue
            candidates.append((p, sub, False, ext_floor, last))
    return candidates, recent, thread_blocked


def pick(candidates):
    own_candidates = [c for c in candidates if c[2]]
    if own_candidates:
        return random.choice(own_candidates)
    if not candidates:
        return None
    by_project = {}
    for p, sub, is_own, floor, last in candidates:
        by_project.setdefault(p["name"], {"project": p, "entries": []})
        by_project[p["name"]]["entries"].append((sub, is_own, floor, last))
    names = list(by_project.keys())
    weights = [by_project[n]["project"].get("weight", 1) for n in names]
    chosen_name = random.choices(names, weights=weights, k=1)[0]
    proj = by_project[chosen_name]["project"]
    sub, is_own, floor, last = random.choice(by_project[chosen_name]["entries"])
    return (proj, sub, is_own, floor, last)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-all", action="store_true")
    args = ap.parse_args()

    config = load_config()
    candidates, recent, banned = build_candidates(config)

    if args.show_all:
        print(f"Banned subs ({len(banned)}): {sorted(banned)}")
        print(f"Recent thread subs: {len(recent)}")
        for sub, days in sorted(recent.items(), key=lambda x: x[1]):
            print(f"  {sub}: {days:.2f}d ago")
        print(f"\nEligible candidates: {len(candidates)}")
        for p, sub, is_own, floor, last in candidates:
            tag = "OWN" if is_own else "ext"
            last_str = f"last={last:.2f}d" if last is not None else "last=never"
            print(f"  [{tag}] {p['name']:25} {sub:30} floor={floor}d {last_str}")
        return

    choice = pick(candidates)
    if not choice:
        print("NO_ELIGIBLE_TARGET", file=sys.stderr)
        sys.exit(2)

    proj, sub, is_own, floor, last = choice
    if args.json:
        print(json.dumps({
            "project": proj,
            "subreddit": sub,
            "is_own_community": is_own,
            "floor_days": floor,
            "last_posted_days_ago": last,
            "eligible_count": len(candidates),
            "banned_count": len(banned),
        }, indent=2))
    else:
        print(f"{proj['name']}\t{sub}")


if __name__ == "__main__":
    main()
