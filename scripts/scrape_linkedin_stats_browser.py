#!/usr/bin/env python3
"""LinkedIn stats: programmatic CDP-attach scrape (replaces stats.sh Step 4).

Usage:
    SOCIAL_AUTOPOSTER_LINKEDIN_STATS=1 python3 scrape_linkedin_stats_browser.py \\
        [--limit 30] [--summary /tmp/linkedin_summary.json] [--quiet]

Replaces the Claude-driven LinkedIn stats leg of skill/stats.sh (Step 4) with
a pure Python pipeline. Same shape as scripts/discover_linkedin_candidates.py:
attach to the running linkedin-agent MCP's Chromium over CDP, reuse its
existing BrowserContext, open a page per target URL, run ONE evaluate() to
locate OUR comment and read its reaction count, close the page. The DB
update path goes through scrape_linkedin_stats.update_linkedin_stats — same
2-strike removal rule, same explicit "post unavailable" signal handling.

Cost of $1-3/run + 5-10 minutes of LLM time for stats.sh Step 4 drops to
~$0 + 3-5 minutes of pure browser time. The LLM in the old prompt was
acting as a for-loop, navigator, and JS injector — zero taste calls — so
removing it keeps the same data quality and just cuts spend + tokens.

Pre-conditions for this to work:
    1. The linkedin-agent MCP is currently running and has launched Chrome
       at least once this session (so DevToolsActivePort exists).
    2. The MCP's launchOptions.args includes --remote-debugging-port=0;
       see ~/.claude/browser-agent-configs/linkedin-agent.json.
    3. The user is logged in inside that browser. We do NOT log in.

Per CLAUDE.md "LinkedIn: flagged patterns" carve-out (2026-04-29): the
read-only DOM read is permitted because the request runs inside the same
Chrome the MCP already drives. We do click "Comment" / "Load more"
buttons inside the post's own comment block — that's the same set of
clicks a user makes to view comments, not the multi-page permalink
fan-out + scroll-loop pattern that triggered the 17 Apr restriction.

Output (stdout): the same structured summary line that update_stats.py
prints for Reddit/Twitter, so stats.sh's extract_field can parse it the
same way:
    LinkedIn: <T> total, <S> skipped, <C> checked, <U> updated, <D> deleted, <E> errors

When --summary is passed, also writes the JSON sidecar that
scrape_linkedin_stats.py used to write so existing stats.sh dashboard
plumbing keeps working unchanged:
    {"refreshed": N, "removed": N, "unavailable": N, "not_found": N}

Failure shapes (stderr JSON, exit 1):
    {"ok": false, "error": "session_invalid", "url": "..."}
    {"ok": false, "error": "mcp_not_running", "detail": "..."}
    {"ok": false, "error": "cdp_attach_failed", "detail": "..."}
    {"ok": false, "error": "no_eligible_posts"}
"""

import argparse
import json
import os
import random
import sys
import time
from typing import Optional

# Reuse the lock helper + login-URL detector from linkedin_browser, and the
# DevToolsActivePort reader from discover_linkedin_candidates. Same lock
# means concurrent helpers (search vs unread-dms vs stats) serialize on
# ~/.claude/linkedin-agent-lock.json; same port reader means we're consistent
# with how the working SERP-scrape locates the MCP.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from linkedin_browser import (  # noqa: E402
    _acquire_browser_lock,
    _is_login_or_checkpoint,
)
from discover_linkedin_candidates import (  # noqa: E402
    DEVTOOLS_ACTIVE_PORT,
    _read_devtools_port,
)


# Browser-side comment finder. Runs in page context via page.evaluate().
# Strategy mirrors the production-tested SCRAPE_JS that lived in stats.sh
# Step 4's heredoc, with two paths: known LinkedIn comment-entity selectors
# first, then a name-text walk-up fallback when LinkedIn obfuscates classes.
# Returns JSON.stringify(...) so the Python side can json.loads regardless
# of how the evaluate channel marshals nested objects.
_COMMENT_FINDER_JS = r"""
({ourName, contentPrefix}) => {
  const res = { reactions: 0, found: false, comment_text_preview: '' };

  // Strategy 1: known CSS selectors (LinkedIn occasionally obfuscates these).
  let commentContainers = Array.from(document.querySelectorAll(
    'article.comments-comment-entity, ' +
    'article.comments-comment-item, ' +
    '[data-id*="comment"], ' +
    '.comments-comment-list__comment'
  ));

  // Strategy 2: fallback — find leaves matching ourName, walk up to a
  // reaction-bearing ancestor.
  if (commentContainers.length === 0) {
    const nameEls = Array.from(document.querySelectorAll('*')).filter(el =>
      el.children.length === 0 && (el.textContent || '').trim() === ourName
    );
    for (const el of nameEls) {
      let node = el.parentElement;
      for (let i = 0; i < 10; i++) {
        if (!node) break;
        const t = node.innerText || '';
        if (t.match(/\d+\s+reaction/i) || node.tagName === 'ARTICLE' || node.getAttribute('data-id')) {
          commentContainers.push(node);
          break;
        }
        node = node.parentElement;
      }
    }
  }

  for (const container of commentContainers) {
    const containerText = container.innerText || container.textContent || '';
    const nameMatch = containerText.includes(ourName);
    const prefixClean = (contentPrefix || '').replace(/[^a-z0-9 ]/gi, '').substring(0, 60).toLowerCase();
    const containerClean = containerText.replace(/[^a-z0-9 ]/gi, '').substring(0, 500).toLowerCase();
    const contentMatch = prefixClean.length > 20 && containerClean.includes(prefixClean);

    if (nameMatch || contentMatch) {
      res.found = true;
      res.comment_text_preview = containerText.substring(0, 80);

      // Try aria-label button first.
      const reactionEl = container.querySelector(
        'button[aria-label*="reaction"], button[aria-label*="Reaction"], ' +
        'button[class*="reactions-count"], button[class*="social-bar"]'
      );
      if (reactionEl) {
        const label = reactionEl.getAttribute('aria-label') || '';
        const labelMatch = label.match(/([\d,]+)\s*[Rr]eaction/);
        if (labelMatch) {
          res.reactions = parseInt(labelMatch[1].replace(/,/g, ''), 10);
        } else {
          const num = parseInt((reactionEl.textContent || '').trim().replace(/,/g, ''), 10);
          if (!isNaN(num)) res.reactions = num;
        }
      }

      // Fallback: parse "N reactions" from container innerText.
      if (!res.reactions) {
        const reactMatch = containerText.match(/(\d+)\s+reaction/i);
        if (reactMatch) res.reactions = parseInt(reactMatch[1], 10);
      }

      break;
    }
  }

  return JSON.stringify(res);
}
"""

# Strings LinkedIn renders when the parent post is gone. If body innerText
# contains any of these, treat the post as unavailable on first detection
# (skip the 2-strike rule). Mirrors stats.sh Step 4 heredoc verbatim.
_UNAVAILABLE_SIGNALS = (
    "This post is unavailable",
    "This post isn't available",
    "This post is no longer available",
    "This content isn't available",
    "This content is no longer available",
    "Page not found",
    "We can't find the page",
)


def _strip_comment_urn(url: str) -> str:
    """Strip the ?commentUrn=... query param so LinkedIn renders the parent
    post correctly. The deep-link form sometimes redirects or fails to render
    the comment block; the bare /feed/update/<urn>/ form is reliable."""
    if not url:
        return url
    return url.split("?", 1)[0]


def _load_eligible_posts(conn, limit: int) -> list:
    """Same predicate stats.sh used for Step 4: active LinkedIn posts whose
    engagement_updated_at is null or older than 7 days. Returns a list of
    dicts with id, our_url, our_content."""
    cur = conn.execute(
        "SELECT id, our_url, our_content FROM posts "
        "WHERE platform='linkedin' AND status='active' AND our_url IS NOT NULL "
        "AND our_url LIKE '%%linkedin.com/feed/update/%%' "
        "AND (engagement_updated_at IS NULL OR "
        "     engagement_updated_at < NOW() - INTERVAL '7 days') "
        "ORDER BY id LIMIT %s",
        [limit],
    )
    rows = cur.fetchall()
    return [
        {
            "id": r["id"],
            "our_url": r["our_url"],
            "our_content": r["our_content"] or "",
        }
        for r in rows
    ]


def _scrape_one(page, post: dict, our_name: str, quiet: bool = False) -> dict:
    """Navigate to one post, locate OUR comment, read reactions. Always
    returns a dict shaped for scrape_linkedin_stats.update_linkedin_stats
    consumption: {url, reactions, found, unavailable?, signal?}.
    Side-effect-free on errors: the caller decides whether to count as a
    DOM error vs. session_invalid (which aborts the whole run).
    """
    raw_url = post["our_url"]
    clean_url = _strip_comment_urn(raw_url)

    try:
        page.goto(clean_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        return {
            "url": raw_url,
            "reactions": 0,
            "found": False,
            "_error": f"navigation_failed: {e}",
        }

    cur_url = page.url
    if _is_login_or_checkpoint(cur_url):
        return {
            "url": raw_url,
            "_session_invalid": True,
            "_landed": cur_url,
        }

    # Homepage / search redirect = LinkedIn refusing the post. Treat as
    # unavailable on first detection (matches the heredoc behavior of
    # propagating unavailable=true).
    if "/feed/update/" not in cur_url:
        return {
            "url": raw_url,
            "reactions": 0,
            "found": False,
            "unavailable": True,
            "signal": f"redirected to {cur_url}",
        }

    # Settle: comments don't render until the page sits a moment.
    page.wait_for_timeout(4000)

    # Early unavailable check on body innerText.
    try:
        body_text = page.evaluate(
            "() => (document.body && document.body.innerText) || ''"
        )
    except Exception:
        body_text = ""
    matched = next((s for s in _UNAVAILABLE_SIGNALS if s in body_text), None)
    if matched:
        return {
            "url": raw_url,
            "reactions": 0,
            "found": False,
            "unavailable": True,
            "signal": matched,
        }

    # Comments don't render until you interact with the page. Scroll then
    # click the post's own "Comment" affordance (NOT a reply box) to expand
    # the inline comment block. This is one click on the post itself, not a
    # multi-page fan-out, so it stays inside the 2026-04-29 carve-out.
    try:
        page.evaluate("() => window.scrollBy(0, 600)")
        page.wait_for_timeout(2000)
    except Exception:
        pass

    try:
        btn = page.query_selector('button[aria-label="Comment"]')
        if btn:
            try:
                btn.click(timeout=3000)
                page.wait_for_timeout(5000)
            except Exception:
                pass
    except Exception:
        pass

    # Expand earlier replies / load-more, best-effort.
    expand_selectors = [
        'button[aria-label*="Load more comments"]',
        'button[aria-label*="load more"]',
        'button[aria-label*="See previous replies"]',
        'button[aria-label*="Load previous replies"]',
    ]
    for sel in expand_selectors:
        try:
            btns = page.query_selector_all(sel)
        except Exception:
            btns = []
        for b in btns[:5]:  # cap per selector to avoid runaway click loops
            try:
                b.click(timeout=2000)
                page.wait_for_timeout(2000)
            except Exception:
                pass

    # Locate our comment + reactions.
    try:
        raw = page.evaluate(
            _COMMENT_FINDER_JS,
            {
                "ourName": our_name,
                "contentPrefix": (post["our_content"] or "")[:80],
            },
        )
    except Exception as e:
        return {
            "url": raw_url,
            "reactions": 0,
            "found": False,
            "_error": f"evaluate_failed: {e}",
        }

    try:
        result = json.loads(raw or "{}")
    except json.JSONDecodeError:
        result = {}

    if not quiet:
        print(
            f"  [{post['id']}] found={result.get('found', False)} "
            f"reactions={result.get('reactions', 0)}",
            flush=True,
        )

    return {
        "url": raw_url,
        "reactions": int(result.get("reactions", 0) or 0),
        "found": bool(result.get("found", False)),
        "comment_text_preview": result.get("comment_text_preview", ""),
    }


def run(limit: int = 30, summary_path: Optional[str] = None,
        quiet: bool = False) -> dict:
    """Main entry. Returns a dict with totals + per-URL results."""
    # 1. Load DB rows BEFORE attaching to the browser, so a DB outage doesn't
    # leave a half-attached Chrome and a held lock for nothing.
    import db as dbmod  # noqa: WPS433
    dbmod.load_env()
    db = dbmod.get_conn()
    try:
        posts = _load_eligible_posts(db, limit)
    except Exception as e:
        db.close()
        return {"ok": False, "error": "db_query_failed", "detail": str(e)}

    if not posts:
        db.close()
        return {
            "ok": True,
            "total": 0,
            "skipped": 0,
            "checked": 0,
            "updated": 0,
            "deleted": 0,
            "errors": 0,
            "results": [],
            "note": "no_eligible_posts",
        }

    # 2. LinkedIn name from config.json (matches stats.sh's lookup path).
    config_path = os.path.expanduser("~/social-autoposter/config.json")
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        our_name = cfg["accounts"]["linkedin"]["name"]
    except Exception:
        our_name = "Matthew Diakonov"

    # 3. Attach to MCP Chrome.
    port = _read_devtools_port()
    if port is None:
        db.close()
        return {
            "ok": False,
            "error": "mcp_not_running",
            "detail": (
                f"{DEVTOOLS_ACTIVE_PORT} is missing, empty, or pointing at a "
                "dead port. The linkedin-agent MCP must be running and have "
                "loaded Chrome at least once this session."
            ),
        }

    from playwright.sync_api import sync_playwright

    _acquire_browser_lock()

    results: list = []
    errors = 0
    session_invalid = False
    landed = None

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
        except Exception as e:
            db.close()
            return {
                "ok": False,
                "error": "cdp_attach_failed",
                "detail": f"connect_over_cdp(localhost:{port}) failed: {e}",
            }

        if not browser.contexts:
            try:
                browser.disconnect()
            except Exception:
                pass
            db.close()
            return {
                "ok": False,
                "error": "cdp_attach_failed",
                "detail": "browser.contexts is empty; MCP has no open context",
            }
        context = browser.contexts[0]

        page = None
        try:
            page = context.new_page()
            for i, post in enumerate(posts):
                if not quiet:
                    print(
                        f"[{i + 1}/{len(posts)}] "
                        f"id={post['id']} {post['our_url'][:90]}",
                        flush=True,
                    )
                r = _scrape_one(page, post, our_name, quiet=quiet)

                if r.get("_session_invalid"):
                    session_invalid = True
                    landed = r.get("_landed")
                    break

                if r.get("_error"):
                    errors += 1
                    if not quiet:
                        print(f"  ERROR: {r['_error']}", flush=True)
                    # Keep the row out of results — we don't want a flaky
                    # nav to look like a real "post unavailable" hit and
                    # bump deletion_detect_count.
                    r.pop("_error", None)
                else:
                    results.append({
                        k: v for k, v in r.items() if not k.startswith("_")
                    })

                # Human-pacing jitter between page loads (stats.sh used 5s
                # batched; we use 3-5s per URL which is more LinkedIn-like).
                if i + 1 < len(posts):
                    time.sleep(random.uniform(3.0, 5.0))
        finally:
            try:
                if page is not None:
                    page.close()
            except Exception:
                pass
            try:
                browser.disconnect()
            except Exception:
                pass

    if session_invalid:
        db.close()
        return {
            "ok": False,
            "error": "session_invalid",
            "url": landed,
        }

    # 4. Apply DB updates via the existing pure function. This is the same
    # write-path stats.sh has used since the leg was Claude-driven, so the
    # 2-strike rule and removal/unavailable accounting stay identical.
    from scrape_linkedin_stats import update_linkedin_stats  # noqa: WPS433
    db_summary = update_linkedin_stats(db, results, quiet=quiet)
    db.close()

    # Map DB summary into the standard counter shape.
    total = len(posts)
    checked = len(results)
    skipped = total - checked  # rows we never got to (session_invalid abort)
    updated = int(db_summary.get("matched", 0) or 0)
    deleted = int(db_summary.get("removed", 0) or 0)
    unavailable = int(db_summary.get("unavailable", 0) or 0)
    not_found = int(db_summary.get("unmatched", 0) or 0)

    # Sidecar JSON for stats.sh's existing dashboard plumbing.
    if summary_path:
        try:
            with open(summary_path, "w") as f:
                json.dump({
                    "refreshed":   updated,
                    "removed":     deleted,
                    "unavailable": unavailable,
                    "not_found":   not_found,
                }, f)
        except Exception as e:
            print(
                f"WARN: failed to write summary {summary_path}: {e}",
                file=sys.stderr,
            )

    return {
        "ok": True,
        "total": total,
        "skipped": skipped,
        "checked": checked,
        "updated": updated,
        "deleted": deleted,
        "errors": errors,
        "unavailable": unavailable,
        "not_found": not_found,
        "results": results,
    }


def main() -> None:
    # Guard: same env-var pattern as discover_linkedin_candidates.py /
    # linkedin_browser.py. Any caller that needs to run this sets the var
    # immediately before invocation; nothing else should fire it.
    if os.environ.get("SOCIAL_AUTOPOSTER_LINKEDIN_STATS") != "1":
        print(
            json.dumps({
                "ok": False,
                "error": "unauthorized_caller",
                "detail": (
                    "scrape_linkedin_stats_browser.py is invoked only by the "
                    "stats.sh Step 4 LinkedIn leg. Set "
                    "SOCIAL_AUTOPOSTER_LINKEDIN_STATS=1 from the caller if "
                    "this invocation is legitimate."
                ),
            }),
            file=sys.stderr,
        )
        sys.exit(2)

    parser = argparse.ArgumentParser(
        description="Programmatic LinkedIn comment-stats scrape (Step 4 replacement)."
    )
    parser.add_argument(
        "--limit", type=int, default=30,
        help="Max posts to refresh in one run (matches the old prompt's LIMIT 30).",
    )
    parser.add_argument(
        "--summary", default=None,
        help="Path to write {refreshed, removed, unavailable, not_found} JSON sidecar.",
    )
    parser.add_argument("--quiet", action="store_true", help="Minimal output.")
    args = parser.parse_args()

    result = run(limit=args.limit, summary_path=args.summary, quiet=args.quiet)

    if not result.get("ok"):
        # Hard failure: emit JSON to stderr, exit 1 so stats.sh logs it.
        print(json.dumps(result, indent=2), file=sys.stderr)
        sys.exit(1)

    # Success path: print the structured summary line stats.sh's
    # extract_field expects (matches Twitter/Reddit prefix shape).
    print(
        f"LinkedIn: {result['total']} total, "
        f"{result['skipped']} skipped, "
        f"{result['checked']} checked, "
        f"{result['updated']} updated, "
        f"{result['deleted']} deleted, "
        f"{result['errors']} errors"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
