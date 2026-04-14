#!/usr/bin/env python3
"""Pick the next (project, subreddit) pair for an original Reddit thread.

Rules:
- Only consider projects with threads.enabled=true.
- A project's own_community (if set) is a candidate every run.
- External subreddits are candidates too, subject to the 3-day floor filter.
- Entry filter: skip any subreddit where this account has posted an original
  thread (thread_url == our_url) in the last 3 days.
- Among eligible candidates, prefer own_community if present (those are
  intended to be daily). Otherwise, weight projects by config weight and pick
  a random eligible external subreddit for the chosen project.

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
FLOOR_DAYS = 3


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def recent_thread_subs():
    """Return set of subreddit slugs (lowercased, no r/) that had an original
    thread posted within FLOOR_DAYS days.

    An "original thread" means a row where thread_url == our_url (we posted it,
    not a comment on someone else's thread).
    """
    conn = dbmod.get_conn()
    rows = conn.execute(
        """
        SELECT thread_url
        FROM posts
        WHERE platform='reddit'
          AND thread_url = our_url
          AND posted_at > NOW() - INTERVAL '%s days'
        """ % FLOOR_DAYS
    ).fetchall()
    conn.close()
    subs = set()
    for (url,) in rows:
        if not url:
            continue
        # Extract /r/NAME/ segment
        parts = url.split("/r/")
        if len(parts) < 2:
            continue
        tail = parts[1]
        sub = tail.split("/", 1)[0]
        subs.add(sub.lower())
    return subs


def norm_sub(s):
    """Return just the slug, lowercased, no 'r/' prefix."""
    if not s:
        return ""
    s = s.strip()
    if s.lower().startswith("r/"):
        s = s[2:]
    return s.lower()


def build_candidates(config, recent_subs):
    """Build a list of (project, subreddit_display, is_own_community) tuples
    for every eligible (not in recent_subs) target.
    """
    candidates = []
    for p in config.get("projects", []):
        t = p.get("threads") or {}
        if not t.get("enabled"):
            continue
        # Own community (daily)
        own = t.get("own_community")
        if own:
            sub_display = own.get("subreddit") if isinstance(own, dict) else own
            if sub_display and norm_sub(sub_display) not in recent_subs:
                candidates.append((p, sub_display, True))
        # External subs (weekly rotation via 3-day floor)
        for sub in t.get("external_subreddits") or []:
            if norm_sub(sub) not in recent_subs:
                candidates.append((p, sub, False))
    return candidates


def pick(config, candidates):
    """Pick one candidate.

    Strategy: if any own_community candidate exists, pick one of them uniformly
    (these are meant to be daily). Otherwise, sample a project weighted by
    config weight (restricted to projects with at least one eligible external
    sub), then pick a random eligible external sub for that project.
    """
    own_candidates = [c for c in candidates if c[2]]
    if own_candidates:
        return random.choice(own_candidates)

    # Weighted selection among projects with external candidates
    by_project = {}
    for p, sub, _ in candidates:
        by_project.setdefault(p["name"], {"project": p, "subs": []})
        by_project[p["name"]]["subs"].append(sub)

    if not by_project:
        return None

    names = list(by_project.keys())
    weights = [by_project[n]["project"].get("weight", 1) for n in names]
    chosen_name = random.choices(names, weights=weights, k=1)[0]
    proj = by_project[chosen_name]["project"]
    sub = random.choice(by_project[chosen_name]["subs"])
    return (proj, sub, False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Output full JSON context")
    ap.add_argument("--show-all", action="store_true", help="Print all eligible candidates and recent floor")
    args = ap.parse_args()

    config = load_config()
    recent = recent_thread_subs()
    candidates = build_candidates(config, recent)

    if args.show_all:
        print(f"Recent subs (last {FLOOR_DAYS}d):", sorted(recent))
        print(f"Eligible candidates: {len(candidates)}")
        for p, sub, is_own in candidates:
            tag = "OWN" if is_own else "ext"
            print(f"  [{tag}] {p['name']:25} {sub}")
        return

    choice = pick(config, candidates)
    if not choice:
        print("NO_ELIGIBLE_TARGET", file=sys.stderr)
        sys.exit(2)

    proj, sub, is_own = choice
    if args.json:
        print(json.dumps({
            "project": proj,
            "subreddit": sub,
            "is_own_community": is_own,
            "recent_subs": sorted(recent),
            "eligible_count": len(candidates),
        }, indent=2))
    else:
        print(f"{proj['name']}\t{sub}")


if __name__ == "__main__":
    main()
