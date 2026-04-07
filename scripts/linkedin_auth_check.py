#!/usr/bin/env python3
"""LinkedIn auth health check and self-healing.

Checks if the LinkedIn session in browser-sessions.json is still valid.
If invalid, re-authenticates using keychain credentials and saves fresh cookies.

Usage:
    python3 scripts/linkedin_auth_check.py          # check + heal if needed
    python3 scripts/linkedin_auth_check.py --check   # check only, exit 1 if invalid
    python3 scripts/linkedin_auth_check.py --force   # force re-auth even if valid

Exit codes:
    0 = session is valid (or was healed)
    1 = session is invalid and could not be healed
    2 = session was invalid but successfully healed
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

STORAGE_STATE = os.path.expanduser("~/.claude/browser-sessions.json")
LINKEDIN_AGENT_CONFIG = os.path.expanduser(
    "~/.claude/browser-agent-configs/linkedin-agent.json"
)
VIEWPORT = {"width": 911, "height": 1016}


def log(msg: str) -> None:
    print(f"[linkedin-auth] {msg}", file=sys.stderr)


def get_li_at_cookie() -> dict | None:
    """Read li_at cookie from browser-sessions.json."""
    if not os.path.exists(STORAGE_STATE):
        return None
    try:
        with open(STORAGE_STATE) as f:
            data = json.load(f)
        for cookie in data.get("cookies", []):
            if cookie["name"] == "li_at":
                return cookie
    except Exception:
        pass
    return None


def check_cookie_valid() -> bool:
    """Check if the li_at cookie is accepted by LinkedIn's servers.

    Makes a lightweight HEAD request to LinkedIn with the cookie.
    If LinkedIn redirects to /login or /uas/login, the session is dead.
    """
    cookie = get_li_at_cookie()
    if not cookie:
        log("No li_at cookie found in browser-sessions.json")
        return False

    # Check expiry
    expires = cookie.get("expires", -1)
    if expires > 0 and expires < time.time():
        log(f"li_at cookie expired at {time.ctime(expires)}")
        return False

    li_at_value = cookie["value"]

    # Also grab JSESSIONID for csrf
    jsession = None
    try:
        with open(STORAGE_STATE) as f:
            data = json.load(f)
        for c in data.get("cookies", []):
            if c["name"] == "JSESSIONID":
                jsession = c["value"]
                break
    except Exception:
        pass

    cookie_header = f'li_at={li_at_value}'
    if jsession:
        cookie_header += f'; JSESSIONID={jsession}'

    try:
        req = urllib.request.Request(
            "https://www.linkedin.com/feed/",
            method="GET",
            headers={
                "Cookie": cookie_header,
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        )
        # Follow redirects manually to detect login redirect
        opener = urllib.request.build_opener(
            urllib.request.HTTPRedirectHandler()
        )
        resp = opener.open(req, timeout=15)
        final_url = resp.geturl()

        if "login" in final_url or "uas/" in final_url or "checkpoint" in final_url:
            log(f"LinkedIn redirected to login: {final_url}")
            return False

        # Check response code
        if resp.status == 200:
            log("LinkedIn session is valid")
            return True

        log(f"LinkedIn returned status {resp.status}")
        return False

    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            log(f"LinkedIn returned {e.code}, session invalid")
            return False
        # 429, 5xx could be temporary
        if e.code >= 500 or e.code == 429:
            log(f"LinkedIn returned {e.code}, assuming session is OK (server issue)")
            return True
        log(f"LinkedIn HTTP error: {e.code}")
        return False
    except Exception as e:
        log(f"Error checking LinkedIn session: {e}")
        # Network error, don't assume session is bad
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


def export_cookies_cdp(port: int) -> bool:
    """Export fresh cookies from the running browser via CDP."""
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    export_script = os.path.join(repo_dir, "scripts", "export_cdp_storage_state.py")

    # Find a LinkedIn page URL prefix to use
    try:
        resp = urllib.request.urlopen(
            f"http://localhost:{port}/json", timeout=2
        )
        pages = json.loads(resp.read())
        linkedin_page = None
        for p in pages:
            url = p.get("url", "")
            if "linkedin.com" in url and "login" not in url and "uas/" not in url:
                linkedin_page = url
                break
        if not linkedin_page:
            # Use any LinkedIn page
            for p in pages:
                if "linkedin.com" in p.get("url", ""):
                    linkedin_page = p["url"]
                    break
    except Exception:
        linkedin_page = None

    if not linkedin_page:
        log("No LinkedIn page found on CDP port for cookie export")
        return False

    # Build a prefix that matches the page URL
    # e.g. "https://www.linkedin.com/feed/" or "https://www.linkedin.com/search/"
    prefix = linkedin_page.split("?")[0]
    if not prefix.endswith("/"):
        prefix = prefix.rsplit("/", 1)[0] + "/"

    try:
        result = subprocess.run(
            [
                sys.executable,
                export_script,
                "--port", str(port),
                "--page-url-prefix", prefix,
                "--cookie-url", "https://www.linkedin.com/feed/",
                "--cookie-url", "https://www.linkedin.com/",
                "--domain-filter", "linkedin.com",
                "--merge",
                "--backup",
                "--require-cookie", "li_at",
                "--out", STORAGE_STATE,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            log("Fresh cookies exported successfully")
            return True
        else:
            log(f"Cookie export failed: {result.stderr.strip()}")
            return False
    except Exception as e:
        log(f"Cookie export error: {e}")
        return False


def re_authenticate() -> bool:
    """Log in to LinkedIn using Playwright and save fresh cookies.

    Tries to connect to the existing LinkedIn agent browser via CDP first.
    Falls back to launching a headless browser.
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
        browser = None
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
                    # Use existing page or create new one
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    is_cdp = True
                    log(f"Connected to LinkedIn agent browser on port {cdp_port}")
            except Exception as e:
                log(f"CDP connection failed: {e}")
                browser = None

        # Fallback: launch headless browser with existing storage state
        if not browser:
            log("Launching headless browser for auth")
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                storage_state=STORAGE_STATE if os.path.exists(STORAGE_STATE) else None,
                viewport=VIEWPORT,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()

        try:
            # Navigate to LinkedIn
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
                if is_cdp:
                    return export_cookies_cdp(cdp_port)
                return save_context_cookies(page.context)

            # We're on the login page
            log("On login page, entering credentials...")

            # Check if email is pre-filled or needs to be entered
            email_field = page.query_selector('input[name="session_key"]')
            if email_field:
                email_val = email_field.input_value()
                if not email_val or email_val.strip() == "":
                    email_field.fill(email)

            # Fill password
            password_field = page.query_selector('input[name="session_password"]')
            if password_field:
                password_field.fill(password)
            else:
                # May be the "Welcome back" page with just password
                pwd_box = page.query_selector('input[type="password"]')
                if pwd_box:
                    pwd_box.fill(password)
                else:
                    log("Could not find password field on login page")
                    return False

            # Click sign in
            sign_in = page.query_selector('button[type="submit"]')
            if not sign_in:
                sign_in = page.query_selector('button:has-text("Sign in")')
            if sign_in:
                sign_in.click()
            else:
                log("Could not find Sign In button")
                return False

            # Wait for navigation
            page.wait_for_timeout(5000)
            current_url = page.url

            # Check for verification/challenge
            if "checkpoint" in current_url or "challenge" in current_url:
                log(f"LinkedIn requires verification at: {current_url}")
                log("Manual intervention needed. Please complete verification in the browser.")
                return False

            if "login" in current_url or "uas/" in current_url:
                log(f"Login failed, still on login page: {current_url}")
                return False

            log(f"Login successful, now at: {current_url}")

            # Save cookies
            if is_cdp:
                # Wait a moment for cookies to settle
                page.wait_for_timeout(2000)
                return export_cookies_cdp(cdp_port)
            else:
                return save_context_cookies(page.context)

        except Exception as e:
            log(f"Re-authentication failed: {e}")
            return False
        finally:
            if not is_cdp and browser:
                browser.close()


def save_context_cookies(context) -> bool:
    """Save cookies from a Playwright context into browser-sessions.json (merge mode)."""
    try:
        state = context.storage_state()
        new_cookies = state.get("cookies", [])
        li_cookies = [c for c in new_cookies if "linkedin.com" in c.get("domain", "")]

        if not any(c["name"] == "li_at" for c in li_cookies):
            log("No li_at cookie in new session, login may have failed")
            return False

        # Merge into existing file
        if os.path.exists(STORAGE_STATE):
            # Backup
            backup = STORAGE_STATE + ".bak"
            shutil.copy2(STORAGE_STATE, backup)

            with open(STORAGE_STATE) as f:
                existing = json.load(f)
            # Remove old LinkedIn cookies
            existing_cookies = [
                c for c in existing.get("cookies", [])
                if "linkedin.com" not in c.get("domain", "")
            ]
            merged = {
                "cookies": existing_cookies + li_cookies,
                "origins": existing.get("origins", []),
            }
        else:
            merged = {"cookies": li_cookies, "origins": []}

        with open(STORAGE_STATE, "w") as f:
            json.dump(merged, f, indent=2)

        log(f"Saved {len(li_cookies)} LinkedIn cookies to {STORAGE_STATE}")
        return True

    except Exception as e:
        log(f"Failed to save cookies: {e}")
        return False


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

    is_valid = check_cookie_valid()

    if is_valid:
        return 0

    if args.check:
        log("Session invalid (check-only mode, not healing)")
        return 1

    log("Session invalid, attempting self-healing...")
    if re_authenticate():
        # Verify the new session works
        if check_cookie_valid():
            log("Self-healing successful, new session verified")
            return 2
        else:
            log("Self-healing completed but new session still invalid")
            return 1
    else:
        log("Self-healing failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
