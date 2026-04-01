#!/usr/bin/env python3
"""LinkedIn browser automation functions for Social Autoposter.

Replaces multi-step Claude browser MCP calls with single Python function calls.
Each function does all browser work internally and returns structured JSON.

Usage:
    # Discover actionable notifications
    python3 linkedin_browser.py notifications

    # Get comment context for a notification URL
    python3 linkedin_browser.py comment-context "https://www.linkedin.com/feed/update/..."

    # Search for posts and return activity IDs
    python3 linkedin_browser.py search "https://www.linkedin.com/search/results/content/?keywords=..."

    # Extract activity ID from a post page
    python3 linkedin_browser.py activity-id "https://www.linkedin.com/feed/update/..."

Requires: pip install playwright && playwright install chromium

Connects to the running linkedin-agent MCP browser via CDP (Chrome DevTools Protocol)
to reuse the existing logged-in session. Falls back to launching a new browser if
the linkedin-agent is not running.
"""

import json
import os
import re
import subprocess
import sys


STORAGE_STATE = os.path.expanduser("~/.claude/browser-sessions.json")
VIEWPORT = {"width": 911, "height": 1016}


def find_linkedin_cdp_port():
    """Find the CDP port of the running linkedin-agent MCP browser.

    Prefers ports with actual logged-in LinkedIn pages (feed, notifications)
    over ports that just have a login page.
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
                linkedin_urls = [
                    p.get("url", "") for p in pages
                    if "linkedin.com" in p.get("url", "")
                ]
                if not linkedin_urls:
                    continue
                # Prefer ports with logged-in pages (feed, update, notifications, search)
                logged_in = any(
                    ("feed" in u or "notifications" in u or "search" in u or "update" in u)
                    and "login" not in u and "uas/" not in u
                    for u in linkedin_urls
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
    """Connect to the linkedin-agent MCP browser via CDP, or launch a new one.

    Returns (browser, page, is_cdp). When is_cdp=True, `page` is a reused
    existing LinkedIn tab (navigate it, don't close it). When is_cdp=False,
    it's a new headless page.
    """
    cdp_port = find_linkedin_cdp_port()

    if cdp_port:
        try:
            browser = playwright.chromium.connect_over_cdp(
                f"http://localhost:{cdp_port}"
            )
            contexts = browser.contexts
            if contexts:
                context = contexts[0]
                # Reuse an existing LinkedIn tab (new tabs don't inherit cookies)
                for pg in context.pages:
                    if "linkedin.com" in pg.url and "login" not in pg.url:
                        return browser, pg, True
                # If no LinkedIn tab, try the first page
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


def discover_notifications(max_load_more=10):
    """Discover LinkedIn notifications using the internal Voyager API.

    First tries the JS extractor (scan_linkedin_notifications.js) which uses
    LinkedIn's internal API for rich data. Falls back to HTML scraping.

    Returns JSON with notifications in the format expected by scan_linkedin_notifications.py.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            # Navigate to LinkedIn (needed for cookies/CSRF)
            if "linkedin.com" not in page.url:
                page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

            # Try running the JS extractor which uses Voyager API
            js_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "scan_linkedin_notifications.js",
            )
            if os.path.exists(js_path):
                with open(js_path) as f:
                    js_code = f.read()

                # The JS is an async function that takes (page), we need to
                # call page.evaluate with the inner evaluate code
                result = page.evaluate("""async () => {
                    const csrfToken = (document.cookie.match(/JSESSIONID="?([^";]+)/) || [])[1] || '';
                    if (!csrfToken) return JSON.stringify({ error: 'No CSRF token - not logged in' });

                    const headers = {
                        'csrf-token': csrfToken,
                        'accept': 'application/vnd.linkedin.normalized+json+2.1',
                        'x-restli-protocol-version': '2.0.0',
                    };

                    const actionableTypes = new Set([
                        'REPLIED_TO_YOUR_COMMENT',
                        'COMMENTED_ON_YOUR_UPDATE',
                        'COMMENTED_ON_YOUR_POST',
                        'MENTIONED_YOU_IN_A_COMMENT',
                        'MENTIONED_YOU_IN_THIS',
                    ]);

                    const allNotifications = [];
                    const profiles = {};

                    for (let start = 0; start < 100; start += 25) {
                        const resp = await fetch(
                            `/voyager/api/voyagerIdentityDashNotificationCards?decorationId=com.linkedin.voyager.dash.deco.identity.notifications.CardsCollection-80&count=25&filterUrn=urn%3Ali%3Afsd_notificationFilter%3AALL&q=notifications&start=${start}`,
                            { headers }
                        );
                        if (resp.status !== 200) {
                            if (start === 0) return JSON.stringify({ error: `API returned ${resp.status}` });
                            break;
                        }

                        const data = await resp.json();
                        const included = data.included || [];

                        included
                            .filter(e => e.$type === 'com.linkedin.voyager.dash.identity.profile.Profile')
                            .forEach(p => {
                                const name = (p.profilePicture && p.profilePicture.a11yText) || '';
                                if (name) profiles[p.entityUrn] = name;
                            });

                        included
                            .filter(e => e.$type === 'com.linkedin.voyager.dash.identity.notifications.Card')
                            .forEach(card => {
                                const objUrn = card.objectUrn || '';
                                const typeMatch = objUrn.match(/,([A-Z_]+),/) || objUrn.match(/,([A-Z_]+)\\)/);
                                const notifType = typeMatch ? typeMatch[1] : 'UNKNOWN';

                                if (!actionableTypes.has(notifType)) return;

                                const commentMatch = objUrn.match(/urn:li:comment:\\([^)]+\\)/);
                                const commentUrn = commentMatch ? commentMatch[0] : '';

                                let activityId = '';
                                const actMatch = commentUrn.match(/activity:(\\d+)/) || commentUrn.match(/ugcPost:(\\d+)/);
                                if (actMatch) activityId = actMatch[1];

                                const headline = (card.headline && card.headline.text) || '';
                                const authorMatch = headline.match(/^(.+?)\\s+(replied|commented|mentioned)/);
                                const authorName = authorMatch ? authorMatch[1] : '';

                                const profileUrl = (card.headerImage && card.headerImage.actionTarget) || '';
                                const navUrl = (card.cardAction && card.cardAction.actionTarget) || '';
                                const postContent = (card.contentSecondaryText && card.contentSecondaryText.text) || '';

                                allNotifications.push({
                                    type: notifType,
                                    commentUrn,
                                    activityId,
                                    authorName,
                                    profileUrl,
                                    navigationUrl: navUrl,
                                    headline,
                                    postContent: postContent.substring(0, 500),
                                    commentText: '',
                                });
                            });

                        if ((data.data || {}).paging) {
                            const paging = data.data.paging;
                            if (start + paging.count >= paging.total) break;
                        }
                    }

                    return JSON.stringify({ notifications: allNotifications, total: allNotifications.length });
                }""")

                if isinstance(result, str):
                    data = json.loads(result)
                else:
                    data = result

                if "error" not in data:
                    return data.get("notifications", [])

            # Fallback: HTML scraping approach
            page.goto("https://www.linkedin.com/notifications/", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            for _ in range(max_load_more):
                try:
                    btn = page.locator('button:has-text("Show more results")').first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        page.wait_for_timeout(1500)
                    else:
                        break
                except Exception:
                    break

            notifications = page.evaluate("""() => {
                const articles = document.querySelectorAll('article');
                const actionable = [];
                articles.forEach(a => {
                    const text = a.innerText || '';
                    let type = null;
                    if (text.includes('replied to your comment')) type = 'reply';
                    else if (text.includes('mentioned you in a comment')) type = 'mention';
                    else if (text.includes('commented on your post')) type = 'comment_on_post';
                    if (!type) return;

                    const strong = a.querySelector('strong');
                    const name = strong ? strong.textContent.trim() : 'unknown';
                    const link = a.querySelector('a[href*="commentUrn"]')
                        || a.querySelector('a[href*="replyUrn"]')
                        || a.querySelector('a[href*="feed/update"]');
                    const url = link ? link.getAttribute('href') : null;

                    let activity_id = null;
                    if (url) {
                        const match = url.match(/activity[:%3A]+(\\d+)/i);
                        if (match) activity_id = match[1];
                    }

                    actionable.push({ type, authorName: name, navigationUrl: url, activityId: activity_id });
                });
                return actionable;
            }""")

            return notifications

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def get_comment_context(comment_url):
    """Navigate to a comment URL and extract the comment's full context.

    Returns JSON:
    {"activity_id": "...", "comments": [{"urn": "...", "author": "...", "content": "..."}]}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            page.goto(comment_url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            result = page.evaluate("""() => {
                const html = document.body.innerHTML;

                // Extract activity IDs
                const activityMatches = html.match(/urn:li:activity:(\\d+)/g) || [];
                const activities = [...new Set(activityMatches)];
                const activity_id = activities.length > 0
                    ? activities[0].match(/activity:(\\d+)/)[1]
                    : null;

                // Extract comment URNs
                const commentMatches = html.match(/urn:li:comment:\\([^)]+\\)/g) || [];
                const comment_urns = [...new Set(commentMatches)];

                // Try to extract comment elements
                const comments = [];
                const commentEls = document.querySelectorAll(
                    'article.comments-comment-entity, [data-id*="urn:li:comment"], [class*="comment"]'
                );
                commentEls.forEach(el => {
                    const nameEl = el.querySelector(
                        '[class*="comment"] [class*="name"], [class*="author"], .comments-post-meta__name-text'
                    );
                    const contentEl = el.querySelector(
                        '[class*="comment-item__main-content"], [class*="comment"] [class*="content"], [class*="comment-body"]'
                    );
                    const dataId = el.getAttribute('data-id') || '';
                    if (nameEl || contentEl) {
                        comments.push({
                            urn: dataId || null,
                            author: nameEl ? nameEl.innerText.trim() : null,
                            content: contentEl ? contentEl.innerText.trim().substring(0, 500) : null,
                        });
                    }
                });

                // Fallback: extract from page text near comment indicators
                if (comments.length === 0) {
                    const main = document.querySelector('main');
                    const pageText = main ? main.innerText : document.body.innerText;
                    // Get all text blocks that look like comments
                    const blocks = pageText.split(/\\n{2,}/);
                    blocks.forEach(block => {
                        const trimmed = block.trim();
                        if (trimmed.length > 20 && trimmed.length < 1000 && !trimmed.includes('Premium')) {
                            comments.push({ urn: null, author: null, content: trimmed.substring(0, 500) });
                        }
                    });
                }

                return {
                    activity_id,
                    comment_urns,
                    comments: comments.slice(0, 20),
                    page_url: window.location.href,
                };
            }""")

            return result

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def search_posts(search_url, max_posts=10):
    """Browse a LinkedIn search URL and extract posts with activity IDs.

    Extracts activity IDs by opening each post's control menu ("...") and
    reading the Report link, which contains the activity URN. LinkedIn's new
    React rendering no longer exposes activity IDs in DOM attributes.

    Returns JSON: {"activity_ids": [...], "posts": [{activity_id, author, preview, text}, ...]}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            page.goto(search_url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            # Wait for search results to render (React lazy loads them)
            for attempt in range(5):
                has_posts = page.evaluate("""() => {
                    return document.querySelectorAll(
                        'button[aria-label*="Open control menu for post"]'
                    ).length;
                }""")
                if has_posts > 0:
                    break
                # Scroll a bit to trigger lazy loading
                page.evaluate("() => window.scrollBy(0, 500)")
                page.wait_for_timeout(2000)

            if not has_posts:
                # One more attempt: scroll to bottom and back
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(3000)
                page.evaluate("() => window.scrollTo(0, 0)")
                page.wait_for_timeout(2000)

            # First, extract all post metadata from the page
            post_metadata = page.evaluate("""() => {
                // Find all "Open control menu for post by X" buttons
                const menuBtns = document.querySelectorAll(
                    'button[aria-label*="Open control menu for post"]'
                );
                const results = [];
                for (const btn of menuBtns) {
                    // Extract author name from aria-label
                    const label = btn.getAttribute('aria-label') || '';
                    const authorMatch = label.match(/post by (.+)/);
                    const author = authorMatch ? authorMatch[1].trim() : null;

                    // Walk up to find the post container
                    let container = btn;
                    for (let j = 0; j < 15 && container; j++) {
                        container = container.parentElement;
                        const text = container ? (container.innerText || '') : '';
                        if (text.includes('Like') && text.includes('Comment') && text.length > 200) {
                            break;
                        }
                    }

                    // Profile URL
                    let profileUrl = null;
                    if (container) {
                        const link = container.querySelector('a[href*="/in/"], a[href*="/company/"]');
                        if (link) profileUrl = link.getAttribute('href');
                    }

                    // Post text
                    let text = '';
                    if (container) {
                        const spans = container.querySelectorAll('span, p');
                        for (const s of spans) {
                            const t = s.innerText.trim();
                            if (t.length > 50 && t.length < 3000 &&
                                !t.includes('Like') && !t.includes('Comment') &&
                                !t.includes('Repost') && !t.includes('Send') &&
                                !t.includes('Follow') && !t.includes('Open control')) {
                                if (!text.includes(t.substring(0, 40))) {
                                    text += t + ' ';
                                }
                                if (text.length > 500) break;
                            }
                        }
                    }

                    results.push({
                        author: author,
                        profile_url: profileUrl,
                        text: text.trim().substring(0, 500),
                    });
                }
                return results;
            }""")

            # Now click each menu button to extract activity IDs
            menu_buttons = page.locator(
                'button[aria-label*="Open control menu for post"]'
            ).all()

            posts = []
            activity_ids = []

            for i, btn in enumerate(menu_buttons[:max_posts]):
                try:
                    btn.click()
                    page.wait_for_timeout(1000)

                    # Extract activity ID from the Report link
                    activity_id = page.evaluate("""() => {
                        const reportLink = document.querySelector('a[href*="report-in-modal"]');
                        if (!reportLink) return null;
                        const href = reportLink.getAttribute('href') || '';
                        const match = href.match(/updateUrn=urn%3Ali%3Aactivity%3A(\\d+)/);
                        if (match) return match[1];
                        const match2 = href.match(/activity%3A(\\d+)/);
                        return match2 ? match2[1] : null;
                    }""")

                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)

                    if activity_id:
                        activity_ids.append(activity_id)
                        meta = post_metadata[i] if i < len(post_metadata) else {}
                        posts.append({
                            "activity_id": activity_id,
                            "author": meta.get("author"),
                            "profile_url": meta.get("profile_url"),
                            "text": meta.get("text", ""),
                            "preview": meta.get("text", "")[:300],
                        })

                except Exception:
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass

            return {
                "activity_ids": activity_ids,
                "posts": posts,
            }

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def extract_activity_id(post_url):
    """Navigate to a post and extract its activity ID.

    Returns JSON: {"activity_id": "...", "post_text": "...", "author": "..."}
    """
    from playwright.sync_api import sync_playwright

    # Try URL parsing first (no browser needed)
    import re
    m = re.search(r"activity[:%3A]+(\d+)", post_url, re.IGNORECASE)
    if m:
        activity_id = m.group(1)
    else:
        activity_id = None

    # If we got it from URL, still fetch post text for context
    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            page.goto(post_url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            result = page.evaluate("""() => {
                const html = document.body.innerHTML;

                // Extract activity ID
                const matches = html.match(/urn:li:activity:(\\d+)/g) || [];
                const activities = [...new Set(matches.map(m => m.match(/activity:(\\d+)/)[1]))];

                // Get post text
                const main = document.querySelector('main');
                const text = main ? main.innerText : '';
                // Find the post content (usually the longest paragraph-like block)
                const blocks = text.split(/\\n{2,}/).filter(b => b.trim().length > 30);
                const postText = blocks.length > 0
                    ? blocks.reduce((a, b) => a.length > b.length ? a : b).trim().substring(0, 500)
                    : '';

                // Author
                const authorEl = document.querySelector('a[href*="/company/"] span, a[href*="/in/"] span');
                const author = authorEl ? authorEl.textContent.trim() : null;

                return {
                    activity_id: activities[0] || null,
                    all_activity_ids: activities,
                    post_text: postText,
                    author,
                };
            }""")

            # Prefer URL-extracted ID, fall back to page-extracted
            if not activity_id and result.get("activity_id"):
                activity_id = result["activity_id"]

            result["activity_id"] = activity_id
            return result

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def scrape_stats(post_url, our_name="Matthew Diakonov", content_prefix=""):
    """Navigate to a LinkedIn post and scrape reaction count on our comment.

    Returns JSON: {"found": bool, "reactions": int, "comment_preview": str}
    """
    from playwright.sync_api import sync_playwright

    # Strip ?commentUrn= param (breaks comment rendering)
    clean_url = re.sub(r"\?commentUrn=.*", "", post_url)

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            page.goto(clean_url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            # Scroll down past the post
            page.evaluate("() => window.scrollBy(0, 600)")
            page.wait_for_timeout(2000)

            # Click Comment button to expand comments section
            try:
                comment_btn = page.locator('button[aria-label="Comment"]').first
                comment_btn.click(timeout=5000)
                page.wait_for_timeout(4000)
            except Exception:
                pass

            # Try to expand all comments
            for label in [
                "Load more comments",
                "load more",
                "See previous replies",
                "Load previous replies",
            ]:
                try:
                    btn = page.locator(f'button[aria-label*="{label}"]').first
                    btn.click(timeout=3000)
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

            # Extract our comment's reaction count using the "View more options" button
            # as anchor — LinkedIn's new DOM has no stable comment container classes
            result = page.evaluate(
                """({ourName, contentPrefix}) => {
                const res = {found: false, reactions: 0, comment_preview: ''};

                // Strategy 1: Find "View more options for <ourName>'s comment" button
                const optionsBtns = document.querySelectorAll('button[aria-label*="View more options"]');
                let commentContainer = null;
                for (const btn of optionsBtns) {
                    const label = btn.getAttribute('aria-label') || '';
                    if (label.toLowerCase().includes(ourName.toLowerCase())) {
                        // Walk up to find the comment container
                        commentContainer = btn;
                        for (let i = 0; i < 8; i++) {
                            commentContainer = commentContainer.parentElement;
                            if (!commentContainer) break;
                            const text = commentContainer.innerText || '';
                            if (text.length > 30 && text.length < 3000) {
                                // Check for reaction elements at this level
                                const reactionEl = commentContainer.querySelector(
                                    'button[aria-label*="Reaction"], button[aria-label*="reaction"]'
                                );
                                if (reactionEl || i >= 3) break;
                            }
                        }
                        break;
                    }
                }

                if (commentContainer) {
                    res.found = true;
                    // Get comment text
                    const spans = commentContainer.querySelectorAll('span, p');
                    for (const s of spans) {
                        const t = s.innerText.trim();
                        if (t.length > 20 && t.length < 2000 &&
                            !t.includes(ourName) && !t.includes('View more') &&
                            !t.includes('Like') && !t.includes('Reply')) {
                            res.comment_preview = t.substring(0, 80);
                            break;
                        }
                    }
                    // Get reactions
                    const reactionEl = commentContainer.querySelector(
                        'button[aria-label*="Reaction"], button[aria-label*="reaction"]'
                    );
                    if (reactionEl) {
                        const label = reactionEl.getAttribute('aria-label') || '';
                        const m = label.match(/([\d,]+)\\s*[Rr]eaction/);
                        if (m) res.reactions = parseInt(m[1].replace(/,/g, ''), 10);
                        else {
                            const t = reactionEl.textContent.trim().replace(/,/g, '');
                            const n = parseInt(t, 10);
                            if (!isNaN(n)) res.reactions = n;
                        }
                    }
                    return res;
                }

                // Strategy 2: Content prefix match in page text
                if (contentPrefix) {
                    const prefixClean = contentPrefix.replace(/[^a-z0-9 ]/gi, '').substring(0, 60).toLowerCase();
                    const bodyClean = document.body.innerText.replace(/[^a-z0-9 ]/gi, '').toLowerCase();
                    if (prefixClean.length > 20 && bodyClean.includes(prefixClean)) {
                        res.found = true;
                        res.comment_preview = '(found by content match)';
                        return res;
                    }
                }

                // Strategy 3: Name scan fallback
                if (document.body.innerText.includes(ourName)) {
                    res.found = true;
                    res.comment_preview = '(found by name scan)';
                }

                return res;
            }""",
                {"ourName": our_name, "contentPrefix": content_prefix},
            )

            return result

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def audit_post(post_url):
    """Check if a LinkedIn post is still live or has been deleted/removed.

    Returns JSON: {"status": "active"|"deleted", "reactions": int, "comments": int, "views": int}
    """
    from playwright.sync_api import sync_playwright

    clean_url = re.sub(r"\?commentUrn=.*", "", post_url)

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            page.goto(clean_url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            result = page.evaluate("""() => {
                const res = {status: 'active', reactions: 0, comments: 0, views: 0};

                const bodyText = document.body.innerText.toLowerCase();
                if (bodyText.includes('this content isn') ||
                    bodyText.includes('page not found') ||
                    bodyText.includes('post not found') ||
                    bodyText.includes('this post was removed') ||
                    bodyText.includes('this post wa') ||
                    bodyText.includes('no longer available') ||
                    bodyText.includes('this post has been removed')) {
                    res.status = 'deleted';
                    return res;
                }

                // Reactions
                const reactionBtn = document.querySelector(
                    'button.social-details-social-counts__reactions-count, ' +
                    'span.social-details-social-counts__reactions-count, ' +
                    'button[aria-label*="reaction"], ' +
                    'span[aria-label*="reaction"]'
                );
                if (reactionBtn) {
                    const label = reactionBtn.getAttribute('aria-label') || '';
                    const m = label.match(/([\d,]+)\\s*reaction/i);
                    if (m) res.reactions = parseInt(m[1].replace(/,/g, ''), 10);
                    else {
                        const t = reactionBtn.textContent.trim().replace(/,/g, '');
                        const n = parseInt(t, 10);
                        if (!isNaN(n)) res.reactions = n;
                    }
                }

                // Comments
                const commentBtn = document.querySelector(
                    'button.social-details-social-counts__comments, ' +
                    'button[aria-label*="comment"]'
                );
                if (commentBtn) {
                    const m = commentBtn.textContent.trim().match(/([\d,]+)/);
                    if (m) res.comments = parseInt(m[1].replace(/,/g, ''), 10);
                }

                // Views/impressions
                const viewsEl = document.querySelector(
                    'span.social-details-social-counts__impressions, ' +
                    'span[aria-label*="impression"], ' +
                    'span.analytics-entry-point'
                );
                if (viewsEl) {
                    const m = viewsEl.textContent.trim().match(/([\d,]+)/);
                    if (m) res.views = parseInt(m[1].replace(/,/g, ''), 10);
                }
                if (res.views === 0) {
                    const viewMatch = document.body.innerText.match(/([\d,]+)\\s*impressions?/i);
                    if (viewMatch) res.views = parseInt(viewMatch[1].replace(/,/g, ''), 10);
                }

                return res;
            }""")

            return result

        finally:
            if not is_cdp:
                page.close()
                browser.close()


def stats_batch(posts_json):
    """Process multiple posts for stats in a single browser session.

    Input: JSON array of [{id, url, content_prefix}]
    Returns: JSON array of [{id, url, found, reactions, comment_preview}]
    """
    import json as _json
    from playwright.sync_api import sync_playwright

    posts = _json.loads(posts_json) if isinstance(posts_json, str) else posts_json
    results = []

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            for post in posts:
                post_id = post.get("id")
                url = post.get("url", "")
                content_prefix = post.get("content_prefix", "")
                clean_url = re.sub(r"\?commentUrn=.*", "", url)

                try:
                    page.goto(clean_url, wait_until="domcontentloaded")
                    page.wait_for_timeout(4000)

                    page.evaluate("() => window.scrollBy(0, 600)")
                    page.wait_for_timeout(2000)

                    try:
                        comment_btn = page.locator(
                            'button[aria-label="Comment"]'
                        ).first
                        comment_btn.click(timeout=5000)
                        page.wait_for_timeout(4000)
                    except Exception:
                        pass

                    result = page.evaluate(
                        """({ourName, contentPrefix}) => {
                        const res = {found: false, reactions: 0, comment_preview: ''};

                        // Strategy 1: Find our comment via "View more options" button
                        const optionsBtns = document.querySelectorAll('button[aria-label*="View more options"]');
                        let container = null;
                        for (const btn of optionsBtns) {
                            const label = btn.getAttribute('aria-label') || '';
                            if (label.toLowerCase().includes(ourName.toLowerCase())) {
                                container = btn;
                                for (let i = 0; i < 8; i++) {
                                    container = container.parentElement;
                                    if (!container) break;
                                    const t = container.innerText || '';
                                    if (t.length > 30 && t.length < 3000) {
                                        const r = container.querySelector('button[aria-label*="Reaction"]');
                                        if (r || i >= 3) break;
                                    }
                                }
                                break;
                            }
                        }
                        if (container) {
                            res.found = true;
                            const spans = container.querySelectorAll('span, p');
                            for (const s of spans) {
                                const t = s.innerText.trim();
                                if (t.length > 20 && t.length < 2000 && !t.includes(ourName) && !t.includes('Like')) {
                                    res.comment_preview = t.substring(0, 80);
                                    break;
                                }
                            }
                            const reactionEl = container.querySelector('button[aria-label*="Reaction"], button[aria-label*="reaction"]');
                            if (reactionEl) {
                                const label = reactionEl.getAttribute('aria-label') || '';
                                const m = label.match(/([\d,]+)\\s*[Rr]eaction/);
                                if (m) res.reactions = parseInt(m[1].replace(/,/g, ''), 10);
                            }
                            return res;
                        }

                        // Strategy 2: Content prefix match
                        if (contentPrefix) {
                            const pc = contentPrefix.replace(/[^a-z0-9 ]/gi, '').substring(0, 60).toLowerCase();
                            const bc = document.body.innerText.replace(/[^a-z0-9 ]/gi, '').toLowerCase();
                            if (pc.length > 20 && bc.includes(pc)) {
                                res.found = true;
                                res.comment_preview = '(content match)';
                                return res;
                            }
                        }

                        // Strategy 3: Name fallback
                        if (document.body.innerText.includes(ourName)) {
                            res.found = true;
                            res.comment_preview = '(name scan)';
                        }
                        return res;
                    }""",
                        {
                            "ourName": "Matthew Diakonov",
                            "contentPrefix": content_prefix,
                        },
                    )

                    result["id"] = post_id
                    result["url"] = url
                    results.append(result)

                    # Rate limit between pages
                    page.wait_for_timeout(3000)

                except Exception as e:
                    results.append(
                        {
                            "id": post_id,
                            "url": url,
                            "found": False,
                            "reactions": 0,
                            "error": str(e)[:100],
                        }
                    )

        finally:
            if not is_cdp:
                page.close()
                browser.close()

    return results


def audit_batch(posts_json):
    """Check multiple posts for deleted/active status in a single browser session.

    Input: JSON array of [{id, url}]
    Returns: JSON array of [{id, url, status, reactions, comments, views}]
    """
    import json as _json
    from playwright.sync_api import sync_playwright

    posts = _json.loads(posts_json) if isinstance(posts_json, str) else posts_json
    results = []

    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)

        try:
            for post in posts:
                post_id = post.get("id")
                url = post.get("url", "")
                clean_url = re.sub(r"\?commentUrn=.*", "", url)

                try:
                    page.goto(clean_url, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)

                    result = page.evaluate("""() => {
                        const res = {status: 'active', reactions: 0, comments: 0, views: 0};
                        const bodyText = document.body.innerText.toLowerCase();
                        if (bodyText.includes('this content isn') ||
                            bodyText.includes('page not found') ||
                            bodyText.includes('this post was removed') ||
                            bodyText.includes('no longer available')) {
                            res.status = 'deleted';
                            return res;
                        }
                        const reactionBtn = document.querySelector(
                            'button[aria-label*="reaction"], span[aria-label*="reaction"]'
                        );
                        if (reactionBtn) {
                            const label = reactionBtn.getAttribute('aria-label') || '';
                            const m = label.match(/([\d,]+)\\s*reaction/i);
                            if (m) res.reactions = parseInt(m[1].replace(/,/g, ''), 10);
                        }
                        const commentBtn = document.querySelector(
                            'button[aria-label*="comment"]'
                        );
                        if (commentBtn) {
                            const m = commentBtn.textContent.trim().match(/([\d,]+)/);
                            if (m) res.comments = parseInt(m[1].replace(/,/g, ''), 10);
                        }
                        const viewMatch = document.body.innerText.match(/([\d,]+)\\s*impressions?/i);
                        if (viewMatch) res.views = parseInt(viewMatch[1].replace(/,/g, ''), 10);
                        return res;
                    }""")

                    result["id"] = post_id
                    result["url"] = url
                    results.append(result)

                    page.wait_for_timeout(3000)

                except Exception as e:
                    results.append(
                        {
                            "id": post_id,
                            "url": url,
                            "status": "error",
                            "error": str(e)[:100],
                        }
                    )

        finally:
            if not is_cdp:
                page.close()
                browser.close()

    return results


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "notifications":
        result = discover_notifications()
        print(json.dumps(result, indent=2))

    elif cmd == "comment-context":
        if len(sys.argv) < 3:
            print("Usage: linkedin_browser.py comment-context <url>", file=sys.stderr)
            sys.exit(1)
        result = get_comment_context(sys.argv[2])
        print(json.dumps(result, indent=2))

    elif cmd == "search":
        if len(sys.argv) < 3:
            print("Usage: linkedin_browser.py search <search_url>", file=sys.stderr)
            sys.exit(1)
        result = search_posts(sys.argv[2])
        print(json.dumps(result, indent=2))

    elif cmd == "activity-id":
        if len(sys.argv) < 3:
            print("Usage: linkedin_browser.py activity-id <post_url>", file=sys.stderr)
            sys.exit(1)
        result = extract_activity_id(sys.argv[2])
        print(json.dumps(result, indent=2))

    elif cmd == "stats":
        if len(sys.argv) < 3:
            print(
                "Usage: linkedin_browser.py stats <post_url> [content_prefix]",
                file=sys.stderr,
            )
            sys.exit(1)
        content_prefix = sys.argv[3] if len(sys.argv) > 3 else ""
        result = scrape_stats(sys.argv[2], content_prefix=content_prefix)
        print(json.dumps(result, indent=2))

    elif cmd == "stats-batch":
        if len(sys.argv) < 3:
            print(
                "Usage: linkedin_browser.py stats-batch '<json_array>'",
                file=sys.stderr,
            )
            sys.exit(1)
        result = stats_batch(sys.argv[2])
        print(json.dumps(result, indent=2))

    elif cmd == "audit":
        if len(sys.argv) < 3:
            print("Usage: linkedin_browser.py audit <post_url>", file=sys.stderr)
            sys.exit(1)
        result = audit_post(sys.argv[2])
        print(json.dumps(result, indent=2))

    elif cmd == "audit-batch":
        if len(sys.argv) < 3:
            print(
                "Usage: linkedin_browser.py audit-batch '<json_array>'",
                file=sys.stderr,
            )
            sys.exit(1)
        result = audit_batch(sys.argv[2])
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
