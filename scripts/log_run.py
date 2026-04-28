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
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    model_suffix = f" model={args.model}" if args.model else ""
    # Inserted between failed=N and cost= so the existing positional regex in
    # bin/server.js still parses old lines (the segment is optional in the regex).
    replies_segment = (
        f" replies_refreshed={args.replies_refreshed}"
        if args.replies_refreshed else ""
    )
    line = (
        f"{timestamp} | {args.script} | "
        f"posted={args.posted} skipped={args.skipped} failed={args.failed}"
        f"{replies_segment} "
        f"cost=${args.cost:.2f} elapsed={args.elapsed:.0f}s{model_suffix}"
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
