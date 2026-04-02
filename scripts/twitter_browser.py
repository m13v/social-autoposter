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

import json
import os
import re
import subprocess
import sys


STORAGE_STATE = os.path.expanduser("~/.claude/browser-sessions.json")
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


def get_browser_and_page(playwright):
    """Connect to the twitter-agent MCP browser via CDP, or launch a new one.

    Returns (browser, page, is_cdp). When is_cdp=True, `page` is a reused
    existing Twitter tab (navigate it, don't close it). When is_cdp=False,
    it's a new headless page.
    """
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

    # Fallback: launch new headless browser
    browser = playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        storage_state=STORAGE_STATE if os.path.exists(STORAGE_STATE) else None,
        viewport=VIEWPORT,
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    page = context.new_page()
    return browser, page, False


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


def reply_to_tweet(tweet_url, text):
    """Reply to a tweet.

    Navigates to the tweet, clicks the reply box, types the reply, and submits.

    Returns: {"ok": true, "tweet_url": "..."} or {"ok": false, "error": "..."}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            page.goto(tweet_url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            # Check if page exists
            page_text = page.text_content("main") or ""
            if "this page doesn't exist" in page_text.lower():
                return {"ok": False, "error": "tweet_not_found"}

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

            # Click the Reply button (it should now be enabled)
            try:
                reply_btn = page.get_by_role("button", name="Reply").last
                reply_btn.wait_for(timeout=5000)
                reply_btn.click()
            except Exception:
                # Fallback: Ctrl+Enter to submit
                page.keyboard.press("Control+Enter")

            page.wait_for_timeout(3000)

            # Verify: check if the reply box is empty (cleared after posting)
            try:
                box_text = reply_box.text_content() or ""
                verified = len(box_text.strip()) == 0 or text not in box_text
            except Exception:
                verified = True

            return {
                "ok": True,
                "tweet_url": tweet_url,
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

            # Extract conversation list
            conversations = page.evaluate("""() => {
                const results = [];
                const items = document.querySelectorAll('main li, main [role="listitem"]');

                for (const item of items) {
                    // Find the conversation link
                    const link = item.querySelector('a[href*="/i/chat/"]');
                    if (!link) continue;

                    const threadUrl = link.href;

                    // Skip non-conversation links
                    if (!threadUrl.match(/\\/i\\/chat\\/[\\d-g]/)) continue;

                    // Extract author name - it's in nested generic divs
                    // Pattern: div > div > div > div with the name text
                    const textNodes = [];
                    const divs = item.querySelectorAll('div, span');
                    for (const d of divs) {
                        const t = d.textContent.trim();
                        if (t && t.length > 0 && t.length < 100) {
                            textNodes.push(t);
                        }
                    }

                    // Author name: first short text that's not a time indicator
                    let author = '';
                    let time = '';
                    let preview = '';

                    // Look for the avatar link to get the handle
                    let handle = '';
                    const avatarLink = item.querySelector('a[href^="https://x.com/"]');
                    if (avatarLink) {
                        const href = avatarLink.getAttribute('href') || '';
                        const m = href.match(/x\\.com\\/([^/]+)/);
                        if (m) handle = m[1];
                    }

                    // The link text has the full info in accessible name
                    const linkName = link.getAttribute('aria-label') || link.textContent || '';

                    // Parse from the structured DOM
                    // Name is in a div that's a sibling of the time div
                    const nameDiv = item.querySelectorAll('div[dir="ltr"], span[dir="ltr"]');
                    for (const nd of nameDiv) {
                        const t = nd.textContent.trim();
                        // Skip if it's a time, preview, or "You:" prefix
                        if (t.match(/^\\d+[hmd]$/) || t.match(/^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)/)) {
                            time = t;
                        } else if (!author && t.length > 0 && t.length < 50 &&
                                   !t.startsWith('You:') && !t.startsWith('@')) {
                            author = t;
                        }
                    }

                    // Get preview text - usually the longest text in the item
                    const allText = item.textContent || '';
                    // Preview is after the author name and time
                    if (author && allText.includes(author)) {
                        const afterAuthor = allText.substring(
                            allText.indexOf(author) + author.length
                        ).trim();
                        // Remove time prefix
                        preview = afterAuthor.replace(/^[\\s]*\\d+[hmd][\\s]*/, '').trim();
                        preview = preview.replace(/^(Sun|Mon|Tue|Wed|Thu|Fri|Sat).*?[\\s]+/, '').trim();
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
            page.goto(thread_url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            # Handle DM passcode if needed
            _handle_dm_passcode(page)
            page.wait_for_timeout(2000)

            result = page.evaluate("""(params) => {
                const maxMessages = params.maxMessages;
                const ourHandle = params.ourHandle;

                // Get the conversation partner name from the header
                // The header has the partner's name in a div after the back button
                let partnerName = '';
                let partnerHandle = '';

                // Strategy 1: Look for the name in the conversation header area
                // It's inside main > div > div > div with just the name text
                const main = document.querySelector('main');
                if (main) {
                    // The header typically has: [back btn] [avatar] [name div]
                    // Find the "View Profile" link to get the handle
                    const profileLink = main.querySelector('a[href*="x.com/"]');
                    if (profileLink) {
                        const href = profileLink.getAttribute('href') || '';
                        const m = href.match(/x\\.com\\/([^/]+)/);
                        if (m && m[1] !== ourHandle) partnerHandle = m[1];
                    }

                    // Get the name text from near the top of main
                    // Look for handle text like @username
                    const handleEls = main.querySelectorAll('div, span');
                    for (const el of handleEls) {
                        const t = el.textContent.trim();
                        if (t.startsWith('@') && t.length > 2 && t.length < 50 &&
                            !t.includes(' ') && t.substring(1) !== ourHandle) {
                            partnerHandle = t.substring(1);
                        }
                    }
                }

                // Find messages in the conversation
                // Messages are in listitem elements within the main area
                const items = main ? main.querySelectorAll('li, [role="listitem"]') : [];
                const messages = [];
                let currentDate = '';

                for (const item of items) {
                    const text = item.textContent || '';

                    // Date separator (e.g., "Mon, Mar 30", "Yesterday", "Today")
                    if (text.match(/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Today|Yesterday)/) &&
                        text.length < 30) {
                        currentDate = text.trim();
                        continue;
                    }

                    // Profile card at the top (has "View Profile" and "Joined")
                    if (text.includes('View Profile') || text.includes('Joined ')) {
                        // Extract partner name from this card
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

                    // Skip empty items
                    if (text.trim().length < 2) continue;

                    // Extract message content and time
                    // Messages have: content div + time div
                    // Our messages have a checkmark img after the time
                    const divs = item.querySelectorAll(':scope > div');
                    if (divs.length === 0) continue;

                    let content = '';
                    let time = '';
                    let isFromUs = false;

                    // Check for checkmark (indicates our message)
                    const checkmark = item.querySelector('img[src*="check"], svg');
                    const allImgs = item.querySelectorAll('img');

                    // Look for time pattern (e.g., "5:08 PM", "9:59 AM")
                    const timeMatch = text.match(/(\\d{1,2}:\\d{2}\\s*[AP]M)/);
                    if (timeMatch) {
                        time = timeMatch[1];
                    }

                    // Content is everything except the time
                    // The content div is typically the first child with actual text
                    const contentDivs = item.querySelectorAll('div');
                    for (const cd of contentDivs) {
                        const t = cd.textContent.trim();
                        // Skip time-only divs
                        if (t.match(/^\\d{1,2}:\\d{2}\\s*[AP]M$/)) continue;
                        // Skip if it's just the time repeated
                        if (t === time) continue;
                        // Get the deepest div with unique content
                        if (t.length > 2 && t.length < 5000 &&
                            !t.includes('View Profile') && !t.includes('Joined ')) {
                            // Check if this is a leaf-ish content div
                            const childDivs = cd.querySelectorAll('div');
                            if (childDivs.length <= 2) {
                                content = t.replace(/(\\d{1,2}:\\d{2}\\s*[AP]M)/g, '').trim();
                                if (content.length > 0) break;
                            }
                        }
                    }

                    if (!content || content.length < 1) continue;

                    // Determine sender: our messages typically appear on the right
                    // and have a different visual treatment.
                    // Check for reaction buttons (emoji, reply) which appear on hover
                    // for received messages
                    const buttons = item.querySelectorAll('button');
                    // If the item has exactly the content + time + checkmark pattern,
                    // it's likely our message
                    // Heuristic: our messages have fewer child elements and the
                    // checkmark image
                    const innerDivCount = item.querySelectorAll('div').length;

                    // Heuristic: our sent messages have SVG checkmarks
                    // (delivery confirmation icons) while received messages don't.
                    const svgCount = item.querySelectorAll('svg').length;
                    isFromUs = svgCount > 0;

                    messages.push({
                        sender: isFromUs ? 'us' : partnerName || partnerHandle || 'them',
                        content: content,
                        time: currentDate ? currentDate + ' ' + time : time,
                        is_from_us: isFromUs,
                    });
                }

                // Take last N messages
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
            page.goto(thread_url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            # Handle DM passcode if needed
            _handle_dm_passcode(page)
            page.wait_for_timeout(2000)

            # Find the message input box
            msg_box = None
            # Try "Unencrypted message" first (common label)
            try:
                msg_box = page.get_by_role("textbox", name="Unencrypted message")
                msg_box.wait_for(timeout=5000)
            except Exception:
                pass

            # Fallback: try "Start a new message"
            if not msg_box:
                try:
                    msg_box = page.get_by_role("textbox", name="Start a new message")
                    msg_box.wait_for(timeout=3000)
                except Exception:
                    pass

            # Fallback: any textbox in the compose area
            if not msg_box:
                try:
                    msg_box = page.locator(
                        'div[role="textbox"][contenteditable="true"]'
                    ).last
                    msg_box.wait_for(timeout=3000)
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

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
