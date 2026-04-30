#!/usr/bin/env python3
"""LinkedIn browser automation: read-only sidebar pre-check.

Usage:
    python3 linkedin_browser.py unread-dms

This is a read-only sidebar scan. NO Voyager API. NO scroll-and-expand loops.
NO programmatic login. ONE navigation (/messaging/) + ONE page.evaluate() to
read unread badges off the visible inbox, then close.

Per CLAUDE.md "LinkedIn: flagged patterns" carve-out (2026-04-29): a
read-only sidebar pre-check is permitted because its fingerprint is
indistinguishable from the existing mcp__linkedin-agent__ sessions (same
profile, same cookies, same headed Chrome binary). The 2026-04-17
restriction was caused by Voyager calls + permalink scroll loops, neither
of which appear here.

Connects to the running linkedin-agent's persistent profile at
~/.claude/browser-profiles/linkedin. Launches HEADED Chromium (per the
CLAUDE.md note that LinkedIn fingerprints headless aggressively). Holds
the linkedin-browser lock for the entire run; expects the caller (shell)
to have already done lock acquisition + ensure_browser_healthy so the MCP
Chrome is gone and the profile is free.

Output (stdout, JSON):
    {
        "ok": true,
        "url": "https://www.linkedin.com/messaging/",
        "total_threads": 13,
        "unread_count": 0,
        "threads": [
            {
                "partner": "Greg Newbegin",
                "preview": "Appreciate the back and forth, insightful",
                "thread_url": "https://www.linkedin.com/messaging/thread/.../"
                              | null (LinkedIn lazy-renders hrefs),
                "unread": false,
                "last_msg_time": "3:03 PM"
            },
            ...
        ]
    }

Failure shapes:
    {"ok": false, "error": "session_invalid", "url": "..."}
    {"ok": false, "error": "profile_locked", "detail": "..."}
    {"ok": false, "error": "navigation_failed", "detail": "..."}

Exits 0 on success, 1 on failure. The caller decides whether to early-exit
the pipeline based on `unread_count`.
"""

import atexit
import json
import os
import re
import sys
import time
from typing import Optional

PROFILE_DIR = os.path.expanduser("~/.claude/browser-profiles/linkedin")
LOCK_FILE = os.path.expanduser("~/.claude/linkedin-agent-lock.json")
LOCK_EXPIRY = 300  # Must match ~/.claude/hooks/linkedin-agent-lock.sh
LOCK_WAIT_MAX = 30  # seconds; pre-check should not block long
LOCK_POLL_INTERVAL = 2
VIEWPORT = {"width": 911, "height": 1016}

_LOCK_SESSION_ID = f"python:{os.getpid()}"
_LOCK_INHERITED = False
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


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
            try:
                context = p.chromium.launch_persistent_context(
                    PROFILE_DIR,
                    headless=False,
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


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    cmd = sys.argv[1]
    if cmd == "unread-dms":
        result = unread_dms()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("ok") else 1)
    print(f"Unknown command: {cmd}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
