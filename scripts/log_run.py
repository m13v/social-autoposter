#!/usr/bin/env python3
"""Append a summary line to the persistent run monitor log.

Usage:
    python3 scripts/log_run.py --script post_reddit --posted 5 --skipped 2 --failed 0 --cost 3.45 --elapsed 600
"""

import argparse
import os
from datetime import datetime

LOG_PATH = os.path.expanduser("~/social-autoposter/skill/logs/run_monitor.log")


def main():
    parser = argparse.ArgumentParser(description="Log a run summary line")
    parser.add_argument("--script", required=True, help="Script name (e.g. post_reddit, engage_reddit)")
    parser.add_argument("--posted", type=int, default=0, help="Number of successful posts")
    parser.add_argument("--skipped", type=int, default=0, help="Number of skipped items")
    parser.add_argument("--failed", type=int, default=0, help="Number of failures")
    parser.add_argument("--cost", type=float, default=0.0, help="Total cost in USD")
    parser.add_argument("--elapsed", type=float, default=0.0, help="Elapsed time in seconds")
    parser.add_argument("--model", default="", help="Dominant Claude model id used in the run (optional)")
    parser.add_argument("--replies-refreshed", type=int, default=0,
                        help="Number of per-reply stat rows refreshed in this run "
                             "(stats_*, engage_github). Surfaces as a separate pill "
                             "in the dashboard Jobs table.")
    parser.add_argument("--checked", type=int, default=0,
                        help="Stats jobs only: posts the run pulled fresh data for "
                             "(per platform). Renders as a 'checked' pill on stats rows.")
    parser.add_argument("--updated", type=int, default=0,
                        help="Stats jobs only: rows where any tracked metric "
                             "(views/upvotes/comments) actually changed. Renders as 'updated'.")
    parser.add_argument("--removed", type=int, default=0,
                        help="Stats jobs only: posts newly flagged deleted/removed in this run. "
                             "Renders as 'removed'.")
    parser.add_argument("--unavailable", type=int, default=0,
                        help="Stats jobs (LinkedIn): posts where the platform "
                             "explicitly returned a 'post unavailable' string. "
                             "Subset of removed; rendered as a separate pill.")
    parser.add_argument("--not-found", dest="not_found", type=int, default=0,
                        help="Stats jobs (LinkedIn): posts still active but our "
                             "comment couldn't be located. Renders as 'not_found'.")
    parser.add_argument("--salvaged", type=int, default=0,
                        help="Twitter cycle: number of pending candidates from "
                             "prior cycles re-assigned to this batch in Phase 0. "
                             "Surfaces as a 'salvaged' pill in the dashboard "
                             "Result column so an operator can tell that work "
                             "from a previously-failed cycle is being retried "
                             "rather than lost. Optional; 0 = omit segment.")
    parser.add_argument("--failure-reasons", dest="failure_reasons", default="",
                        help="Optional comma-separated `reason:count` pairs "
                             "describing why a run reported failed>0 "
                             "(e.g. 'monthly_limit:5,timeout:1'). Surfaced in "
                             "the dashboard Result column so operators can "
                             "tell a hard cap from a transient error without "
                             "opening the log file. Reason keys are free-form "
                             "snake_case; the dashboard sorts by count desc "
                             "and shows the top one with the rest in tooltip.")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    model_suffix = f" model={args.model}" if args.model else ""
    # Inserted between failed=N and cost= so the existing positional regex in
    # bin/server.js still parses old lines (the segment is optional in the regex).
    replies_segment = (
        f" replies_refreshed={args.replies_refreshed}"
        if args.replies_refreshed else ""
    )
    # Stats-job per-run counters. The base segment (checked/updated/removed)
    # stays as a single optional capture group for the bin/server.js regex.
    # The LinkedIn-specific extras (unavailable/not_found) tail the base
    # segment as their own optional groups so older lines still parse.
    stats_segment = (
        f" checked={args.checked} updated={args.updated} removed={args.removed}"
        if (args.checked or args.updated or args.removed
            or args.unavailable or args.not_found) else ""
    )
    if args.unavailable:
        stats_segment += f" unavailable={args.unavailable}"
    if args.not_found:
        stats_segment += f" not_found={args.not_found}"
    # `salvaged=N` segment tails the stats segment as its own optional capture
    # so old log lines (no salvage info) still parse cleanly. Twitter-cycle
    # specific today, but any pipeline that retries pending work cross-cycle
    # can emit it.
    salvaged_segment = f" salvaged={args.salvaged}" if args.salvaged else ""
    # `failure_reasons` segment is appended after elapsed (and after the
    # optional model suffix) so the existing positional regex in bin/server.js
    # still parses old lines. Sanitize: strip whitespace and forbid the pipe
    # char so the value can't break out of the log line column. Empty string
    # = omit the segment entirely (preserves backward compat).
    fr_raw = (args.failure_reasons or "").strip()
    fr_clean = fr_raw.replace("|", "").replace(" ", "")
    failure_segment = f" failure_reasons={fr_clean}" if fr_clean else ""
    line = (
        f"{timestamp} | {args.script} | "
        f"posted={args.posted} skipped={args.skipped} failed={args.failed}"
        f"{replies_segment}{stats_segment} "
        f"cost=${args.cost:.2f} elapsed={args.elapsed:.0f}s{model_suffix}{failure_segment}"
    )

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")

    print(line)

    if args.posted == 0 and args.failed > 0:
        warning = f"WARNING: {args.script} posted=0 failed={args.failed} -- possible silent failure"
        with open(LOG_PATH, "a") as f:
            f.write(f"{timestamp} | {warning}\n")
        print(warning)


if __name__ == "__main__":
    main()
