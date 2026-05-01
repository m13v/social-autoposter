#!/usr/bin/env python3
"""
twitter_gen_links.py — Phase 2b-gen helper for run-twitter-cycle.sh.

Reads a candidate plan JSON file produced by Phase 2b-prep, generates the
matching landing-page (or falls back to the plain project URL) for each
candidate, and writes the file back with a `link_url` field per candidate.

The browser lock is NOT held while this runs. generate_page.py is pure HTTP +
git + Cloud-Run-deploy work, no twitter-agent profile use, so other twitter
pipelines can use the browser during the 10-40 minute landing-page build.

Plan file shape (in/out):
{
  "candidates": [
    {
      "candidate_id": int,
      "candidate_url": str,
      "thread_author": str,
      "thread_text": str,
      "matched_project": str,
      "reply_text": str,
      "engagement_style": str,
      "language": str,
      "has_landing_pages": bool,
      "link_keyword": str,   # only when has_landing_pages=true
      "link_slug": str,      # only when has_landing_pages=true
      ...
      # Written by THIS script:
      "link_url": str,       # final URL to embed in the reply (may be "")
      "link_source": str,    # seo_page | plain_url_fallback | plain_url_no_lp |
                             # plain_url_timeout_fallback | empty
    },
    ...
  ]
}

Usage:
    python3 twitter_gen_links.py --plan /tmp/twitter_cycle_plan_<batch>.json

Exits 0 on best-effort completion (each candidate gets a link_url, even if
generation failed; the fallback chain protects the cycle from blocking on
SEO infra issues). Exits non-zero only when the plan file itself is unreadable
or empty.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_DIR = os.path.expanduser("~/social-autoposter")
GENERATE_PAGE = os.path.join(REPO_DIR, "seo", "generate_page.py")
CONFIG_PATH = os.path.join(REPO_DIR, "config.json")
GEN_TIMEOUT_SEC = 3000  # generate_page.py's own 2400s budget + slack


def load_projects() -> dict:
    """Map name -> project dict."""
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    return {p["name"]: p for p in cfg.get("projects", [])}


def run_generate(product: str, keyword: str, slug: str) -> tuple[str, str]:
    """Run generate_page.py for a single candidate.

    Returns (page_url, source_tag). On success: (real_url, "seo_page"). On
    any failure: ("", "<reason>") so the caller can fall back to the plain URL.
    """
    cmd = [
        "python3",
        GENERATE_PAGE,
        "--product", product,
        "--keyword", keyword,
        "--slug", slug,
        "--trigger", "twitter",
    ]
    print(f"[gen] product={product} keyword={keyword!r} slug={slug!r}", flush=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=GEN_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        print(f"[gen] TIMEOUT after {GEN_TIMEOUT_SEC}s", flush=True)
        return ("", "timeout")
    print(f"[gen] exit={r.returncode}", flush=True)
    if r.stderr:
        # Trail-truncate so we don't blow out the cycle log on a verbose failure.
        print("[gen][stderr-tail]", r.stderr[-2000:], flush=True)
    # generate_page.py prints a final JSON line: {"success": ..., "page_url": ...}
    page_url = ""
    last_obj = None
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                last_obj = json.loads(line)
            except Exception:
                continue
    if last_obj and last_obj.get("success") and last_obj.get("page_url"):
        page_url = last_obj["page_url"]
    if not page_url:
        # Surface the last few stdout lines for debug.
        print("[gen] no page_url in stdout; tail=", flush=True)
        print(r.stdout[-2000:], flush=True)
        return ("", "no_page_url")
    return (page_url, "seo_page")


def resolve_link(candidate: dict, projects: dict) -> tuple[str, str]:
    """Decide the link URL for a single candidate.

    Order of preference: SEO page (when applicable) -> plain project URL -> "".
    """
    proj_name = candidate.get("matched_project") or ""
    proj = projects.get(proj_name) or {}
    plain_url = proj.get("website") or proj.get("url") or ""
    has_lp = bool(candidate.get("has_landing_pages"))
    keyword = (candidate.get("link_keyword") or "").strip()
    slug = (candidate.get("link_slug") or "").strip()

    if has_lp and keyword and slug and proj.get("landing_pages"):
        page_url, source = run_generate(proj_name, keyword, slug)
        if page_url:
            return (page_url, "seo_page")
        # Fell through; fall back to plain project URL.
        if plain_url:
            return (plain_url, f"plain_url_fallback:{source}")
        return ("", f"empty:{source}")
    # No landing-pages config or LLM didn't supply keyword/slug.
    if plain_url:
        return (plain_url, "plain_url_no_lp")
    return ("", "empty")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True,
                    help="Path to the plan JSON file (read+rewrite in place)")
    args = ap.parse_args()

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"[gen] plan file not found: {plan_path}", file=sys.stderr)
        return 2
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[gen] plan file unreadable: {e}", file=sys.stderr)
        return 2

    candidates = plan.get("candidates") or []
    if not candidates:
        print("[gen] plan has 0 candidates; nothing to do", flush=True)
        return 0

    projects = load_projects()

    for c in candidates:
        link_url, source = resolve_link(c, projects)
        c["link_url"] = link_url
        c["link_source"] = source
        print(f"[gen] candidate_id={c.get('candidate_id')} "
              f"link_url={link_url!r} source={source}", flush=True)

    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"[gen] plan rewritten with link_url for {len(candidates)} candidates",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
