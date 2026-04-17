#!/usr/bin/env python3
"""Twitter/X browser automation functions for Social Autoposter.

Replaces multi-step Claude browser MCP calls with single Python function calls.
Each function does all browser work internally and returns structured JSON.

Usage:
    # Reply to a tweet
    python3 twitter_browser.py reply "https://x.com/user/status/123" "reply text"

    # Scan DM inbox for unread conversations
    python3 twitter_browser.py unread-dms

    # Read messages from a DM conversation
    python3 twitter_browser.py read-conversation "https://x.com/i/chat/123-456"

    # Send a DM message
    python3 twitter_browser.py send-dm "https://x.com/i/chat/123-456" "message text"

Requires: pip install playwright && playwright install chromium

Connects to the running twitter-agent MCP browser via CDP (Chrome DevTools Protocol)
to reuse the existing logged-in session.
"""

import atexit
import json
import os
import re
import subprocess
import sys
import time


PROFILE_DIR = os.path.expanduser("~/.claude/browser-profiles/twitter")
LOCK_FILE = os.path.expanduser("~/.claude/twitter-agent-lock.json")
LOCK_EXPIRY = 300  # Must match twitter-agent-lock.sh
VIEWPORT = {"width": 911, "height": 1016}
OUR_HANDLE = "m13v_"

# DM encryption passcode from .env
DM_PASSCODE = os.environ.get("TWITTER_DM_PASSCODE", "")
if not DM_PASSCODE:
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("TWITTER_DM_PASSCODE="):
                    DM_PASSCODE = line.strip().split("=", 1)[1]
                    break


def find_twitter_cdp_port():
    """Find the CDP port of the running twitter-agent MCP browser."""
    try:
        ps_out = subprocess.check_output(
            ["ps", "aux"], text=True, stderr=subprocess.DEVNULL
        )
        ports = set()
        for line in ps_out.splitlines():
            if "chromium" not in line.lower() and "chrome" not in line.lower():
                continue
            m = re.search(r"remote-debugging-port=(\d+)", line)
            if m:
                ports.add(int(m.group(1)))

        import urllib.request

        best_port = None
        for port in sorted(ports):
            try:
                resp = urllib.request.urlopen(
                    f"http://localhost:{port}/json", timeout=2
                )
                pages = json.loads(resp.read())
                twitter_urls = [
                    p.get("url", "")
                    for p in pages
                    if "x.com" in p.get("url", "") or "twitter.com" in p.get("url", "")
                ]
                if not twitter_urls:
                    continue
                # Prefer ports with logged-in pages (home, chat, notifications)
                logged_in = any(
                    ("home" in u or "chat" in u or "notifications" in u or "status" in u)
                    and "login" not in u
                    for u in twitter_urls
                )
                if logged_in:
                    return port
                if best_port is None:
                    best_port = port
            except Exception:
                continue
        return best_port
    except Exception:
        pass
    return None


_LOCK_SESSION_ID = f"python:{os.getpid()}"
_LOCK_INHERITED = False
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _release_browser_lock():
    """Release the lock if we hold it.

    If we inherited the lock from a Claude session (UUID holder), leave it for
    the hook/session-end handler to release — don't clobber the parent's lock.
    """
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
    """Check if another session holds the Twitter browser lock, then acquire it.

    Claude Code does not export session_id to subprocesses, so we can't directly
    match our parent session. But the PreToolUse hook (twitter-agent-lock.sh)
    enforces one Claude UUID owner at a time: any other Claude session would
    have been blocked before it could reach this script via Bash. So if the
    current lock is held by a UUID-style id, it must be our parent — inherit
    it instead of failing. Only python:PID holders represent a real conflict.
    """
    global _LOCK_SESSION_ID, _LOCK_INHERITED
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                lock = json.load(f)
            age = time.time() - lock.get("timestamp", 0)
            holder = lock.get("session_id", "")
            if age < LOCK_EXPIRY:
                if _UUID_RE.match(holder or ""):
                    _LOCK_SESSION_ID = holder
                    _LOCK_INHERITED = True
                else:
                    print(json.dumps({
                        "success": False,
                        "error": f"Twitter browser is locked by session {holder} ({int(age)}s ago). Skipping."
                    }))
                    sys.exit(1)
        except (json.JSONDecodeError, OSError):
            pass
    with open(LOCK_FILE, "w") as f:
        json.dump({"session_id": _LOCK_SESSION_ID, "timestamp": int(time.time())}, f)


def _refresh_browser_lock():
    """Refresh the lock timestamp to prevent expiry during long operations."""
    try:
        with open(LOCK_FILE, "w") as f:
            json.dump({"session_id": _LOCK_SESSION_ID, "timestamp": int(time.time())}, f)
    except OSError:
        pass


def get_browser_and_page(playwright):
    """Connect to the twitter-agent MCP browser via CDP, or launch a new one.

    Returns (browser, page, is_cdp). When is_cdp=True, `page` is a reused
    existing Twitter tab (navigate it, don't close it). When is_cdp=False,
    it's a new headless page.
    """
    _acquire_browser_lock()
    cdp_port = find_twitter_cdp_port()

    if cdp_port:
        try:
            browser = playwright.chromium.connect_over_cdp(
                f"http://localhost:{cdp_port}"
            )
            contexts = browser.contexts
            if contexts:
                context = contexts[0]
                for pg in context.pages:
                    if ("x.com" in pg.url or "twitter.com" in pg.url) and "login" not in pg.url:
                        return browser, pg, True
                if context.pages:
                    return browser, context.pages[0], True
        except Exception:
            pass

    # Fallback: launch persistent browser with saved profile
    context = playwright.chromium.launch_persistent_context(
        PROFILE_DIR,
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
        viewport=VIEWPORT,
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    page = context.new_page()
    return context, page, False


def _handle_dm_passcode(page):
    """Handle the DM encryption passcode dialog if it appears.

    Twitter/X requires a 4-digit passcode to decrypt DMs.
    Returns True if passcode was entered, False if not needed.
    """
    if "pin/recovery" not in page.url:
        return False

    if not DM_PASSCODE:
        print("Warning: DM passcode required but TWITTER_DM_PASSCODE not set", file=sys.stderr)
        return False

    try:
        digits = list(DM_PASSCODE)
        # Find the 4 passcode input boxes
        inputs = page.locator('input')
        count = inputs.count()
        for i in range(min(len(digits), count)):
            inp = inputs.nth(i)
            inp.click()
            page.keyboard.type(digits[i])
            page.wait_for_timeout(300)

        page.wait_for_timeout(3000)
        return "pin/recovery" not in page.url
    except Exception as e:
        print(f"Warning: Failed to enter DM passcode: {e}", file=sys.stderr)
        return False



def _collect_our_reply_links(page):
    """Collect all /OUR_HANDLE/status/ links currently in the DOM."""
    return set(page.evaluate(f"""() => {{
        const links = new Set();
        document.querySelectorAll('a[href*="/{OUR_HANDLE}/status/"]').forEach(a => {{
            const href = a.getAttribute('href');
            if (href && /\\/{OUR_HANDLE}\\/status\\/\\d+$/.test(href))
                links.add(href);
        }});
        return [...links];
    }}"""))


def reply_to_tweet(tweet_url, text):
    """Reply to a tweet.

    Navigates to the tweet, clicks the reply box, types the reply, and submits.

    Returns: {"ok": true, "tweet_url": "...", "reply_url": "..."} or {"ok": false, "error": "..."}
    """
    print(f"[twitter_browser] reply_to_tweet called: {tweet_url}", file=sys.stderr)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            # Set up CDP Network interception to capture CreateTweet response
            _cdp_session = None
            _created_tweet_ids = []
            try:
                _cdp_session = page.context.new_cdp_session(page)
                _cdp_session.send("Network.enable")

                def _on_cdp_response(params):
                    try:
                        url = params.get("response", {}).get("url", "")
                        if "CreateTweet" in url:
                            body_resp = _cdp_session.send(
                                "Network.getResponseBody",
                                {"requestId": params["requestId"]},
                            )
                            data = json.loads(body_resp.get("body", "{}"))
                            rest_id = (
                                data.get("data", {})
                                .get("create_tweet", {})
                                .get("tweet_results", {})
                                .get("result", {})
                                .get("rest_id")
                            )
                            if rest_id:
                                _created_tweet_ids.append(rest_id)
                    except Exception:
                        pass

                _cdp_session.on("Network.responseReceived", _on_cdp_response)
            except Exception:
                pass

            page.goto(tweet_url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            # Check if page exists
            page_text = page.text_content("main") or ""
            if "this page doesn't exist" in page_text.lower():
                return {"ok": False, "error": "tweet_not_found"}

            # Snapshot our reply links before posting (to detect the new one)
            links_before = _collect_our_reply_links(page)

            # Find the reply textbox
            reply_box = None
            try:
                reply_box = page.get_by_role("textbox", name="Post text")
                reply_box.wait_for(timeout=10000)
            except Exception:
                # Scroll down to find the reply box
                page.evaluate("window.scrollBy(0, 500)")
                page.wait_for_timeout(2000)
                try:
                    reply_box = page.get_by_role("textbox", name="Post text")
                    reply_box.wait_for(timeout=5000)
                except Exception:
                    return {"ok": False, "error": "reply_box_not_found"}

            # Click and type the reply
            reply_box.click()
            page.wait_for_timeout(500)
            page.keyboard.type(text, delay=10)
            page.wait_for_timeout(1000)

            # Click the Reply submit button. MUST target tweetButtonInline by
            # testid; substring-matching "Reply" by accessible name matches
            # every reply-icon on the page and picks the wrong one.
            try:
                reply_btn = page.locator('[data-testid="tweetButtonInline"]').first
                reply_btn.wait_for(state="visible", timeout=5000)
                for _ in range(20):
                    if reply_btn.get_attribute("aria-disabled") != "true":
                        break
                    page.wait_for_timeout(100)
                reply_btn.click()
            except Exception:
                page.keyboard.press("Meta+Enter")

            page.wait_for_timeout(4000)

            # Verify: check if the reply box is empty (cleared after posting)
            try:
                box_text = reply_box.text_content() or ""
                verified = len(box_text.strip()) == 0 or text not in box_text
            except Exception:
                verified = True

            # Clean up CDP session
            if _cdp_session:
                try:
                    _cdp_session.detach()
                except Exception:
                    pass

            # Capture reply URL
            reply_url = None

            # Method 1: CDP network interception (most reliable)
            if _created_tweet_ids:
                reply_url = f"https://x.com/{OUR_HANDLE}/status/{_created_tweet_ids[-1]}"
                print(f"[reply_url] captured via CDP: {reply_url}", file=sys.stderr)

            # Method 2: DOM diff (check if new reply links appeared)
            if not reply_url:
                for attempt in range(3):
                    links_after = _collect_our_reply_links(page)
                    new_links = links_after - links_before
                    if new_links:
                        reply_path = max(new_links, key=lambda x: int(re.search(r'/status/(\d+)', x).group(1)))
                        reply_url = f"https://x.com{reply_path}" if not reply_path.startswith("http") else reply_path
                        break
                    page.wait_for_timeout(2000)

            # Method 3: Check our profile for the latest reply
            if not reply_url:
                try:
                    page.goto(f"https://x.com/{OUR_HANDLE}/with_replies", wait_until="domcontentloaded")
                    page.wait_for_timeout(4000)
                    profile_links = _collect_our_reply_links(page)
                    if profile_links:
                        latest_path = max(profile_links, key=lambda x: int(re.search(r'/status/(\d+)', x).group(1)))
                        latest_id = re.search(r'/status/(\d+)', latest_path).group(1)
                        tracker_file = "/tmp/social-autoposter-last-reply-id.txt"
                        last_known = ""
                        try:
                            with open(tracker_file) as f:
                                last_known = f.read().strip()
                        except FileNotFoundError:
                            pass
                        if latest_id != last_known:
                            reply_url = f"https://x.com{latest_path}" if not latest_path.startswith("http") else latest_path
                            with open(tracker_file, "w") as f:
                                f.write(latest_id)
                except Exception:
                    pass

            if reply_url:
                print(f"[reply_url] found: {reply_url}", file=sys.stderr)

            return {
                "ok": True,
                "tweet_url": tweet_url,
                "reply_url": reply_url,
                "verified": verified,
            }

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def unread_dms():
    """Scan Twitter/X DM inbox for conversations.

    Navigates to /i/chat, handles the encryption passcode if needed,
    and extracts all visible conversations with their author, preview text,
    timestamp, and conversation URL.

    Returns: [{"author": "...", "handle": "...", "preview": "...", "time": "...",
               "thread_url": "...", "is_from_us": bool}, ...]
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            page.goto("https://x.com/i/chat", wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            # Handle DM passcode if needed
            _handle_dm_passcode(page)
            page.wait_for_timeout(2000)

            # Verify we're on the DM inbox
            if "chat" not in page.url:
                return {"ok": False, "error": "not_on_dm_page", "url": page.url}

            # Extract conversation list from the accessible link names
            # Each conversation is a listitem with a link whose accessible
            # name contains: "user avatar NAME TIME PREVIEW"
            conversations = page.evaluate("""() => {
                const results = [];
                const items = document.querySelectorAll('main li, main [role="listitem"]');

                for (const item of items) {
                    // Find the conversation link
                    const link = item.querySelector('a[href*="/i/chat/"]');
                    if (!link) continue;

                    const threadUrl = link.href;
                    if (!threadUrl.match(/\\/i\\/chat\\/[\\d-g]/)) continue;

                    // Get the handle from the avatar link
                    let handle = '';
                    const avatarLink = item.querySelector('a[href^="https://x.com/"]');
                    if (avatarLink) {
                        const href = avatarLink.getAttribute('href') || '';
                        const m = href.match(/x\\.com\\/([^/]+)/);
                        if (m) handle = m[1];
                    }

                    // Extract structured data from the DOM
                    // The link contains nested divs with: [avatar] [name+time div] [preview div]
                    // Walk the DOM tree to find text nodes
                    let author = '';
                    let time = '';
                    let preview = '';

                    // The link's accessible name has everything
                    const linkText = (link.getAttribute('aria-label') || '').trim();
                    if (linkText) {
                        // Format: "user avatar NAME TIME PREVIEW"
                        let text = linkText.replace(/^user avatar\\s*/, '');
                        // Try to parse out components
                        const timeMatch = text.match(/\\s+(\\d+[hmd]|\\d+w|Just now)\\s+/);
                        if (timeMatch) {
                            const idx = text.indexOf(timeMatch[0]);
                            author = text.substring(0, idx).trim();
                            time = timeMatch[1];
                            preview = text.substring(idx + timeMatch[0].length).trim();
                        }
                    }

                    // Fallback: parse from child elements directly
                    if (!author) {
                        const divs = link.querySelectorAll('div');
                        const texts = [];
                        for (const d of divs) {
                            // Only get leaf-ish nodes
                            if (d.children.length <= 1) {
                                const t = d.textContent.trim();
                                if (t && t.length > 0) texts.push(t);
                            }
                        }
                        // Typically: [name, verified_badge?, time, preview]
                        for (const t of texts) {
                            if (t.match(/^\\d+[hmd]$/) || t.match(/^\\d+w$/)) {
                                time = t;
                            } else if (!author && t.length > 0 && t.length < 50 &&
                                       t !== 'user avatar') {
                                author = t;
                            } else if (author && !preview && t.length > 5) {
                                preview = t;
                            }
                        }
                    }

                    const isFromUs = preview.startsWith('You:');

                    if (author || handle) {
                        results.push({
                            author: author,
                            handle: handle,
                            preview: preview.substring(0, 300),
                            time: time,
                            thread_url: threadUrl,
                            is_from_us: isFromUs,
                        });
                    }
                }

                return results;
            }""")

            # Deduplicate by thread_url
            seen = set()
            unique = []
            for c in conversations:
                if c["thread_url"] not in seen:
                    seen.add(c["thread_url"])
                    unique.append(c)

            return unique

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def read_conversation(thread_url, max_messages=20):
    """Read messages from a specific Twitter/X DM conversation.

    Navigates to the thread URL and extracts the most recent messages
    with their sender, content, and timestamp.

    Returns: {"partner_name": "...", "partner_handle": "...",
              "messages": [{"sender": "...", "content": "...", "time": "...",
                            "is_from_us": bool}, ...], "total_found": N}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            # Navigate using JS to avoid SPA navigation timeouts
            page.evaluate(f"window.location.href = '{thread_url}'")
            page.wait_for_timeout(6000)

            # Handle DM passcode if needed
            _handle_dm_passcode(page)
            page.wait_for_timeout(2000)

            result = page.evaluate("""(params) => {
                const maxMessages = params.maxMessages;
                const ourHandle = params.ourHandle;

                let partnerName = '';
                let partnerHandle = '';
                const main = document.querySelector('main');
                if (!main) return {partner_name: '', partner_handle: '', messages: [], total_found: 0};

                // Find the conversation panel (the section containing the
                // message textbox), NOT the sidebar conversation list.
                // The textbox has aria-label like "Unencrypted message".
                const textbox = main.querySelector('[role="textbox"]');
                // Walk up from textbox to find the conversation container
                // that holds the message list items.
                let convPanel = null;
                if (textbox) {
                    // The conversation panel is typically a sibling of or
                    // ancestor of the textbox container. Walk up to find
                    // the div that contains BOTH the message list and textbox.
                    let el = textbox;
                    for (let i = 0; i < 10; i++) {
                        el = el.parentElement;
                        if (!el) break;
                        const lis = el.querySelectorAll('li, [role="listitem"]');
                        if (lis.length >= 2) {
                            convPanel = el;
                            break;
                        }
                    }
                }

                // Fallback: if no textbox found, try to find the panel
                // that has "View Profile" text (the conversation header)
                if (!convPanel) {
                    const allDivs = main.querySelectorAll('div');
                    for (const d of allDivs) {
                        if (d.textContent.includes('View Profile') &&
                            d.textContent.includes('Joined ') &&
                            d.querySelectorAll('li').length >= 2) {
                            convPanel = d;
                            break;
                        }
                    }
                }

                // Last fallback: use main but filter out sidebar items
                if (!convPanel) convPanel = main;

                // Extract partner info from profile card in the conversation
                const profileLink = convPanel.querySelector('a[href*="x.com/"]');
                if (profileLink) {
                    const href = profileLink.getAttribute('href') || '';
                    const m = href.match(/x\\.com\\/([^/]+)/);
                    if (m && m[1] !== ourHandle) partnerHandle = m[1];
                }

                // Look for @handle text
                const handleEls = convPanel.querySelectorAll('div, span');
                for (const el of handleEls) {
                    const t = el.textContent.trim();
                    if (t.startsWith('@') && t.length > 2 && t.length < 50 &&
                        !t.includes(' ') && t.substring(1) !== ourHandle) {
                        partnerHandle = t.substring(1);
                        break;
                    }
                }

                // Find messages — only from the conversation panel
                const items = convPanel.querySelectorAll('li, [role="listitem"]');
                const messages = [];
                let currentDate = '';

                for (const item of items) {
                    const text = item.textContent || '';

                    // Skip sidebar conversation items (they contain
                    // avatar links to x.com/username profiles)
                    const sidebarLink = item.querySelector('a[href*="/i/chat/"]');
                    if (sidebarLink) continue;

                    // Date separator
                    if (text.match(/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Today|Yesterday)/) &&
                        text.length < 30) {
                        currentDate = text.trim();
                        continue;
                    }

                    // Profile card
                    if (text.includes('View Profile') || text.includes('Joined ')) {
                        const nameEl = item.querySelector('div[dir="ltr"], span');
                        if (nameEl && !partnerName) {
                            const n = nameEl.textContent.trim();
                            if (n && n.length > 1 && n.length < 50 &&
                                !n.startsWith('@') && !n.includes('View') &&
                                !n.includes('Joined')) {
                                partnerName = n;
                            }
                        }
                        continue;
                    }

                    if (text.trim().length < 2) continue;

                    // Extract message content and time
                    let content = '';
                    let time = '';
                    let isFromUs = false;

                    const timeMatch = text.match(/(\\d{1,2}:\\d{2}\\s*[AP]M)/);
                    if (timeMatch) {
                        time = timeMatch[1];
                    }

                    // Content: find the deepest div with message text
                    const contentDivs = item.querySelectorAll('div');
                    for (const cd of contentDivs) {
                        const t = cd.textContent.trim();
                        if (t.match(/^\\d{1,2}:\\d{2}\\s*[AP]M$/)) continue;
                        if (t === time) continue;
                        if (t.length > 2 && t.length < 5000 &&
                            !t.includes('View Profile') && !t.includes('Joined ')) {
                            const childDivs = cd.querySelectorAll('div');
                            if (childDivs.length <= 2) {
                                content = t.replace(/(\\d{1,2}:\\d{2}\\s*[AP]M)/g, '').trim();
                                if (content.length > 0) break;
                            }
                        }
                    }

                    if (!content || content.length < 1) continue;

                    // Our sent messages have SVG checkmarks (delivery status)
                    const svgCount = item.querySelectorAll('svg').length;
                    isFromUs = svgCount > 0;

                    messages.push({
                        sender: isFromUs ? 'us' : partnerName || partnerHandle || 'them',
                        content: content,
                        time: currentDate ? currentDate + ' ' + time : time,
                        is_from_us: isFromUs,
                    });
                }

                const recent = messages.slice(-maxMessages);

                return {
                    partner_name: partnerName,
                    partner_handle: partnerHandle,
                    messages: recent,
                    total_found: messages.length,
                };
            }""", {"maxMessages": max_messages, "ourHandle": OUR_HANDLE})

            return result

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def send_dm(thread_url, message):
    """Send a message in a Twitter/X DM conversation.

    Navigates to the thread URL, types the message in the compose box,
    and sends it.

    Returns: {"ok": true, "thread_url": "..."} or {"ok": false, "error": "..."}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            # Navigate to DM inbox first, then click into conversation.
            # Direct URL navigation to DM conversations often hangs
            # because X's SPA doesn't fire domcontentloaded for DM routes.
            page.evaluate("window.location.href = 'https://x.com/i/chat/'")
            page.wait_for_timeout(5000)

            # Handle DM passcode if needed
            _handle_dm_passcode(page)
            page.wait_for_timeout(2000)

            # Extract conversation ID from URL and click into it
            conv_id = thread_url.rstrip("/").split("/")[-1]
            try:
                conv_link = page.locator(f'a[href*="{conv_id}"]').first
                conv_link.click()
                page.wait_for_timeout(3000)
            except Exception:
                return {"ok": False, "error": "conversation_not_found_in_sidebar"}

            # Find the message input box
            msg_box = None
            for label in ["Unencrypted message", "Start a new message"]:
                try:
                    msg_box = page.get_by_role("textbox", name=label)
                    msg_box.wait_for(state="visible", timeout=5000)
                    break
                except Exception:
                    msg_box = None

            if not msg_box:
                try:
                    msg_box = page.locator(
                        'div[role="textbox"][contenteditable="true"]'
                    ).last
                    msg_box.wait_for(state="visible", timeout=3000)
                except Exception:
                    return {"ok": False, "error": "message_box_not_found"}

            # Click and type
            msg_box.click()
            page.wait_for_timeout(500)
            page.keyboard.type(message, delay=10)
            page.wait_for_timeout(1000)

            # Send: press Enter (Twitter DMs send on Enter)
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)

            # Verify: check if the message appears in the conversation
            msg_start = message[:50]
            verified = page.evaluate("""(msgStart) => {
                const main = document.querySelector('main');
                if (!main) return false;
                const text = main.textContent || '';
                return text.includes(msgStart);
            }""", msg_start)

            return {
                "ok": True,
                "thread_url": page.url,
                "verified": verified,
            }

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def discover_notifications(scroll_count=8):
    """Scrape mentions from https://x.com/notifications/mentions.

    Scrolls the mentions tab and extracts each tweet as a mention record.
    No API cost (uses the logged-in session via CDP).

    Returns: {"notifications": [...], "total": N} or {"error": "..."}
    """
    print(f"[twitter_browser] discover_notifications called (scroll_count={scroll_count})", file=sys.stderr)
    from playwright.sync_api import sync_playwright

    EXTRACTOR_JS = r"""() => {
      const out = [];
      for (const article of document.querySelectorAll('article[data-testid="tweet"]')) {
        try {
          let handle = '';
          let displayName = '';
          for (const link of article.querySelectorAll('a[role="link"]')) {
            const href = link.getAttribute('href');
            if (href && href.startsWith('/') && !href.includes('/status/') && !href.includes('/i/') && href.length > 1 && href.split('/').length === 2) {
              handle = href.replace('/', '');
              const nameEl = link.querySelector('span');
              if (nameEl) displayName = nameEl.textContent || '';
              break;
            }
          }
          const tweetText = article.querySelector('[data-testid="tweetText"]');
          const text = tweetText ? tweetText.textContent : '';
          const timeEl = article.querySelector('time');
          const timeParent = timeEl ? timeEl.closest('a') : null;
          const tweetHref = timeParent ? timeParent.getAttribute('href') : '';
          const tweetUrl = tweetHref ? ('https://x.com' + tweetHref) : '';
          const datetime = timeEl ? timeEl.getAttribute('datetime') : '';
          const idMatch = tweetHref ? tweetHref.match(/\/status\/(\d+)/) : null;
          const tweetId = idMatch ? idMatch[1] : '';
          let replies=0, retweets=0, likes=0, views=0, bookmarks=0;
          for (const btn of article.querySelectorAll('[role="group"] button, [role="group"] a')) {
            const al = btn.getAttribute('aria-label') || '';
            let m;
            if (m=al.match(/([\d,]+)\s*repl/i)) replies=parseInt(m[1].replace(/,/g,''));
            if (m=al.match(/([\d,]+)\s*repost/i)) retweets=parseInt(m[1].replace(/,/g,''));
            if (m=al.match(/([\d,]+)\s*like/i)) likes=parseInt(m[1].replace(/,/g,''));
            if (m=al.match(/([\d,]+)\s*view/i)) views=parseInt(m[1].replace(/,/g,''));
            if (m=al.match(/([\d,]+)\s*bookmark/i)) bookmarks=parseInt(m[1].replace(/,/g,''));
          }
          // Detect reply-to target (if tweet is a reply, there's a "Replying to" block)
          let replyingTo = '';
          const socialContext = article.querySelector('[data-testid="socialContext"]');
          const ariaLabel = article.getAttribute('aria-label') || '';
          for (const span of article.querySelectorAll('a[href^="/"]')) {
            const href = span.getAttribute('href') || '';
            if (href.includes('/status/') && span.textContent && span.textContent.trim().startsWith('@')) {
              replyingTo = span.textContent.trim().replace(/^@/, '');
              break;
            }
          }
          if (tweetId && handle) {
            out.push({
              tweet_id: tweetId,
              handle: handle,
              display_name: displayName.trim(),
              text: (text || '').substring(0, 1000),
              tweet_url: tweetUrl,
              datetime: datetime,
              replies: replies, retweets: retweets, likes: likes, views: views, bookmarks: bookmarks,
              replying_to: replyingTo
            });
          }
        } catch(e) {}
      }
      return out;
    }"""

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)
        try:
            page.goto("https://x.com/notifications/mentions", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            seen = set()
            all_tweets = []
            for i in range(scroll_count):
                try:
                    new_tweets = page.evaluate(EXTRACTOR_JS)
                except Exception as e:
                    print(f"[notifications] extractor error on scroll {i}: {e}", file=sys.stderr)
                    new_tweets = []
                added = 0
                for t in new_tweets:
                    tid = t.get('tweet_id')
                    if tid and tid not in seen:
                        seen.add(tid)
                        all_tweets.append(t)
                        added += 1
                print(f"[notifications] scroll {i+1}/{scroll_count}: +{added} new, total {len(all_tweets)}", file=sys.stderr)
                page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                page.wait_for_timeout(1500)
                _refresh_browser_lock()

            return {"notifications": all_tweets, "total": len(all_tweets)}
        finally:
            if not is_cdp:
                page.close()
                browser.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "reply":
        if len(sys.argv) < 4:
            print(
                "Usage: twitter_browser.py reply <tweet_url> <reply_text>",
                file=sys.stderr,
            )
            sys.exit(1)
        result = reply_to_tweet(sys.argv[2], sys.argv[3])
        print(json.dumps(result, indent=2))

    elif cmd == "self-reply":
        # Self-reply with guaranteed project URL. The URL is passed as a
        # separate arg and appended at the tool level so the LLM cannot
        # strip it from the text (which happened repeatedly when relying
        # on prompt instructions alone).
        if len(sys.argv) < 5:
            print(
                "Usage: twitter_browser.py self-reply <our_reply_url> <text> <project_url>",
                file=sys.stderr,
            )
            sys.exit(1)
        our_url, text, project_url = sys.argv[2], sys.argv[3], sys.argv[4]
        if not project_url.startswith("http"):
            print(
                f"self-reply: project_url must start with http(s), got: {project_url!r}",
                file=sys.stderr,
            )
            sys.exit(1)
        stripped = text.rstrip()
        if project_url in stripped:
            final = stripped
        else:
            final = f"{stripped} {project_url}"
        result = reply_to_tweet(our_url, final)
        result["final_text"] = final
        print(json.dumps(result, indent=2))

    elif cmd == "unread-dms":
        result = unread_dms()
        print(json.dumps(result, indent=2))

    elif cmd == "read-conversation":
        if len(sys.argv) < 3:
            print(
                "Usage: twitter_browser.py read-conversation <thread_url>",
                file=sys.stderr,
            )
            sys.exit(1)
        result = read_conversation(sys.argv[2])
        print(json.dumps(result, indent=2))

    elif cmd == "send-dm":
        if len(sys.argv) < 4:
            print(
                "Usage: twitter_browser.py send-dm <thread_url> <message>",
                file=sys.stderr,
            )
            sys.exit(1)
        result = send_dm(sys.argv[2], sys.argv[3])
        print(json.dumps(result, indent=2))

    elif cmd == "notifications":
        scroll_count = 8
        if len(sys.argv) >= 3:
            try:
                scroll_count = int(sys.argv[2])
            except ValueError:
                print(f"notifications: scroll_count must be int, got {sys.argv[2]!r}", file=sys.stderr)
                sys.exit(1)
        result = discover_notifications(scroll_count=scroll_count)
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
