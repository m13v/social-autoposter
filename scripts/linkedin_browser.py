#!/usr/bin/env python3
"""LinkedIn browser automation: read-only sidebar pre-check + search SERP read.

Usage:
    python3 linkedin_browser.py unread-dms
    python3 linkedin_browser.py search <vertical> <query>
        # vertical = people | content | companies

Both commands are read-only DOM scrapes: NO Voyager API, NO scroll-and-expand
loops, NO permalink fan-out, NO clicks/typing, NO programmatic login. Each
invocation does ONE navigation + ONE page.evaluate() then closes the context.

Per CLAUDE.md "LinkedIn: flagged patterns" carve-out (2026-04-29): a
read-only DOM read is permitted because its fingerprint is indistinguishable
from the existing mcp__linkedin-agent__ sessions (same profile, same cookies,
same headed Chrome binary). The 2026-04-17 restriction was caused by Voyager
calls + permalink scroll loops, neither of which appear here.

The search subcommand additionally enforces a rate-limit budget against the
linkedin_browser_searches table (see _check_rate_limit) per the 2026-04-29
research findings: ~30s min gap, ~40/day, ~150/month soft cap leaves headroom
under LinkedIn's ~300/month commercial-use wall on free accounts.

Connects to the running linkedin-agent's persistent profile at
~/.claude/browser-profiles/linkedin. Launches HEADED Chromium (per the
CLAUDE.md note that LinkedIn fingerprints headless aggressively). Holds
the linkedin-browser lock for the entire run; expects the caller (shell)
to have already done lock acquisition + ensure_browser_healthy so the MCP
Chrome is gone and the profile is free.

Output (stdout, JSON), unread-dms:
    {
        "ok": true,
        "url": "https://www.linkedin.com/messaging/",
        "total_threads": 13,
        "unread_count": 0,
        "threads": [...],
    }

Output (stdout, JSON), search:
    {
        "ok": true,
        "url": "https://www.linkedin.com/search/results/people/?keywords=...",
        "vertical": "people",
        "query": "founder rag retrieval",
        "result_count": 10,
        "results": [
            {
                "name": "...",            # people only
                "headline": "...",        # people only
                "location": "...",        # people only
                "profile_url": "...",     # people only
                "author": "...",          # content only
                "post_text": "...",       # content only (snippet)
                "post_url": "...",        # content only
                "company": "...",         # companies only
                "tagline": "...",         # companies only
                "company_url": "...",     # companies only
            },
            ...
        ],
    }

Failure shapes (both commands):
    {"ok": false, "error": "session_invalid", "url": "..."}
    {"ok": false, "error": "profile_locked", "detail": "..."}
    {"ok": false, "error": "navigation_failed", "detail": "..."}

Search-only failure shapes:
    {"ok": false, "error": "bad_vertical", "detail": "..."}
    {"ok": false, "error": "rate_limited", "reason": "min_gap|daily_cap|monthly_cap",
     "detail": "...", "retry_after_seconds": N}
    {"ok": false, "error": "db_unavailable", "detail": "..."}

Exits 0 on success, 1 on failure.
"""

import atexit
import json
import os
import random
import re
import sys
import time
import urllib.parse
from typing import Optional

PROFILE_DIR = os.path.expanduser("~/.claude/browser-profiles/linkedin")
LOCK_FILE = os.path.expanduser("~/.claude/linkedin-agent-lock.json")
LOCK_EXPIRY = 300  # Must match ~/.claude/hooks/linkedin-agent-lock.sh
LOCK_WAIT_MAX = 30  # seconds; pre-check should not block long
LOCK_POLL_INTERVAL = 2
VIEWPORT = {"width": 911, "height": 1016}
# linkedin-agent uses the system Google Chrome binary, not Playwright's
# bundled "Chrome for Testing". Profile was created/migrated by system
# Chrome and "Chrome for Testing" fails to open it (SIGTRAP / kill EPERM
# observed 2026-04-29). Match the agent's binary so the profile stays
# compatible.
SYSTEM_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

_LOCK_SESSION_ID = f"python:{os.getpid()}"
_LOCK_INHERITED = False
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
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


def _release_browser_lock():
    if _LOCK_INHERITED:
        return
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE) as f:
                lock = json.load(f)
            if lock.get("session_id") == _LOCK_SESSION_ID:
                os.remove(LOCK_FILE)
    except (json.JSONDecodeError, OSError):
        pass


atexit.register(_release_browser_lock)


def _acquire_browser_lock():
    """Mirror twitter_browser._acquire_browser_lock semantics."""
    global _LOCK_SESSION_ID, _LOCK_INHERITED
    deadline = time.time() + LOCK_WAIT_MAX
    while True:
        if os.path.exists(LOCK_FILE):
            try:
                with open(LOCK_FILE) as f:
                    lock = json.load(f)
                age = time.time() - lock.get("timestamp", 0)
                holder = lock.get("session_id", "")
                if age >= LOCK_EXPIRY:
                    break  # stale, take it
                if _UUID_RE.match(holder or ""):
                    # parent Claude session holds it; inherit
                    _LOCK_SESSION_ID = holder
                    _LOCK_INHERITED = True
                    break
                if time.time() >= deadline:
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "error": "profile_locked",
                                "detail": (
                                    f"holder={holder} age={int(age)}s "
                                    f"waited={LOCK_WAIT_MAX}s"
                                ),
                            }
                        )
                    )
                    sys.exit(1)
                time.sleep(LOCK_POLL_INTERVAL)
                continue
            except (json.JSONDecodeError, OSError):
                pass
        break
    with open(LOCK_FILE, "w") as f:
        json.dump(
            {"session_id": _LOCK_SESSION_ID, "timestamp": int(time.time())}, f
        )


def _is_login_or_checkpoint(url: str) -> bool:
    if not url:
        return True
    return any(
        marker in url
        for marker in (
            "/login",
            "/checkpoint",
            "/uas/login",
            "linkedin.com/authwall",
        )
    )


def unread_dms() -> dict:
    """Scan LinkedIn /messaging/ sidebar in headed mode, read-only."""
    from playwright.sync_api import sync_playwright

    _acquire_browser_lock()

    with sync_playwright() as p:
        # Persistent context = same profile/cookies/session as linkedin-agent.
        # Headed mode per CLAUDE.md (LinkedIn fingerprints headless).
        deadline = time.time() + LOCK_WAIT_MAX
        context = None
        last_err: Optional[Exception] = None
        while True:
            # Clear stale Singleton* before each attempt. The MCP-spawned
            # Chrome may have left these behind on a non-graceful exit;
            # ensure_browser_healthy in lock.sh also tries this but
            # there's still a race window.
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
                )
                break
            except Exception as e:
                last_err = e
                if time.time() >= deadline:
                    return {
                        "ok": False,
                        "error": "profile_locked",
                        "detail": f"launch_persistent_context failed: {e}",
                    }
                time.sleep(LOCK_POLL_INTERVAL)

        try:
            page = context.new_page()
            try:
                page.goto(
                    "https://www.linkedin.com/messaging/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except Exception as e:
                return {
                    "ok": False,
                    "error": "navigation_failed",
                    "detail": str(e),
                }

            # Settle: wait for the conversation list to render. LinkedIn's
            # messaging UI lazy-loads after DOMContentLoaded.
            try:
                page.wait_for_selector(
                    "ul.msg-conversations-container__conversations-list, "
                    "ul[class*='conversations-list'], "
                    "main [role='list']",
                    timeout=10000,
                )
            except Exception:
                pass  # we'll still try to read whatever's there
            page.wait_for_timeout(1500)

            cur_url = page.url
            if _is_login_or_checkpoint(cur_url):
                return {
                    "ok": False,
                    "error": "session_invalid",
                    "url": cur_url,
                }

            # Read sidebar. Strategy:
            #   - For each conversation list item, derive partner name
            #     (bolded participant), preview text, time, and unread state.
            #   - Unread signal: visual blue dot (.notification-badge--show)
            #     OR data-test-unread, NOT generic [aria-label*=unread].
            #     LinkedIn renders hover "Mark as unread" buttons that
            #     contain the substring 'unread' on every thread.
            #   - thread_url: try the <a href> if rendered; otherwise null.
            threads = page.evaluate(
                """
                () => {
                  const out = [];
                  // Find conversation list items. LinkedIn renders these as
                  // <li> inside the conversations list; fall back to any
                  // [role=listitem] anchored under the messaging main.
                  const candidates = document.querySelectorAll(
                    "ul.msg-conversations-container__conversations-list > li, "
                    + "ul[class*='conversations-list'] > li, "
                    + "main [role='listitem']"
                  );
                  for (const item of candidates) {
                    // Skip ad slots / non-conversation rows.
                    const link = item.querySelector(
                      "a.msg-conversation-listitem__link, a[href*='/messaging/thread/']"
                    );
                    const innerText = (item.innerText || "").trim();
                    if (!innerText) continue;

                    // Unread badge: blue dot. Avoid the broad
                    // [aria-label*=unread] selector which matches the
                    // hover "Mark as unread" affordance.
                    const blueDot = item.querySelector(
                      ".notification-badge--show, "
                      + "[data-test-unread='true'], "
                      + ".msg-conversation-card__unread-count, "
                      + ".notification-badge.notification-badge--show"
                    );
                    const unread = !!blueDot;

                    // Partner name: prefer h3 / participant-names node.
                    const nameEl = item.querySelector(
                      "h3, .msg-conversation-listitem__participant-names, "
                      + ".msg-conversation-card__participant-names"
                    );
                    const partner = nameEl
                      ? (nameEl.textContent || "").trim()
                      : "";

                    // Time element: usually a small time/timestamp span.
                    const timeEl = item.querySelector(
                      "time, .msg-conversation-listitem__time-stamp, "
                      + ".msg-conversation-card__time-stamp"
                    );
                    const time = timeEl
                      ? (timeEl.textContent || "").trim()
                      : "";

                    // Preview (snippet of last message). Take first text
                    // node after the participant name that isn't the time.
                    const previewEl = item.querySelector(
                      ".msg-conversation-card__message-snippet, "
                      + ".msg-conversation-listitem__message-snippet, "
                      + "p.msg-conversation-card__message-snippet"
                    );
                    let preview = previewEl
                      ? (previewEl.textContent || "").trim()
                      : "";
                    if (!preview) {
                      // Fallback: trim partner+time off the innerText.
                      preview = innerText
                        .replace(partner, "")
                        .replace(time, "")
                        .trim()
                        .slice(0, 200);
                    }

                    let threadUrl = null;
                    if (link) {
                      const href = link.getAttribute("href") || "";
                      if (href && /\\/messaging\\/thread\\//.test(href)) {
                        threadUrl = href.startsWith("http")
                          ? href
                          : ("https://www.linkedin.com" + href);
                      }
                    }

                    out.push({
                      partner,
                      preview: preview.slice(0, 200),
                      time,
                      thread_url: threadUrl,
                      unread,
                    });
                  }
                  return JSON.stringify(out);
                }
                """
            )
            try:
                threads_list = json.loads(threads or "[]")
            except json.JSONDecodeError:
                threads_list = []

            unread_count = sum(1 for t in threads_list if t.get("unread"))

            return {
                "ok": True,
                "url": cur_url,
                "total_threads": len(threads_list),
                "unread_count": unread_count,
                "threads": threads_list,
            }

        finally:
            try:
                context.close()
            except Exception:
                pass


def _open_db():
    """Lazy-import scripts.db so unread-dms callers don't need psycopg2.

    Raises ImportError on failure; callers convert to {"error":"db_unavailable"}.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import db as dbmod  # type: ignore  # noqa: WPS433
    dbmod.load_env()
    return dbmod.get_conn()


def _check_rate_limit() -> dict:
    """Return {"ok": True} if a new search is allowed, else a failure shape.

    Auto-creates the linkedin_browser_searches table on first use so this
    helper is self-contained (no separate migration step needed).
    Fails CLOSED on DB errors: if we can't enforce the budget we don't
    perform the search. Better to silently skip a cycle than to drift past
    the ~300/month wall and trigger a restriction.
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
        # Idempotent. Each statement is run separately because psycopg2
        # rejects multi-statement strings on simple cursors.
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
    failure. Note that if logging fails we silently let the row drop, which
    means the next rate-limit check will under-count. Acceptable: the
    monthly cap has 50% headroom under the actual wall.
    """
    try:
        conn = _open_db()
    except Exception as e:
        print(
            f"[linkedin_browser] _log_search: db open failed: {e}",
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
            f"[linkedin_browser] _log_search: insert failed: {e}",
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

_SEARCH_JS_CONTENT = r"""
() => {
  const out = [];
  const cards = document.querySelectorAll(
    "div.feed-shared-update-v2[data-urn], "
    + "div.update-components-update-v2, "
    + "[data-chameleon-result-urn]"
  );
  for (const c of Array.from(cards).slice(0, 25)) {
    const urn = c.getAttribute("data-urn")
      || c.getAttribute("data-chameleon-result-urn") || "";
    const actorLink = c.querySelector(
      "a.update-components-actor__meta-link, "
      + ".update-components-actor a[href*='/in/'], "
      + ".update-components-actor a[href*='/company/']"
    );
    const author = (() => {
      const a = c.querySelector(
        ".update-components-actor__title, "
        + ".update-components-actor__name, "
        + "span.feed-shared-actor__name"
      );
      return a ? (a.textContent || "").trim() : "";
    })();
    const text = (() => {
      const t = c.querySelector(
        ".update-components-text, .feed-shared-update-v2__description, "
        + ".update-components-update-v2__commentary"
      );
      return t ? (t.textContent || "").trim() : "";
    })();
    const activityMatch = urn.match(/activity:(\d+)/)
      || urn.match(/ugcPost:(\d+)/);
    const postUrl = activityMatch
      ? ("https://www.linkedin.com/feed/update/urn:li:activity:"
          + activityMatch[1] + "/")
      : "";
    if (!author && !text && !postUrl) continue;
    out.push({
      author: author.replace(/\s+/g, " ").slice(0, 200),
      post_text: text.replace(/\s+/g, " ").slice(0, 600),
      post_url: postUrl,
      actor_url: actorLink
        ? (actorLink.href || "").split("?")[0] : "",
    });
  }
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
    search_url = (
        f"https://www.linkedin.com/search/results/{vertical}/?keywords={encoded}"
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
    """Mirror unread_dms_with_retry: one retry on transient browser-target
    failures only. Do NOT retry on rate_limited / session_invalid / db_*."""
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
            f"[linkedin_browser] search transient failure attempt {attempt}: "
            f"{result.get('detail') or result.get('error')}; retrying...",
            file=sys.stderr,
        )
        time.sleep(2)
    return last_result


def unread_dms_with_retry(max_attempts: int = 2) -> dict:
    """Wrap unread_dms with one retry on TargetClosedError-style transient
    failures. The headed Chrome launch races against atexit lock release on
    the previous run; a single retry after a short delay clears most cases.
    """
    last_result: dict = {"ok": False, "error": "no_attempts"}
    for attempt in range(1, max_attempts + 1):
        try:
            result = unread_dms()
        except Exception as e:
            result = {
                "ok": False,
                "error": "exception",
                "detail": f"{type(e).__name__}: {e}",
                "attempt": attempt,
            }
        last_result = result
        # Only retry on transient browser-target failures, not on
        # session_invalid / profile_locked which won't self-heal.
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
            f"[linkedin_browser] transient failure attempt {attempt}: "
            f"{result.get('detail') or result.get('error')}; retrying...",
            file=sys.stderr,
        )
        time.sleep(2)
    return last_result


def main():
    # Guard: only authorized pipelines may invoke this helper. Other Claude
    # subprocess planners (post_reddit, post_twitter, etc.) auto-load
    # CLAUDE.md as system context, see this helper documented there, and
    # have wandered off-task to "smoke test" it — racing the linkedin
    # profile's SingletonLock and triggering server-side session
    # invalidation. The legitimate caller sets the matching env var
    # immediately before invoking; nothing else does.
    #
    # Allowed env vars:
    #   SOCIAL_AUTOPOSTER_LINKEDIN_PRECHECK=1  -> unread-dms (engage-dm-replies)
    #   SOCIAL_AUTOPOSTER_LINKEDIN_SEARCH=1    -> search (run-linkedin / discovery)
    authorized = (
        os.environ.get("SOCIAL_AUTOPOSTER_LINKEDIN_PRECHECK") == "1"
        or os.environ.get("SOCIAL_AUTOPOSTER_LINKEDIN_SEARCH") == "1"
    )
    if not authorized:
        print(
            json.dumps({
                "ok": False,
                "error": "unauthorized_caller",
                "detail": (
                    "linkedin_browser.py is invoked only by the "
                    "engage-dm-replies pre-check or the run-linkedin search "
                    "discovery pipeline. Set "
                    "SOCIAL_AUTOPOSTER_LINKEDIN_PRECHECK=1 (for unread-dms) "
                    "or SOCIAL_AUTOPOSTER_LINKEDIN_SEARCH=1 (for search) "
                    "from the caller if this invocation is legitimate."
                ),
            }),
            file=sys.stderr,
        )
        sys.exit(2)
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    cmd = sys.argv[1]
    if cmd == "unread-dms":
        result = unread_dms_with_retry()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("ok") else 1)
    if cmd == "search":
        if len(sys.argv) < 4:
            print(
                "Usage: linkedin_browser.py search "
                "<people|content|companies> <query>",
                file=sys.stderr,
            )
            sys.exit(2)
        vertical = sys.argv[2]
        query = " ".join(sys.argv[3:])
        result = search_with_retry(vertical, query)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("ok") else 1)
    print(f"Unknown command: {cmd}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
