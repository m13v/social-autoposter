#!/usr/bin/env python3
"""DEPRECATED 2026-05-05.

This script implemented the per-permalink scrape loop pattern that LinkedIn's
anti-bot system flagged on 2026-05-05 (incident #2 after 2026-04-17). Even
when CDP-attached to the linkedin-agent MCP, looping `page.goto` over 30
`/feed/update/<urn>/` permalinks per fire is itself the banned pattern,
regardless of which Chrome process drives it.

Replaced by:
- skill/stats-linkedin.sh (Claude-driven, MCP linkedin-agent only)
- scripts/update_linkedin_stats_from_feed.py (DB writer with scan_no_change_count)

The new pipeline does ONE navigation per fire to /in/me/recent-activity/all/,
scroll-loads in-page (native LinkedIn UX), and extracts engagement counts
for every visible post in a single DOM read. No permalink hops.

The locked skill/stats.sh (Step 4 LinkedIn leg) still references this file
path. Until stats.sh is unlocked and updated to call stats-linkedin.sh
directly, this stub stays in place to fail fast and keep the rest of
stats.sh's per-platform fan-out unaffected.

Do NOT restore the old body. The git history preserves it if archaeology
is needed.
"""

import json
import sys


def main() -> None:
    print(
        json.dumps({
            "ok": False,
            "error": "deprecated",
            "detail": (
                "scrape_linkedin_stats_browser.py was retired 2026-05-05 "
                "after triggering LinkedIn anti-bot fingerprinting (incident "
                "#2 in 3 weeks). Use skill/stats-linkedin.sh instead, which "
                "runs MCP-only with a single activity-feed navigation. See "
                "the file header for full context."
            ),
        }),
        file=sys.stderr,
    )
    # Exit 2 (not 1) so stats.sh logs it as a hard failure distinct from
    # 'no eligible posts' (exit 0 with note) or runtime error (exit 1).
    sys.exit(2)


if __name__ == "__main__":
    main()
