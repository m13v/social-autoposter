#!/usr/bin/env python3
"""LinkedIn auth health check and self-healing.

Checks if the LinkedIn persistent browser profile has a valid session.
If invalid, re-authenticates using keychain credentials.
Cookies auto-persist via Chrome's persistent profile (no manual export needed).

Usage:
    python3 scripts/linkedin_auth_check.py          # check + heal if needed
    python3 scripts/linkedin_auth_check.py --check   # check only, exit 1 if invalid
    python3 scripts/linkedin_auth_check.py --force   # force re-auth even if valid

Exit codes:
    0 = session is valid (or was healed)
    1 = session is invalid and could not be healed
    2 = session was invalid but successfully healed
    3 = rate limited (429) or account restricted; cooldown file written
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

PROFILE_DIR = os.path.expanduser("~/.claude/browser-profiles/linkedin")
VIEWPORT = {"width": 911, "height": 1016}


def log(msg: str) -> None:
    print(f"[linkedin-auth] {msg}", file=sys.stderr)


def get_li_at_from_cdp(port: int) -> str | None:
    """Extract li_at cookie value from a running browser via CDP."""
    try:
        import websocket
        resp = urllib.request.urlopen(f"http://localhost:{port}/json", timeout=2)
        pages = json.loads(resp.read())
        linkedin_ws = None
        for p in pages:
            if "linkedin.com" in p.get("url", "") and "login" not in p.get("url", ""):
                linkedin_ws = p.get("webSocketDebuggerUrl")
                break
        if not linkedin_ws:
            for p in pages:
                if "linkedin.com" in p.get("url", ""):
                    linkedin_ws = p.get("webSocketDebuggerUrl")
                    break
        if not linkedin_ws:
            return None

        ws = websocket.create_connection(linkedin_ws, timeout=5)
        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        result = json.loads(ws.recv())
        ws.close()
        for cookie in result.get("result", {}).get("cookies", []):
            if cookie["name"] == "li_at" and "linkedin.com" in cookie.get("domain", ""):
                return cookie["value"]
    except Exception:
        pass
    return None


def check_session_valid() -> bool | str:
    """Check if the LinkedIn session is valid.

    First tries to get li_at from a running browser via CDP.
    Falls back to launching a headless persistent context and navigating.

    Returns:
        True = session valid
        False = session invalid
        "rate_limited" = 429 detected, cooldown written
    """
    # Try CDP first (fast, non-destructive)
    cdp_port = find_linkedin_cdp_port()
    if cdp_port:
        li_at = get_li_at_from_cdp(cdp_port)
        if li_at:
            result = check_cookie_value(li_at)
            if result == "rate_limited":
                return "rate_limited"
            return result

    # Fallback: launch persistent profile and check navigation
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                PROFILE_DIR,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
                viewport=VIEWPORT,
            )
            page = ctx.new_page()
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            url = page.url
            ctx.close()

            if "login" in url or "uas/" in url or "checkpoint" in url:
                log(f"LinkedIn redirected to login: {url}")
                return False
            log("LinkedIn session is valid (navigation check)")
            return True
    except Exception as e:
        log(f"Session check via profile launch failed: {e}")
        # If we can't check (e.g. profile locked by running browser), assume valid
        if "Browser is already in use" in str(e):
            log("Profile locked by running browser, assuming session is valid")
            return True
        return False


def check_cookie_value(li_at: str) -> bool:
    """Validate a li_at cookie value against LinkedIn's servers."""
    try:
        req = urllib.request.Request(
            "https://www.linkedin.com/feed/",
            method="GET",
            headers={
                "Cookie": f"li_at={li_at}",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        )
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
        resp = opener.open(req, timeout=15)
        final_url = resp.geturl()

        if "login" in final_url or "uas/" in final_url or "checkpoint" in final_url:
            log(f"LinkedIn redirected to login: {final_url}")
            return False
        if resp.status == 200:
            log("LinkedIn session is valid")
            return True
        log(f"LinkedIn returned status {resp.status}")
        return False
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            log(f"LinkedIn returned {e.code}, session invalid")
            return False
        if e.code == 429:
            log(f"LinkedIn returned 429, account is rate limited")
            # Write cooldown so cron runs skip for 2 hours
            try:
                from linkedin_cooldown import set_cooldown
                from datetime import datetime, timedelta, timezone
                set_cooldown("429 rate limit during auth check", datetime.now(timezone.utc) + timedelta(hours=2))
            except Exception:
                pass
            return "rate_limited"
        if e.code >= 500:
            log(f"LinkedIn returned {e.code}, assuming session is OK (server issue)")
            return True
        log(f"LinkedIn HTTP error: {e.code}")
        return False
    except Exception as e:
        log(f"Error checking LinkedIn session: {e}")
        return True


def get_linkedin_password() -> tuple[str, str] | None:
    """Get LinkedIn credentials from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "LinkedIn m13v", "-g"],
            capture_output=True,
            text=True,
        )
        full = result.stdout + result.stderr
        acct_match = re.search(r'"acct"<blob>="([^"]+)"', full)
        pwd_match = re.search(r'password: "([^"]+)"', full)
        if acct_match and pwd_match:
            return acct_match.group(1), pwd_match.group(1)
    except Exception as e:
        log(f"Keychain lookup failed: {e}")
    return None


def find_linkedin_cdp_port() -> int | None:
    """Find the CDP port of a running LinkedIn agent browser."""
    try:
        ps_out = subprocess.check_output(
            ["ps", "aux"], text=True, stderr=subprocess.DEVNULL
        )
        ports = set()
        for line in ps_out.splitlines():
            if "chrome" not in line.lower() and "chromium" not in line.lower():
                continue
            m = re.search(r"remote-debugging-port=(\d+)", line)
            if m:
                ports.add(int(m.group(1)))

        for port in sorted(ports):
            try:
                resp = urllib.request.urlopen(
                    f"http://localhost:{port}/json", timeout=2
                )
                pages = json.loads(resp.read())
                linkedin_urls = [
                    p.get("url", "")
                    for p in pages
                    if "linkedin.com" in p.get("url", "")
                ]
                if linkedin_urls:
                    return port
            except Exception:
                continue
    except Exception:
        pass
    return None


def re_authenticate() -> bool:
    """Log in to LinkedIn using Playwright persistent profile.

    Tries to connect to the existing LinkedIn agent browser via CDP first.
    Falls back to launching a headless persistent profile.
    Cookies auto-persist in the Chrome profile, no manual export needed.
    """
    creds = get_linkedin_password()
    if not creds:
        log("Could not retrieve LinkedIn credentials from keychain")
        return False

    email, password = creds
    log(f"Re-authenticating as {email}...")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return False

    with sync_playwright() as p:
        context = None
        page = None
        is_cdp = False

        # Try CDP connection to existing LinkedIn agent browser
        cdp_port = find_linkedin_cdp_port()
        if cdp_port:
            try:
                browser = p.chromium.connect_over_cdp(
                    f"http://localhost:{cdp_port}"
                )
                contexts = browser.contexts
                if contexts:
                    ctx = contexts[0]
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    is_cdp = True
                    log(f"Connected to LinkedIn agent browser on port {cdp_port}")
            except Exception as e:
                log(f"CDP connection failed: {e}")

        # Fallback: launch headless persistent profile
        if not page:
            log("Launching headless persistent profile for auth")
            try:
                context = p.chromium.launch_persistent_context(
                    PROFILE_DIR,
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                    viewport=VIEWPORT,
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
            except Exception as e:
                if "Browser is already in use" in str(e):
                    log("Profile locked by running browser. Cannot re-auth without CDP.")
                    return False
                raise

        try:
            page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_timeout(3000)
            current_url = page.url

            # Already logged in?
            if "login" not in current_url and "uas/" not in current_url and "checkpoint" not in current_url:
                log("Already logged in after navigation")
                return True

            # On login page, enter credentials
            log("On login page, entering credentials...")
            email_field = page.query_selector('input[name="session_key"]')
            if email_field:
                email_val = email_field.input_value()
                if not email_val or email_val.strip() == "":
                    email_field.fill(email)

            password_field = page.query_selector('input[name="session_password"]')
            if password_field:
                password_field.fill(password)
            else:
                pwd_box = page.query_selector('input[type="password"]')
                if pwd_box:
                    pwd_box.fill(password)
                else:
                    log("Could not find password field on login page")
                    return False

            sign_in = page.query_selector('button[type="submit"]')
            if not sign_in:
                sign_in = page.query_selector('button:has-text("Sign in")')
            if sign_in:
                sign_in.click()
            else:
                log("Could not find Sign In button")
                return False

            page.wait_for_timeout(5000)
            current_url = page.url

            if "checkpoint" in current_url or "challenge" in current_url:
                log(f"LinkedIn requires verification at: {current_url}")
                # Write 6-hour cooldown so cron runs stop hammering login
                try:
                    from linkedin_cooldown import set_cooldown
                    from datetime import datetime, timedelta, timezone
                    set_cooldown(
                        "checkpoint/verification challenge detected",
                        datetime.now(timezone.utc) + timedelta(hours=6),
                    )
                    log("Cooldown set for 6 hours to prevent repeated login attempts")
                except Exception:
                    pass
                return False

            if "login" in current_url or "uas/" in current_url:
                log(f"Login failed, still on login page: {current_url}")
                return False

            log(f"Login successful, now at: {current_url}")
            # Cookies auto-persist in the persistent profile
            return True

        except Exception as e:
            log(f"Re-authentication failed: {e}")
            return False
        finally:
            if context and not is_cdp:
                context.close()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="LinkedIn auth health check and self-healing")
    parser.add_argument("--check", action="store_true", help="Check only, don't heal")
    parser.add_argument("--force", action="store_true", help="Force re-auth even if valid")
    args = parser.parse_args()

    if args.force:
        log("Forced re-authentication requested")
        if re_authenticate():
            log("Re-authentication successful")
            return 2
        else:
            log("Re-authentication failed")
            return 1

    is_valid = check_session_valid()

    if is_valid == "rate_limited":
        log("Rate limited (429). Cooldown written, skipping run.")
        return 3

    if is_valid:
        return 0

    if args.check:
        log("Session invalid (check-only mode, not healing)")
        return 1

    # Check cooldown before attempting re-auth (avoid hammering after checkpoint)
    try:
        from linkedin_cooldown import read_cooldown
        cd = read_cooldown()
        if cd:
            log(f"Skipping re-auth: in cooldown ({cd['reason']}, until {cd['resume_after']})")
            return 1
    except Exception:
        pass

    log("Session invalid, attempting self-healing...")
    if re_authenticate():
        log("Self-healing successful")
        return 2
    else:
        log("Self-healing failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
