#!/usr/bin/env python3
"""LinkedIn SERP discovery: read-only Phase A search-page scrape.

Usage:
    python3 discover_linkedin_candidates.py <vertical> <query>
        # vertical = people | content | companies

Designed to replace the Claude-driven SERP nav inside skill/run-linkedin.sh
Phase A. Pulls ONE page of LinkedIn search results, extracts candidate
metadata + engagement, prints a JSON envelope to stdout. The shell pipes
that envelope to log_linkedin_search_attempts.py and
score_linkedin_candidates.py (existing consumers).

Read-only DOM scrape: NO Voyager API, NO scroll-and-expand loops, NO
permalink fan-out, NO clicks/typing, NO programmatic login. ONE goto +
ONE page.evaluate() then close the context.

Per CLAUDE.md "LinkedIn: flagged patterns" carve-out (2026-04-29): a
read-only DOM read is permitted because its fingerprint is indistinguishable
from the existing mcp__linkedin-agent__ sessions (same profile, same
cookies, same headed Chrome binary). The 2026-04-17 restriction was caused
by Voyager calls + permalink scroll loops, neither of which appear here.

Rate-limited against linkedin_browser_searches per the 2026-04-29 research:
~30s min gap, ~40/day, ~150/month soft cap leaves headroom under LinkedIn's
~300/month commercial-use wall on free accounts. Fails CLOSED on DB errors:
if we cannot enforce the budget we do not perform the search.

Shares the persistent-profile launcher + lock helpers with linkedin_browser
(unread-dms pre-check), so both tools cooperate on
~/.claude/linkedin-agent-lock.json and ~/.claude/browser-profiles/linkedin.

Output (stdout, JSON):
    {
        "ok": true,
        "url": "https://www.linkedin.com/search/results/people/?keywords=...",
        "vertical": "people",
        "query": "founder rag retrieval",
        "result_count": 10,
        "results": [...],
        "rate_budget": {"daily_used": N, "daily_cap": N,
                        "monthly_used": N, "monthly_cap": N},
    }

Failure shapes:
    {"ok": false, "error": "session_invalid", "url": "..."}
    {"ok": false, "error": "profile_locked", "detail": "..."}
    {"ok": false, "error": "navigation_failed", "detail": "..."}
    {"ok": false, "error": "bad_vertical", "detail": "..."}
    {"ok": false, "error": "empty_query", "detail": ""}
    {"ok": false, "error": "rate_limited",
     "reason": "min_gap|daily_cap|monthly_cap",
     "detail": "...", "retry_after_seconds": N}
    {"ok": false, "error": "db_unavailable", "detail": "..."}

Exits 0 on success, 1 on failure.
"""

import json
import os
import random
import sys
import time
import urllib.parse
from typing import Optional

# Shared persistent-profile launcher + lock helpers live in linkedin_browser
# so the unread-dms pre-check and this discovery tool cooperate on the same
# Chrome profile and the same ~/.claude/linkedin-agent-lock.json. Importing
# linkedin_browser also registers its atexit handler that releases the lock
# on process exit — covers us too.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from linkedin_browser import (  # noqa: E402
    LOCK_POLL_INTERVAL,
    LOCK_WAIT_MAX,
    PROFILE_DIR,
    SYSTEM_CHROME,
    VIEWPORT,
    _acquire_browser_lock,
    _is_login_or_checkpoint,
)

# Search rate-limit budget. Picked from 2026-04-29 research synthesis: vendor
# consensus is 8-25s gap + 80-100 profile views/hour + <500/day at high-volume
# scraping. We scale that down hard because we are using one real human
# account, not a farm. The hard ceiling is LinkedIn's free-tier commercial-use
# wall at ~300 people-searches/month — we target half of that.
SEARCH_MIN_GAP_SECONDS = 30
SEARCH_DAILY_CAP = 40         # 10 searches × ~4 sessions/day
SEARCH_MONTHLY_CAP = 150      # 50% headroom under LinkedIn's ~300/month wall
SEARCH_VERTICALS = ("people", "content", "companies")
SEARCH_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS linkedin_browser_searches (
    id        SERIAL PRIMARY KEY,
    query     TEXT NOT NULL,
    vertical  TEXT NOT NULL,
    ok        BOOLEAN NOT NULL,
    error     TEXT,
    ran_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lbs_ran_at
    ON linkedin_browser_searches(ran_at DESC);
"""


def _open_db():
    """Lazy-import scripts.db; raises ImportError on failure."""
    import db as dbmod  # type: ignore  # noqa: WPS433
    dbmod.load_env()
    return dbmod.get_conn()


def _check_rate_limit() -> dict:
    """Return {"ok": True, ...} if a new search is allowed, else a failure shape.

    Auto-creates linkedin_browser_searches on first use. Fails CLOSED on DB
    errors: if we can't enforce the budget we don't perform the search.
    Better to silently skip a cycle than to drift past the ~300/month wall
    and trigger a restriction.
    """
    try:
        conn = _open_db()
    except Exception as e:
        return {
            "ok": False,
            "error": "db_unavailable",
            "detail": f"{type(e).__name__}: {e}",
        }
    try:
        for stmt in [s.strip() for s in SEARCH_TABLE_DDL.split(";") if s.strip()]:
            conn.execute(stmt)
        conn.commit()

        cur = conn.execute(
            "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(ran_at)))::INT AS gap "
            "FROM linkedin_browser_searches"
        )
        row = cur.fetchone()
        gap = row["gap"] if row and row["gap"] is not None else None
        if gap is not None and gap < SEARCH_MIN_GAP_SECONDS:
            return {
                "ok": False,
                "error": "rate_limited",
                "reason": "min_gap",
                "detail": (
                    f"last search was {gap}s ago, "
                    f"need {SEARCH_MIN_GAP_SECONDS}s gap"
                ),
                "retry_after_seconds": SEARCH_MIN_GAP_SECONDS - gap,
            }

        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM linkedin_browser_searches "
            "WHERE ran_at >= NOW() - INTERVAL '24 hours'"
        )
        daily = cur.fetchone()["n"]
        if daily >= SEARCH_DAILY_CAP:
            return {
                "ok": False,
                "error": "rate_limited",
                "reason": "daily_cap",
                "detail": f"{daily} searches in last 24h, cap {SEARCH_DAILY_CAP}",
                "retry_after_seconds": 3600,
            }

        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM linkedin_browser_searches "
            "WHERE ran_at >= date_trunc('month', NOW())"
        )
        monthly = cur.fetchone()["n"]
        if monthly >= SEARCH_MONTHLY_CAP:
            return {
                "ok": False,
                "error": "rate_limited",
                "reason": "monthly_cap",
                "detail": (
                    f"{monthly} searches this month, cap {SEARCH_MONTHLY_CAP}"
                ),
                "retry_after_seconds": 86400,
            }
        return {"ok": True, "daily_used": daily, "monthly_used": monthly}
    except Exception as e:
        return {
            "ok": False,
            "error": "db_unavailable",
            "detail": f"{type(e).__name__}: {e}",
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _log_search(query: str, vertical: str, ok: bool, error: Optional[str]) -> None:
    """Best-effort write of one row to linkedin_browser_searches.

    Never raises: a failed log must not turn a successful search into a
    failure. Note that if logging fails the next rate-limit check will
    under-count. Acceptable: the monthly cap has 50% headroom under the
    actual wall.
    """
    try:
        conn = _open_db()
    except Exception as e:
        print(
            f"[discover_linkedin_candidates] _log_search: db open failed: {e}",
            file=sys.stderr,
        )
        return
    try:
        conn.execute(
            "INSERT INTO linkedin_browser_searches "
            "(query, vertical, ok, error) VALUES (%s, %s, %s, %s)",
            [query, vertical, ok, error],
        )
        conn.commit()
    except Exception as e:
        print(
            f"[discover_linkedin_candidates] _log_search: insert failed: {e}",
            file=sys.stderr,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


# DOM extractors per vertical. Each is a single querySelectorAll + map, with
# multiple selector fallbacks because LinkedIn's class names rotate. Returns
# JSON.stringify(...) so the Python side can json.loads regardless of how the
# evaluate channel marshals nested objects. Limit to the first 25 cards on the
# page — anything beyond that requires scrolling, which we explicitly do not
# do.
#
# Provenance:
#   _SEARCH_JS_CONTENT  — lifted from skill/run-linkedin.sh (production-tested).
#   _SEARCH_JS_PEOPLE   — UNVERIFIED. Selectors based on widely-documented
#                         LinkedIn patterns + multiple fallbacks. Smoke-test
#                         against a real SERP before relying on the output.
#   _SEARCH_JS_COMPANIES — UNVERIFIED. Same caveat as people.
#
# Reconciliation procedure for the UNVERIFIED extractors (do this once,
# then update this block to mark them VERIFIED with the date):
#   Preconditions:
#     - linkedin-agent has been idle >= 1 hour. Check
#       ~/.playwright-mcp/linkedin-agent/page-*.yml mtimes.
#     - The persistent profile is logged in. Do NOT trigger a probe like
#       "navigate to LinkedIn and tell me what you see" — that prompt itself
#       is the high-risk behavior that invalidated cookies on 2026-04-29.
#       If the session is dead, wait for the next normal pipeline cycle to
#       re-auth, then resume reconciliation in a fresh hour.
#   Steps (use the linkedin-agent MCP, NOT this script — the script logs to
#   linkedin_browser_searches and burns the rate budget for nothing):
#     1. mcp__linkedin-agent__browser_navigate to
#        https://www.linkedin.com/search/results/people/?keywords=founder%20ai
#     2. mcp__linkedin-agent__browser_evaluate, paste _SEARCH_JS_PEOPLE
#        verbatim (including the JSON.stringify wrap). JSON.parse the
#        returned string.
#        Accept criterion: >= 5 entries with non-empty name AND profile_url.
#        Reject: [] or rows with all-empty fields → snapshot the page,
#        find the live card class names, patch the querySelectorAll lists.
#        Keep existing fallback selectors at the END of each list to stay
#        compatible with the older layout.
#     3. Repeat for /search/results/companies/?keywords=founder%20ai with
#        _SEARCH_JS_COMPANIES. Same accept criterion (>= 5 cards with
#        company AND company_url).
#   Hard limits during reconciliation: 2 navigations total, no
#   close-and-reopen of the agent, no scroll, no clicks. Anything more is
#   the same fingerprint pattern that triggered the 2026-04-29 lockouts.
_SEARCH_JS_PEOPLE = r"""
() => {
  const out = [];
  const cards = document.querySelectorAll(
    "div.search-results-container li div.entity-result, "
    + "li.reusable-search__result-container, "
    + "[data-chameleon-result-urn]"
  );
  for (const c of Array.from(cards).slice(0, 25)) {
    const link = c.querySelector(
      "a[href*='/in/'].app-aware-link, a[href*='/in/']"
    );
    const profileUrl = link
      ? (link.href || link.getAttribute("href") || "")
      : "";
    const nameEl = c.querySelector(
      ".entity-result__title-text, .entity-result__title-line, "
      + "span[aria-hidden='true']"
    );
    const name = nameEl ? (nameEl.textContent || "").trim() : "";
    const headlineEl = c.querySelector(
      ".entity-result__primary-subtitle, .t-14.t-black.t-normal"
    );
    const headline = headlineEl
      ? (headlineEl.textContent || "").trim() : "";
    const locEl = c.querySelector(
      ".entity-result__secondary-subtitle, .t-14.t-normal"
    );
    const location = locEl ? (locEl.textContent || "").trim() : "";
    if (!name && !profileUrl) continue;
    out.push({
      name: name.replace(/\s+/g, " ").slice(0, 200),
      headline: headline.replace(/\s+/g, " ").slice(0, 300),
      location: location.replace(/\s+/g, " ").slice(0, 200),
      profile_url: profileUrl.split("?")[0],
    });
  }
  return JSON.stringify(out);
}
"""

# Content-search extractor: lifted verbatim (modulo JSON.stringify wrap) from
# skill/run-linkedin.sh Phase A, which has been the production scraper since
# the post-restriction rebuild. Richer than a basic title/href grab: pulls
# activity URN with regex fallbacks, author follower count, post age in
# hours, and reaction/comment/repost counts from social-details-social-counts.
# Keep this in sync if either side changes.
_SEARCH_JS_CONTENT = r"""
() => {
  const out = [];
  const containers = document.querySelectorAll(
    'div.feed-shared-update-v2, '
    + 'div[data-urn*="urn:li:activity"], '
    + 'div[data-urn*="urn:li:share"], '
    + 'div[data-urn*="urn:li:ugcPost"]'
  );
  const seenUrns = new Set();
  const re = /(activity|share|ugcPost)[:_-](\d{16,19})/gi;

  function parseRelativeAge(txt) {
    if (!txt) return null;
    const m = txt.match(/(\d+)\s*(s|m|h|d|w|mo|y)/i);
    if (!m) return null;
    const n = parseInt(m[1], 10);
    const unit = m[2].toLowerCase();
    const map = { s: 1/3600, m: 1/60, h: 1, d: 24, w: 24*7, mo: 24*30, y: 24*365 };
    return n * (map[unit] || 0);
  }
  function parseCount(txt) {
    if (!txt) return 0;
    const t = txt.replace(/,/g, '').trim();
    const m = t.match(/([\d.]+)\s*([KkMm]?)/);
    if (!m) return 0;
    const n = parseFloat(m[1]);
    const mult = m[2].toLowerCase() === 'k' ? 1000
      : (m[2].toLowerCase() === 'm' ? 1_000_000 : 1);
    return Math.round(n * mult);
  }

  Array.from(containers).slice(0, 25).forEach(el => {
    let activityId = null;
    const urns = new Set();
    const dataUrn = el.getAttribute('data-urn') || '';
    let m;
    re.lastIndex = 0;
    while ((m = re.exec(dataUrn)) !== null) {
      urns.add(m[2]);
      if (m[1].toLowerCase() === 'activity' && !activityId) activityId = m[2];
    }
    if (!activityId) {
      el.querySelectorAll(
        '[data-urn], a[href*="urn:li"], a[href*="/feed/update/"]'
      ).forEach(d => {
        const v = (d.getAttribute('data-urn') || d.getAttribute('href') || '');
        re.lastIndex = 0;
        let mm;
        while ((mm = re.exec(v)) !== null) {
          urns.add(mm[2]);
          if (mm[1].toLowerCase() === 'activity' && !activityId) activityId = mm[2];
        }
      });
    }
    if (!activityId || seenUrns.has(activityId)) return;
    seenUrns.add(activityId);

    const authorAnchor = el.querySelector(
      'a[href*="/in/"], a[data-control-name*="actor"]'
    );
    const authorName = (el.querySelector(
      '.update-components-actor__name, span.feed-shared-actor__name'
    )?.textContent || '').trim();
    const authorUrl = authorAnchor ? authorAnchor.href : null;
    let authorFollowers = 0;
    const supplementary = el.querySelector(
      '.update-components-actor__supplementary-actor-info, '
      + '.feed-shared-actor__sub-description'
    );
    if (supplementary) {
      const fm = (supplementary.textContent || '').match(
        /([\d.,]+[KkMm]?)\s*follower/
      );
      if (fm) authorFollowers = parseCount(fm[1]);
    }

    const textEl = el.querySelector(
      '.update-components-text, .feed-shared-update-v2__description, '
      + 'span.break-words'
    );
    const postText = (textEl ? textEl.textContent : '').trim().slice(0, 500);

    const timeEl = el.querySelector(
      'time, .update-components-actor__sub-description, '
      + 'span.feed-shared-actor__sub-description'
    );
    const ageText = timeEl ? timeEl.textContent.trim() : '';
    const ageHours = parseRelativeAge(ageText);

    const social = el.querySelector(
      '.social-details-social-counts, .social-action-counts, '
      + '.update-v2-social-activity'
    );
    let reactions = 0, comments = 0, reposts = 0;
    if (social) {
      const reactEl = social.querySelector(
        '[aria-label*="reaction" i], '
        + '.social-details-social-counts__reactions-count'
      );
      if (reactEl) reactions = parseCount(
        reactEl.textContent || reactEl.getAttribute('aria-label') || ''
      );
      const commentEl = social.querySelector(
        '[aria-label*="comment" i], '
        + 'li.social-details-social-counts__comments'
      );
      if (commentEl) comments = parseCount(
        commentEl.textContent || commentEl.getAttribute('aria-label') || ''
      );
      const repostEl = social.querySelector(
        '[aria-label*="repost" i], '
        + 'li.social-details-social-counts__item--right-aligned'
      );
      if (repostEl) reposts = parseCount(
        repostEl.textContent || repostEl.getAttribute('aria-label') || ''
      );
    }

    out.push({
      post_url: 'https://www.linkedin.com/feed/update/urn:li:activity:'
        + activityId + '/',
      activity_id: activityId,
      all_urns: Array.from(urns),
      author_name: authorName || null,
      author_profile_url: authorUrl,
      author_followers: authorFollowers || null,
      post_text: postText,
      age_hours: ageHours,
      reactions: reactions,
      comments: comments,
      reposts: reposts,
      age_text: ageText
    });
  });
  return JSON.stringify(out);
}
"""

_SEARCH_JS_COMPANIES = r"""
() => {
  const out = [];
  const cards = document.querySelectorAll(
    "div.search-results-container li div.entity-result, "
    + "li.reusable-search__result-container, "
    + "[data-chameleon-result-urn]"
  );
  for (const c of Array.from(cards).slice(0, 25)) {
    const link = c.querySelector(
      "a[href*='/company/'].app-aware-link, a[href*='/company/']"
    );
    const url = link ? (link.href || link.getAttribute("href") || "") : "";
    const nameEl = c.querySelector(
      ".entity-result__title-text, .entity-result__title-line, "
      + "span[aria-hidden='true']"
    );
    const name = nameEl ? (nameEl.textContent || "").trim() : "";
    const taglineEl = c.querySelector(
      ".entity-result__primary-subtitle, .t-14.t-black.t-normal"
    );
    const tagline = taglineEl ? (taglineEl.textContent || "").trim() : "";
    if (!name && !url) continue;
    out.push({
      company: name.replace(/\s+/g, " ").slice(0, 200),
      tagline: tagline.replace(/\s+/g, " ").slice(0, 300),
      company_url: url.split("?")[0],
    });
  }
  return JSON.stringify(out);
}
"""

_SEARCH_JS_BY_VERTICAL = {
    "people": _SEARCH_JS_PEOPLE,
    "content": _SEARCH_JS_CONTENT,
    "companies": _SEARCH_JS_COMPANIES,
}


def search(vertical: str, query: str) -> dict:
    """Read one page of LinkedIn search results, headed, read-only.

    ONE goto, ONE evaluate, close. No scrolling, no clicks. Rate-limited
    against linkedin_browser_searches; fails closed if the DB is reachable
    but the budget is exhausted.
    """
    if vertical not in SEARCH_VERTICALS:
        return {
            "ok": False,
            "error": "bad_vertical",
            "detail": f"got {vertical!r}; want one of {SEARCH_VERTICALS}",
        }
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "empty_query", "detail": ""}

    rate = _check_rate_limit()
    if not rate.get("ok"):
        return rate

    from playwright.sync_api import sync_playwright

    _acquire_browser_lock()

    encoded = urllib.parse.quote(query)
    # Content searches sort by date_posted to match skill/run-linkedin.sh
    # Phase A behavior — fresh posts > stale ones for engagement work.
    suffix = "&sortBy=date_posted" if vertical == "content" else ""
    search_url = (
        f"https://www.linkedin.com/search/results/{vertical}/"
        f"?keywords={encoded}{suffix}"
    )

    with sync_playwright() as p:
        deadline = time.time() + LOCK_WAIT_MAX
        context = None
        last_err: Optional[Exception] = None
        while True:
            for fname in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                try:
                    os.remove(os.path.join(PROFILE_DIR, fname))
                except OSError:
                    pass
            try:
                context = p.chromium.launch_persistent_context(
                    PROFILE_DIR,
                    headless=False,
                    executable_path=(
                        SYSTEM_CHROME if os.path.exists(SYSTEM_CHROME) else None
                    ),
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--window-position=3953,-1032",
                        "--window-size=911,1016",
                    ],
                    viewport=VIEWPORT,
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                    extra_http_headers={
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                break
            except Exception as e:
                last_err = e
                if time.time() >= deadline:
                    err = {
                        "ok": False,
                        "error": "profile_locked",
                        "detail": f"launch_persistent_context failed: {e}",
                    }
                    _log_search(query, vertical, ok=False, error="profile_locked")
                    return err
                time.sleep(LOCK_POLL_INTERVAL)

        try:
            page = context.new_page()
            try:
                page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except Exception as e:
                _log_search(query, vertical, ok=False, error="navigation_failed")
                return {
                    "ok": False,
                    "error": "navigation_failed",
                    "detail": str(e),
                }

            # Settle: search results lazy-render after DOMContentLoaded.
            # Selectors reflect both the post-2023 layout (entity-result) and
            # the older (reusable-search__result-container).
            try:
                page.wait_for_selector(
                    "div.search-results-container, "
                    "main[aria-label*='Search'], "
                    "div.feed-shared-update-v2",
                    timeout=10000,
                )
            except Exception:
                pass  # extractor will return [] if nothing rendered

            # Random 1-3s human-pacing delay before reading the DOM. Per
            # 2026-04-29 research; not a fingerprint cure, but cheap.
            page.wait_for_timeout(random.randint(1000, 3000))

            cur_url = page.url
            if _is_login_or_checkpoint(cur_url):
                _log_search(query, vertical, ok=False, error="session_invalid")
                return {
                    "ok": False,
                    "error": "session_invalid",
                    "url": cur_url,
                }

            raw = page.evaluate(_SEARCH_JS_BY_VERTICAL[vertical])
            try:
                results = json.loads(raw or "[]")
            except json.JSONDecodeError:
                results = []

            _log_search(query, vertical, ok=True, error=None)
            return {
                "ok": True,
                "url": cur_url,
                "vertical": vertical,
                "query": query,
                "result_count": len(results),
                "results": results,
                "rate_budget": {
                    "daily_used": rate.get("daily_used"),
                    "daily_cap": SEARCH_DAILY_CAP,
                    "monthly_used": rate.get("monthly_used"),
                    "monthly_cap": SEARCH_MONTHLY_CAP,
                },
            }

        finally:
            try:
                context.close()
            except Exception:
                pass


def search_with_retry(vertical: str, query: str, max_attempts: int = 2) -> dict:
    """One retry on transient browser-target failures only. Do NOT retry on
    rate_limited / session_invalid / db_*."""
    last_result: dict = {"ok": False, "error": "no_attempts"}
    for attempt in range(1, max_attempts + 1):
        try:
            result = search(vertical, query)
        except Exception as e:
            result = {
                "ok": False,
                "error": "exception",
                "detail": f"{type(e).__name__}: {e}",
                "attempt": attempt,
            }
        last_result = result
        err = (result.get("error") or "").lower()
        detail = (result.get("detail") or "").lower()
        transient = (
            "targetclosed" in detail
            or "target page" in detail
            or "browser has been closed" in detail
            or err == "navigation_failed"
        )
        if result.get("ok") or not transient or attempt >= max_attempts:
            if attempt > 1:
                result["retry_attempt"] = attempt
            return result
        print(
            f"[discover_linkedin_candidates] transient failure attempt "
            f"{attempt}: {result.get('detail') or result.get('error')}; "
            f"retrying...",
            file=sys.stderr,
        )
        time.sleep(2)
    return last_result


def main():
    # Guard: only authorized pipelines may invoke this helper. Other Claude
    # subprocess planners auto-load CLAUDE.md as system context, see this
    # helper documented there, and have wandered off-task to "smoke test"
    # it — racing the linkedin profile's SingletonLock and triggering
    # server-side session invalidation. The legitimate caller sets the
    # matching env var immediately before invoking; nothing else does.
    if os.environ.get("SOCIAL_AUTOPOSTER_LINKEDIN_SEARCH") != "1":
        print(
            json.dumps({
                "ok": False,
                "error": "unauthorized_caller",
                "detail": (
                    "discover_linkedin_candidates.py is invoked only by the "
                    "run-linkedin Phase A discovery pipeline. Set "
                    "SOCIAL_AUTOPOSTER_LINKEDIN_SEARCH=1 from the caller if "
                    "this invocation is legitimate."
                ),
            }),
            file=sys.stderr,
        )
        sys.exit(2)
    if len(sys.argv) < 3:
        print(
            "Usage: discover_linkedin_candidates.py "
            "<people|content|companies> <query>",
            file=sys.stderr,
        )
        sys.exit(2)
    vertical = sys.argv[1]
    query = " ".join(sys.argv[2:])
    result = search_with_retry(vertical, query)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
