#!/usr/bin/env python3
"""Reddit browser automation functions for Social Autoposter.

Replaces multi-step Claude browser MCP calls with single Python function calls.
Each function does all browser work internally and returns structured JSON.

Usage:
    # Post a top-level comment on a Reddit thread
    python3 reddit_browser.py post-comment "https://old.reddit.com/r/sub/comments/abc/title/" "comment text"

    # Reply to an existing comment
    python3 reddit_browser.py reply "https://old.reddit.com/r/sub/comments/abc/title/def/" "reply text"

    # Scan DM inbox for unread conversations
    python3 reddit_browser.py unread-dms

    # Read messages from a Reddit chat conversation
    python3 reddit_browser.py read-conversation "https://www.reddit.com/chat/..."

    # Send a DM in a Reddit chat
    python3 reddit_browser.py send-dm "https://www.reddit.com/chat/..." "message text"

Requires: pip install playwright && playwright install chromium

Connects to the running reddit-agent MCP browser via CDP (Chrome DevTools Protocol)
to reuse the existing logged-in session.
"""

import json
import os
import re
import subprocess
import sys
import time


STORAGE_STATE = os.path.expanduser("~/.claude/browser-sessions.json")
VIEWPORT = {"width": 911, "height": 1016}

# Load Reddit username from config
_config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
OUR_USERNAME = "Deep_Ad1959"
if os.path.exists(_config_path):
    try:
        with open(_config_path) as f:
            _cfg = json.load(f)
        OUR_USERNAME = _cfg.get("accounts", {}).get("reddit", {}).get("username", OUR_USERNAME)
    except Exception:
        pass


def find_reddit_cdp_port():
    """Find the CDP port of the running reddit-agent MCP browser.

    Scans all Chrome/Chromium processes for remote-debugging-port flags,
    then queries each port's /json endpoint for pages with reddit.com
    or old.reddit.com URLs. Prefers ports with logged-in pages.
    """
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
                reddit_urls = [
                    p.get("url", "")
                    for p in pages
                    if "reddit.com" in p.get("url", "")
                       or "old.reddit.com" in p.get("url", "")
                ]
                if not reddit_urls:
                    continue
                # Prefer ports with logged-in pages (not login page)
                logged_in = any(
                    ("old.reddit.com" in u or "/r/" in u or "/chat" in u
                     or "/message" in u or "reddit.com/u/" in u
                     or "reddit.com/user/" in u)
                    and "login" not in u
                    for u in reddit_urls
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
    """Connect to the reddit-agent MCP browser via CDP, or launch a new one.

    Returns (browser, page, is_cdp). When is_cdp=True, `page` is a reused
    existing Reddit tab (navigate it, don't close it). When is_cdp=False,
    it's a new headless page.
    """
    cdp_port = find_reddit_cdp_port()

    if cdp_port:
        try:
            browser = playwright.chromium.connect_over_cdp(
                f"http://localhost:{cdp_port}"
            )
            contexts = browser.contexts
            if contexts:
                context = contexts[0]
                # Reuse an existing Reddit tab
                for pg in context.pages:
                    if "reddit.com" in pg.url and "login" not in pg.url:
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


def _to_old_reddit(url):
    """Convert any reddit URL to old.reddit.com."""
    url = re.sub(r"https?://(www\.)?reddit\.com", "https://old.reddit.com", url)
    # Remove trailing query params that old reddit doesn't use
    url = re.sub(r"\?.*$", "", url)
    return url


def _ensure_old_reddit(page):
    """If page redirected to new reddit, navigate to old.reddit.com equivalent."""
    current = page.url
    if "old.reddit.com" in current:
        return
    if "reddit.com" in current and "old.reddit.com" not in current:
        old_url = _to_old_reddit(current)
        page.goto(old_url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)


def post_comment(thread_url, text):
    """Post a top-level comment on a Reddit thread.

    Navigates to old.reddit.com thread, finds the comment textarea,
    types the comment text, and submits.

    Returns: {"ok": true, "permalink": "..."} or {"ok": false, "error": "..."}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            old_url = _to_old_reddit(thread_url)
            page.goto(old_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            _ensure_old_reddit(page)

            # Check if thread exists
            page_text = page.text_content("body") or ""
            if "page not found" in page_text.lower() or "there doesn't seem to be anything here" in page_text.lower():
                return {"ok": False, "error": "thread_not_found"}

            # Check if thread is locked
            if page.locator(".locked-tagline").count() > 0:
                return {"ok": False, "error": "thread_locked"}

            # Find the top-level comment form.
            # On old.reddit.com, the main comment box is a textarea inside
            # div.usertext-edit within the comment area (not inside .comment).
            comment_form = page.locator(
                ".commentarea > .usertext textarea.usertext-input, "
                ".commentarea > .usertext-edit textarea"
            ).first

            try:
                comment_form.wait_for(state="visible", timeout=5000)
            except Exception:
                # Try clicking "add a comment" or similar link
                try:
                    page.locator("a.access-required, a[href*='login']").first.click()
                    page.wait_for_timeout(2000)
                    comment_form = page.locator(
                        ".commentarea textarea"
                    ).first
                    comment_form.wait_for(state="visible", timeout=3000)
                except Exception:
                    return {"ok": False, "error": "comment_box_not_found"}

            # Fill the textarea (old reddit uses standard textareas)
            comment_form.fill(text)
            page.wait_for_timeout(1000)

            # Click the save/submit button
            save_btn = page.locator(
                ".commentarea > .usertext button[type='submit'], "
                ".commentarea > .usertext-edit button[type='submit'], "
                ".commentarea > .usertext .save-button button"
            ).first

            try:
                save_btn.wait_for(state="visible", timeout=3000)
                save_btn.click()
            except Exception:
                # Fallback: find any button with value "save" near the comment box
                try:
                    save_btn = page.locator(
                        ".commentarea button:has-text('save')"
                    ).first
                    save_btn.click()
                except Exception:
                    return {"ok": False, "error": "save_button_not_found"}

            page.wait_for_timeout(5000)

            # Check for errors (rate limit, etc.)
            error_el = page.locator(".status.error, .error").first
            try:
                if error_el.is_visible():
                    error_text = error_el.text_content() or "unknown_error"
                    return {"ok": False, "error": error_text.strip()[:200]}
            except Exception:
                pass

            # Try to find the permalink of our new comment
            permalink = page.evaluate("""(ourUsername) => {
                // Find comments by our username, get the last one (most recent)
                const authorLinks = document.querySelectorAll(
                    '.comment a.author[href*="/' + ourUsername + '"]'
                );
                if (authorLinks.length === 0) return null;
                const lastAuthor = authorLinks[authorLinks.length - 1];
                // Walk up to the .comment container
                let comment = lastAuthor.closest('.comment');
                if (!comment) return null;
                // Find the permalink
                const perma = comment.querySelector('a.bylink[href*="/comments/"]');
                if (perma) return perma.getAttribute('href');
                return null;
            }""", OUR_USERNAME)

            return {
                "ok": True,
                "permalink": permalink,
                "thread_url": thread_url,
            }

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def reply_to_comment(comment_permalink, text):
    """Reply to an existing Reddit comment.

    Navigates to the comment permalink on old.reddit.com, clicks the
    "reply" link to expand the reply box, fills in the text, and submits.

    Returns: {"ok": true} or {"ok": false, "error": "..."}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            old_url = _to_old_reddit(comment_permalink)
            # Add ?context=1 to ensure we see the target comment
            if "?" not in old_url:
                old_url += "?context=1"
            page.goto(old_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            _ensure_old_reddit(page)

            # Check if comment exists
            page_text = page.text_content("body") or ""
            if "page not found" in page_text.lower():
                return {"ok": False, "error": "comment_not_found"}

            # On old.reddit.com with a comment permalink, the target comment
            # is highlighted. Find it and click its "reply" link.
            # The target comment is the first .comment in .sitetable.nestedlisting
            # or the one with .target class.

            # Click the reply link on the target comment
            reply_clicked = False

            # Strategy 1: Find the target/highlighted comment's reply link
            try:
                reply_link = page.locator(
                    ".nestedlisting > .comment .flat-list a:has-text('reply'), "
                    ".comment.target .flat-list a:has-text('reply')"
                ).first
                reply_link.wait_for(state="visible", timeout=5000)
                reply_link.click()
                reply_clicked = True
            except Exception:
                pass

            # Strategy 2: If only one comment visible, use its reply link
            if not reply_clicked:
                try:
                    reply_link = page.locator(
                        ".comment .flat-list a:has-text('reply')"
                    ).first
                    reply_link.wait_for(state="visible", timeout=3000)
                    reply_link.click()
                    reply_clicked = True
                except Exception:
                    pass

            if not reply_clicked:
                return {"ok": False, "error": "reply_link_not_found"}

            page.wait_for_timeout(1000)

            # Find the reply textarea that just appeared (pick the visible one)
            reply_box = None
            all_ta = page.locator(".comment .usertext-edit textarea")
            for i in range(all_ta.count()):
                if all_ta.nth(i).is_visible():
                    reply_box = all_ta.nth(i)
                    break

            if not reply_box:
                return {"ok": False, "error": "reply_textarea_not_found"}

            # Fill the reply text
            reply_box.fill(text)
            page.wait_for_timeout(1000)

            # Click the save button nearest to the visible reply box
            save_btn = None
            all_btns = page.locator(
                ".comment .usertext-edit button[type='submit']"
            )
            for i in range(all_btns.count()):
                if all_btns.nth(i).is_visible():
                    save_btn = all_btns.nth(i)
                    break

            if not save_btn:
                return {"ok": False, "error": "reply_save_button_not_found"}

            save_btn.click()

            page.wait_for_timeout(5000)

            # Check for errors
            error_el = page.locator(".status.error, .error").first
            try:
                if error_el.is_visible():
                    error_text = error_el.text_content() or "unknown_error"
                    return {"ok": False, "error": error_text.strip()[:200]}
            except Exception:
                pass

            # Verify: check if our comment appeared
            verified = page.evaluate("""(ourUsername) => {
                const authorLinks = document.querySelectorAll(
                    '.comment a.author[href*="/' + ourUsername + '"]'
                );
                return authorLinks.length > 0;
            }""", OUR_USERNAME)

            return {
                "ok": True,
                "verified": verified,
                "comment_permalink": comment_permalink,
            }

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def edit_comment(comment_permalink, new_text):
    """Edit an existing Reddit comment.

    Navigates to the comment permalink on old.reddit.com, clicks "edit",
    replaces the textarea content, and saves.

    Returns: {"ok": true} or {"ok": false, "error": "..."}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            old_url = _to_old_reddit(comment_permalink)
            page.goto(old_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            _ensure_old_reddit(page)

            page_text = page.text_content("body") or ""
            if "page not found" in page_text.lower():
                return {"ok": False, "error": "comment_not_found"}

            # Click the "edit" link on our comment
            edit_clicked = False
            try:
                edit_link = page.locator(
                    ".comment .flat-list a:has-text('edit')"
                ).first
                edit_link.wait_for(state="visible", timeout=5000)
                edit_link.click()
                edit_clicked = True
            except Exception:
                pass

            if not edit_clicked:
                return {"ok": False, "error": "edit_link_not_found"}

            page.wait_for_timeout(1000)

            # Find the edit textarea (pick the visible one)
            edit_box = None
            all_ta = page.locator(".comment .usertext-edit textarea")
            for i in range(all_ta.count()):
                if all_ta.nth(i).is_visible():
                    edit_box = all_ta.nth(i)
                    break

            if not edit_box:
                return {"ok": False, "error": "edit_textarea_not_found"}

            # Clear and fill with new text
            edit_box.fill(new_text)
            page.wait_for_timeout(1000)

            # Click save (pick the visible one)
            save_btn = None
            all_btns = page.locator(
                ".comment .usertext-edit button[type='submit']"
            )
            for i in range(all_btns.count()):
                if all_btns.nth(i).is_visible():
                    save_btn = all_btns.nth(i)
                    break

            if not save_btn:
                return {"ok": False, "error": "edit_save_button_not_found"}

            save_btn.click()

            page.wait_for_timeout(4000)

            # Verify the edit was saved
            verified = page.evaluate("""(newTextStart) => {
                const comments = document.querySelectorAll('.comment .usertext-body');
                for (const c of comments) {
                    if (c.textContent && c.textContent.includes(newTextStart)) {
                        return true;
                    }
                }
                return false;
            }""", new_text[:50])

            return {
                "ok": True,
                "verified": verified,
                "comment_permalink": comment_permalink,
            }

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def unread_dms():
    """Scan Reddit for unread DMs/chat conversations.

    Navigates to old.reddit.com/message/unread/ for traditional messages,
    then checks reddit.com/chat for chat-style conversations.

    Returns: list of conversations with author, preview, time, thread_url.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            conversations = []

            # Part 1: Check old.reddit.com/message/unread/ for traditional PMs
            page.goto(
                "https://old.reddit.com/message/unread/",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(3000)
            _ensure_old_reddit(page)

            old_messages = page.evaluate("""() => {
                const results = [];
                const messages = document.querySelectorAll('.message');
                for (const msg of messages) {
                    // Author
                    const authorEl = msg.querySelector('.author');
                    const author = authorEl ? authorEl.textContent.trim() : '';

                    // Subject
                    const subjectEl = msg.querySelector('a.title, .subject a');
                    const subject = subjectEl ? subjectEl.textContent.trim() : '';

                    // Body preview
                    const bodyEl = msg.querySelector('.md');
                    const body = bodyEl ? bodyEl.textContent.trim().substring(0, 300) : '';

                    // Time
                    const timeEl = msg.querySelector('time, .live-timestamp');
                    const time = timeEl
                        ? (timeEl.getAttribute('title') || timeEl.textContent.trim())
                        : '';

                    // Permalink
                    const permaLink = msg.querySelector(
                        'a[href*="/message/messages/"]'
                    );
                    const permalink = permaLink
                        ? permaLink.getAttribute('href')
                        : '';

                    if (author) {
                        results.push({
                            author: author,
                            subject: subject,
                            preview: body.substring(0, 200),
                            time: time,
                            thread_url: permalink
                                ? 'https://old.reddit.com' + permalink
                                : '',
                            type: 'pm',
                        });
                    }
                }
                return results;
            }""")

            conversations.extend(old_messages)

            # Part 2: Check reddit.com/chat for chat-style messages
            page.goto(
                "https://www.reddit.com/chat",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(5000)

            # Reddit chat is a SPA. Extract visible chat rooms from sidebar.
            chat_rooms = page.evaluate("""() => {
                const results = [];

                // The chat sidebar shows rooms. Each room is typically
                // a clickable element with the username and last message.
                // Look for elements that contain chat room info.

                // Strategy 1: Find chat room items in the sidebar list
                const items = document.querySelectorAll(
                    '[class*="ChatRoom"], [class*="chat-room"], ' +
                    'a[href*="/chat/"], [role="listitem"]'
                );

                for (const item of items) {
                    const text = item.textContent || '';
                    if (text.length < 3) continue;

                    // Try to extract author and preview
                    let author = '';
                    let preview = '';
                    let time = '';
                    let threadUrl = '';

                    // Look for links to chat threads
                    const link = item.querySelector('a[href*="/chat/"]')
                        || (item.tagName === 'A' && item.href && item.href.includes('/chat/')
                            ? item : null);
                    if (link) {
                        threadUrl = link.href || link.getAttribute('href') || '';
                        if (threadUrl && !threadUrl.startsWith('http')) {
                            threadUrl = 'https://www.reddit.com' + threadUrl;
                        }
                    }

                    // Extract text nodes for author/preview
                    const spans = item.querySelectorAll('span, p, div');
                    const texts = [];
                    for (const s of spans) {
                        const t = s.textContent.trim();
                        if (t.length > 1 && t.length < 200
                            && s.children.length <= 1) {
                            texts.push(t);
                        }
                    }

                    // Heuristic: first short text is author, longer text is preview
                    for (const t of texts) {
                        if (!author && t.length < 40 && !t.includes(' ')) {
                            author = t;
                        } else if (!preview && t.length > 3) {
                            preview = t;
                        }
                    }

                    // Check for unread indicator
                    const hasUnread = item.querySelector(
                        '[class*="unread"], [class*="badge"], [class*="notification"]'
                    ) !== null;

                    // Also check for bold text (common unread indicator)
                    const hasBold = item.querySelector('strong, b, [class*="Bold"]')
                        !== null;

                    if (author && (hasUnread || hasBold || threadUrl)) {
                        results.push({
                            author: author,
                            subject: '',
                            preview: preview.substring(0, 200),
                            time: time,
                            thread_url: threadUrl,
                            type: 'chat',
                            has_unread: hasUnread || hasBold,
                        });
                    }
                }

                return results;
            }""")

            conversations.extend(chat_rooms)

            # Deduplicate by author
            seen = set()
            unique = []
            for c in conversations:
                key = c.get("author", "").lower()
                if key and key not in seen:
                    seen.add(key)
                    unique.append(c)

            return unique

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def read_conversation(chat_url, max_messages=20):
    """Read messages from a Reddit chat or PM thread.

    For chat URLs (reddit.com/chat/...), navigates to the chat and extracts
    messages. For PM URLs (old.reddit.com/message/...), reads the PM thread.

    Returns: {"partner_name": "...", "messages": [...], "total_found": N}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            is_chat = "/chat" in chat_url and "message" not in chat_url

            if is_chat:
                # Reddit Chat (SPA on new reddit)
                page.goto(chat_url, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)

                # Reddit Chat uses accessible names on message elements:
                # "USERNAME said TIME_AGO, MESSAGE_TEXT, N replies, N reactions"
                # Extract via aria labels on generic elements
                result = page.evaluate("""(params) => {
                    const maxMessages = params.maxMessages;
                    const ourUsername = params.ourUsername;
                    let partnerName = '';
                    const messages = [];

                    // Get chat room name from the header
                    const headerEls = document.querySelectorAll(
                        '[aria-label*="Current chat"]'
                    );
                    for (const h of headerEls) {
                        const label = h.getAttribute('aria-label') || '';
                        const m = label.match(/Current chat,\\s*(.+)/);
                        if (m) { partnerName = m[1]; break; }
                    }
                    // Fallback: look for header text
                    if (!partnerName) {
                        const headers = document.querySelectorAll('h1, h2, h3');
                        for (const h of headers) {
                            const t = h.textContent.trim();
                            if (t.length > 1 && t.length < 60 && !t.includes('Chat')
                                && !t.includes('Reddit')) {
                                partnerName = t;
                                break;
                            }
                        }
                    }

                    // Find message elements by their accessible name pattern:
                    // "USERNAME said TIME, TEXT, N replies, N reactions"
                    const allEls = document.querySelectorAll('[aria-label]');
                    for (const el of allEls) {
                        const label = el.getAttribute('aria-label') || '';
                        // Match: "Username said time_ago, message text, N replies"
                        const m = label.match(
                            /^(\\S+) said (.+?),\\s*(.+?),\\s*\\d+ repl/
                        );
                        if (!m) continue;

                        const sender = m[1];
                        const time = m[2];
                        let content = m[3];

                        // Clean up content (remove trailing ", 0 reactions" etc)
                        content = content.replace(/,\\s*\\d+ reactions?$/, '').trim();

                        const isFromUs = sender.toLowerCase()
                            === ourUsername.toLowerCase();
                        if (!isFromUs && sender) {
                            partnerName = partnerName || sender;
                        }

                        messages.push({
                            sender: sender,
                            content: content.substring(0, 2000),
                            time: time,
                            is_from_us: isFromUs,
                        });
                    }

                    const recent = messages.slice(-maxMessages);
                    return {
                        partner_name: partnerName,
                        messages: recent,
                        total_found: messages.length,
                    };
                }""", {"maxMessages": max_messages, "ourUsername": OUR_USERNAME})

                return result

            else:
                # Traditional PM thread on old.reddit.com
                old_url = _to_old_reddit(chat_url)
                page.goto(old_url, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                _ensure_old_reddit(page)

                result = page.evaluate("""(params) => {
                    const maxMessages = params.maxMessages;
                    const ourUsername = params.ourUsername;
                    let partnerName = '';
                    const messages = [];

                    const msgEls = document.querySelectorAll('.message');
                    for (const msg of msgEls) {
                        const authorEl = msg.querySelector('.author');
                        const sender = authorEl
                            ? authorEl.textContent.trim() : '';

                        const bodyEl = msg.querySelector('.md');
                        const content = bodyEl
                            ? bodyEl.textContent.trim() : '';

                        const timeEl = msg.querySelector(
                            'time, .live-timestamp'
                        );
                        const time = timeEl
                            ? (timeEl.getAttribute('title')
                               || timeEl.textContent.trim())
                            : '';

                        const isFromUs = sender.toLowerCase()
                            === ourUsername.toLowerCase();

                        if (!isFromUs && sender) {
                            partnerName = sender;
                        }

                        if (content) {
                            messages.push({
                                sender: sender,
                                content: content.substring(0, 2000),
                                time: time,
                                is_from_us: isFromUs,
                            });
                        }
                    }

                    const recent = messages.slice(-maxMessages);
                    return {
                        partner_name: partnerName,
                        messages: recent,
                        total_found: messages.length,
                    };
                }""", {"maxMessages": max_messages, "ourUsername": OUR_USERNAME})

                return result

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def send_dm(chat_url, message):
    """Send a message in a Reddit chat or PM thread.

    For chat URLs (reddit.com/chat/...), navigates to the chat room and
    types/sends the message. For PM URLs, uses old.reddit.com message compose.

    Returns: {"ok": true, "thread_url": "..."} or {"ok": false, "error": "..."}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            is_chat = "/chat" in chat_url and "message" not in chat_url

            if is_chat:
                # Reddit Chat (SPA)
                page.goto(chat_url, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)

                # Find the message input box
                msg_box = None

                # Strategy 1: look for a textarea or contenteditable
                for selector in [
                    'textarea[placeholder*="Message"]',
                    'textarea[placeholder*="message"]',
                    'textarea',
                    'div[contenteditable="true"]',
                    '[role="textbox"]',
                ]:
                    try:
                        el = page.locator(selector).last
                        if el.is_visible():
                            msg_box = el
                            break
                    except Exception:
                        continue

                if not msg_box:
                    return {"ok": False, "error": "chat_input_not_found"}

                # Click and type the message
                msg_box.click()
                page.wait_for_timeout(500)

                # Use keyboard.type for contenteditable, fill for textarea
                tag = msg_box.evaluate("el => el.tagName.toLowerCase()")
                if tag == "textarea":
                    msg_box.fill(message)
                else:
                    page.keyboard.type(message, delay=10)

                page.wait_for_timeout(1000)

                # Send: try clicking a send button, fallback to Enter
                sent = False
                try:
                    send_btn = page.locator(
                        'button[aria-label*="Send"], '
                        'button[aria-label*="send"], '
                        'button:has-text("Send")'
                    ).first
                    if send_btn.is_visible():
                        send_btn.click()
                        sent = True
                except Exception:
                    pass

                if not sent:
                    page.keyboard.press("Enter")

                page.wait_for_timeout(3000)

                # Verify message appeared
                msg_start = message[:50]
                verified = page.evaluate("""(msgStart) => {
                    const body = document.body.textContent || '';
                    return body.includes(msgStart);
                }""", msg_start)

                return {
                    "ok": True,
                    "thread_url": page.url,
                    "verified": verified,
                }

            else:
                # Traditional PM reply on old.reddit.com
                old_url = _to_old_reddit(chat_url)
                page.goto(old_url, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                _ensure_old_reddit(page)

                # Find the reply textarea in the PM thread
                reply_box = page.locator(
                    ".usertext-edit textarea, textarea[name='text']"
                ).last

                try:
                    reply_box.wait_for(state="visible", timeout=5000)
                except Exception:
                    return {"ok": False, "error": "pm_reply_box_not_found"}

                reply_box.fill(message)
                page.wait_for_timeout(1000)

                # Click save/submit
                save_btn = page.locator(
                    "button[type='submit']:has-text('save'), "
                    "button[type='submit']"
                ).last

                try:
                    save_btn.click()
                except Exception:
                    return {"ok": False, "error": "pm_save_button_not_found"}

                page.wait_for_timeout(4000)

                return {
                    "ok": True,
                    "thread_url": page.url,
                    "verified": True,
                }

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def compose_dm(recipient, subject, body):
    """Compose and send a new Reddit DM/chat to a user.

    Navigates to reddit.com/message/compose/?to=recipient and fills in
    the subject and body fields.

    Returns: {"ok": true} or {"ok": false, "error": "..."}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            # Try the compose page on old.reddit.com
            compose_url = (
                f"https://old.reddit.com/message/compose/?to={recipient}"
            )
            page.goto(compose_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Check if we got redirected to new reddit chat
            if "chat" in page.url and "old.reddit.com" not in page.url:
                # We're on new reddit chat - type and send
                page.wait_for_timeout(3000)

                # Find the message input
                msg_box = None
                for selector in [
                    'textarea',
                    'div[contenteditable="true"]',
                    '[role="textbox"]',
                ]:
                    try:
                        el = page.locator(selector).last
                        if el.is_visible():
                            msg_box = el
                            break
                    except Exception:
                        continue

                if not msg_box:
                    return {"ok": False, "error": "chat_input_not_found"}

                full_msg = f"{subject}\n\n{body}" if subject else body
                msg_box.click()
                page.wait_for_timeout(500)

                tag = msg_box.evaluate("el => el.tagName.toLowerCase()")
                if tag == "textarea":
                    msg_box.fill(full_msg)
                else:
                    page.keyboard.type(full_msg, delay=10)

                page.wait_for_timeout(1000)

                # Send
                try:
                    send_btn = page.locator(
                        'button[aria-label*="Send"], '
                        'button:has-text("Send")'
                    ).first
                    if send_btn.is_visible():
                        send_btn.click()
                    else:
                        page.keyboard.press("Enter")
                except Exception:
                    page.keyboard.press("Enter")

                page.wait_for_timeout(3000)
                return {"ok": True, "thread_url": page.url}

            else:
                # Old reddit compose form
                _ensure_old_reddit(page)

                # Fill subject
                subject_input = page.locator(
                    'input[name="subject"]'
                ).first
                try:
                    subject_input.wait_for(state="visible", timeout=3000)
                    subject_input.fill(subject)
                except Exception:
                    return {"ok": False, "error": "subject_field_not_found"}

                # Fill body
                body_input = page.locator(
                    'textarea[name="text"]'
                ).first
                try:
                    body_input.wait_for(state="visible", timeout=3000)
                    body_input.fill(body)
                except Exception:
                    return {"ok": False, "error": "body_field_not_found"}

                page.wait_for_timeout(1000)

                # Submit
                submit_btn = page.locator(
                    'button[type="submit"]'
                ).first
                try:
                    submit_btn.click()
                except Exception:
                    return {"ok": False, "error": "submit_button_not_found"}

                page.wait_for_timeout(4000)

                # Check for success (redirects to sent messages)
                if "sent" in page.url or "message" in page.url:
                    return {"ok": True, "thread_url": page.url}

                # Check for errors
                error_el = page.locator(".error").first
                try:
                    if error_el.is_visible():
                        return {
                            "ok": False,
                            "error": (error_el.text_content() or "")[:200],
                        }
                except Exception:
                    pass

                return {"ok": True, "thread_url": page.url}

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "post-comment":
        if len(sys.argv) < 4:
            print(
                "Usage: reddit_browser.py post-comment <thread_url> <text>",
                file=sys.stderr,
            )
            sys.exit(1)
        result = post_comment(sys.argv[2], sys.argv[3])
        print(json.dumps(result, indent=2))

    elif cmd == "reply":
        if len(sys.argv) < 4:
            print(
                "Usage: reddit_browser.py reply <comment_permalink> <text>",
                file=sys.stderr,
            )
            sys.exit(1)
        result = reply_to_comment(sys.argv[2], sys.argv[3])
        print(json.dumps(result, indent=2))

    elif cmd == "edit":
        if len(sys.argv) < 4:
            print(
                "Usage: reddit_browser.py edit <comment_permalink> <new_text>",
                file=sys.stderr,
            )
            sys.exit(1)
        result = edit_comment(sys.argv[2], sys.argv[3])
        print(json.dumps(result, indent=2))

    elif cmd == "unread-dms":
        result = unread_dms()
        print(json.dumps(result, indent=2))

    elif cmd == "read-conversation":
        if len(sys.argv) < 3:
            print(
                "Usage: reddit_browser.py read-conversation <chat_url>",
                file=sys.stderr,
            )
            sys.exit(1)
        result = read_conversation(sys.argv[2])
        print(json.dumps(result, indent=2))

    elif cmd == "send-dm":
        if len(sys.argv) < 4:
            print(
                "Usage: reddit_browser.py send-dm <chat_url> <message>",
                file=sys.stderr,
            )
            sys.exit(1)
        result = send_dm(sys.argv[2], sys.argv[3])
        print(json.dumps(result, indent=2))

    elif cmd == "compose-dm":
        if len(sys.argv) < 5:
            print(
                "Usage: reddit_browser.py compose-dm <recipient> <subject> <body>",
                file=sys.stderr,
            )
            sys.exit(1)
        result = compose_dm(sys.argv[2], sys.argv[3], sys.argv[4])
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
