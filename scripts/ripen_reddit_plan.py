#!/usr/bin/env python3
"""
ripen_reddit_plan.py

Reddit equivalent of Twitter's Phase 2a (T1 re-poll + delta gate). Reads a
plan JSON written by `post_reddit.py --phase plan`, captures T0 score/comments
for each target_thread_url, sleeps SLEEP_SECONDS (default 300), re-polls T1,
computes composite delta = Δupvotes + W_COMMENTS * Δcomments, and drops
decisions whose composite <= FLOOR (default 5).

Survivors are written to --out as a new plan JSON consumed by
`post_reddit.py --phase post`. Dropped decisions are logged to stderr and
into the output JSON under `ripen_dropped_details`.

Defaults match the design agreed on 2026-05-06:
    composite = Δup + 4*Δcomments
    floor = composite > 5  (strict)
    sleep = 300s (5 min)

Failure modes:
    - T0 fetch fails for a URL: drop that decision (fail-closed; we cannot
      measure delta without T0)
    - All T0 fetches fail: bail with passthrough (likely Reddit-wide rate
      limit; better to post stale than nothing on a bad-network cycle)
    - T1 fetch fails for a URL: drop that decision (same logic)
"""
import argparse
import json
import os
import subprocess
import sys
import time

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_DIR, "scripts")


def repoll(urls, timeout=120):
    """Call reddit_tools.py repoll with the given URLs. Returns the parsed
    {"results": {url: {ok, score, comments}}} dict (or {} on hard failure)."""
    if not urls:
        return {}
    payload = json.dumps({"urls": urls})
    try:
        proc = subprocess.run(
            ["python3", os.path.join(SCRIPTS_DIR, "reddit_tools.py"), "repoll"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"[ripen] ERROR: repoll subprocess timeout", file=sys.stderr)
        return {}
    if proc.returncode != 0:
        print(f"[ripen] ERROR: repoll exit={proc.returncode} stderr={proc.stderr[:200]}",
              file=sys.stderr)
        return {}
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        print(f"[ripen] ERROR: repoll bad JSON: {e}", file=sys.stderr)
        return {}
    return out.get("results") or {}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="in_path", required=True, help="Input plan JSON path")
    p.add_argument("--out", required=True, help="Output filtered plan JSON path")
    p.add_argument("--floor", type=float, default=5.0,
                   help="Composite delta must be STRICTLY greater than this (default: 5.0)")
    p.add_argument("--w-comments", type=float, default=4.0,
                   help="Comment weight in composite formula (default: 4.0)")
    p.add_argument("--sleep", type=int, default=300,
                   help="Seconds to sleep between T0 and T1 (default: 300)")
    p.add_argument("--no-sleep", action="store_true",
                   help="Skip the sleep (for tests)")
    args = p.parse_args()

    with open(args.in_path) as f:
        plan = json.load(f)

    decisions = plan.get("decisions") or []
    if not decisions:
        print(f"[ripen] empty plan, passthrough", file=sys.stderr)
        with open(args.out, "w") as f:
            json.dump(plan, f)
        return 0

    urls = []
    for d in decisions:
        # post_reddit.py writes the field as `thread_url` (not target_thread_url).
        # Tolerate both for safety in case the schema ever changes.
        u = (d.get("thread_url") or d.get("target_thread_url") or "").strip()
        if u:
            urls.append(u)

    if not urls:
        print(f"[ripen] no thread_urls in {len(decisions)} decisions; passthrough",
              file=sys.stderr)
        with open(args.out, "w") as f:
            json.dump(plan, f)
        return 0

    # ---- T0 capture ---------------------------------------------------------
    print(f"[ripen] T0: fetching {len(urls)} thread(s)...", file=sys.stderr)
    t0 = repoll(urls)
    t0_ok = {u: r for u, r in t0.items() if r.get("ok")}
    if not t0_ok:
        print(f"[ripen] WARN: 0 of {len(urls)} T0 fetches succeeded; "
              "passthrough (likely rate limit)", file=sys.stderr)
        with open(args.out, "w") as f:
            json.dump(plan, f)
        return 0
    print(f"[ripen] T0: {len(t0_ok)}/{len(urls)} succeeded", file=sys.stderr)

    # ---- Sleep --------------------------------------------------------------
    if not args.no_sleep:
        print(f"[ripen] sleeping {args.sleep}s for engagement to develop...",
              file=sys.stderr)
        time.sleep(args.sleep)

    # ---- T1 re-poll ---------------------------------------------------------
    print(f"[ripen] T1: re-fetching {len(t0_ok)} thread(s)...", file=sys.stderr)
    t1 = repoll(list(t0_ok.keys()))

    # ---- Filter -------------------------------------------------------------
    survivors = []
    drops = []
    for d in decisions:
        url = (d.get("thread_url") or d.get("target_thread_url") or "").strip()
        t0r = t0_ok.get(url)
        t1r = t1.get(url, {}) if t1 else {}
        if not t0r:
            drops.append({"url": url, "reason": "no_t0"})
            continue
        if not t1r.get("ok"):
            drops.append({
                "url": url,
                "reason": f"t1_fail:{t1r.get('error', 'unknown')}",
            })
            continue
        d_up = int(t1r["score"]) - int(t0r["score"])
        d_co = int(t1r["comments"]) - int(t0r["comments"])
        composite = d_up + args.w_comments * d_co
        # Annotate decision with measurement (always, even if dropped — useful
        # for downstream analysis/debug)
        d["ripen"] = {
            "t0_score": t0r["score"],
            "t0_comments": t0r["comments"],
            "t1_score": t1r["score"],
            "t1_comments": t1r["comments"],
            "delta_up": d_up,
            "delta_comments": d_co,
            "composite": composite,
            "window_sec": args.sleep if not args.no_sleep else 0,
            "floor": args.floor,
            "w_comments": args.w_comments,
        }
        if composite > args.floor:
            survivors.append(d)
            print(f"[ripen] PASS composite={composite:.1f} (Δup={d_up}, Δcomm={d_co}) "
                  f"{url}", file=sys.stderr)
        else:
            drops.append({
                "url": url,
                "reason": f"composite={composite:.1f} <= floor={args.floor}",
                "delta_up": d_up,
                "delta_comments": d_co,
            })
            print(f"[ripen] DROP composite={composite:.1f} (Δup={d_up}, Δcomm={d_co}) "
                  f"{url}", file=sys.stderr)

    plan["decisions"] = survivors
    plan["ripen_summary"] = {
        "input_count": len(decisions),
        "survivors": len(survivors),
        "drops": len(drops),
        "floor": args.floor,
        "w_comments": args.w_comments,
        "sleep_sec": args.sleep if not args.no_sleep else 0,
    }
    plan["ripen_dropped_details"] = drops

    with open(args.out, "w") as f:
        json.dump(plan, f)

    # Compact, parseable summary marker for the dashboard's
    # enrichPostCommentsRedditRuns() in bin/server.js. Field order matters; keep
    # in sync with the regex on the JS side.
    best_composite = None
    best_d_up = None
    best_d_co = None
    for d in survivors:
        rip = d.get("ripen") or {}
        c = rip.get("composite")
        if c is None:
            continue
        if best_composite is None or c > best_composite:
            best_composite = c
            best_d_up = rip.get("delta_up")
            best_d_co = rip.get("delta_comments")
    bc = "" if best_composite is None else f"{best_composite:.1f}"
    bu = "" if best_d_up is None else str(best_d_up)
    bk = "" if best_d_co is None else str(best_d_co)
    print(
        f"[ripen] summary input={len(decisions)} survivors={len(survivors)} "
        f"drops={len(drops)} floor={args.floor} w_comments={args.w_comments} "
        f"window_sec={args.sleep if not args.no_sleep else 0} "
        f"best_composite={bc} best_d_up={bu} best_d_co={bk}",
        file=sys.stderr,
    )
    print(f"[ripen] done: {len(survivors)} survivors, {len(drops)} drops "
          f"(floor>{args.floor}, w_comments={args.w_comments})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
