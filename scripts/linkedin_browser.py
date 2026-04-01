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
"""

import json
import os
import sys

STORAGE_STATE = os.path.expanduser("~/.claude/browser-sessions.json")
VIEWPORT = {"width": 911, "height": 1016}


def get_browser_context(playwright):
    browser = playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        storage_state=STORAGE_STATE if os.path.exists(STORAGE_STATE) else None,
        viewport=VIEWPORT,
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    return browser, context


def discover_notifications(max_load_more=10):
    """Navigate to LinkedIn notifications, expand, extract actionable items.

    Returns JSON array of notifications:
    [{"type": "reply|mention|comment_on_post", "name": "Author", "url": "...", "activity_id": "..."}]
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, context = get_browser_context(p)
        page = context.new_page()

        try:
            page.goto("https://www.linkedin.com/notifications/", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            # Click "Show more results" repeatedly
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

            # Extract actionable notifications
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

                    // Extract activity ID from URL
                    let activity_id = null;
                    if (url) {
                        const match = url.match(/activity[:%3A]+(\d+)/i);
                        if (match) activity_id = match[1];
                    }

                    actionable.push({ type, name, url, activity_id });
                });
                return actionable;
            }""")

            return notifications

        finally:
            context.close()
            browser.close()


def get_comment_context(comment_url):
    """Navigate to a comment URL and extract the comment's full context.

    Returns JSON:
    {"activity_id": "...", "comments": [{"urn": "...", "author": "...", "content": "..."}]}
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, context = get_browser_context(p)
        page = context.new_page()

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
            context.close()
            browser.close()


def search_posts(search_url, max_posts=10):
    """Browse a LinkedIn search URL and extract posts with activity IDs.

    Returns JSON array:
    [{"activity_id": "...", "author": "...", "preview": "...", "company_url": "..."}]
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, context = get_browser_context(p)
        page = context.new_page()

        try:
            page.goto(search_url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            # Click all "Comment" buttons to expose activity IDs
            comment_buttons = page.locator('button').filter(has_text="Comment").all()
            for i, btn in enumerate(comment_buttons[:max_posts]):
                try:
                    btn.click()
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

            # Also try "Load more" and repeat
            try:
                load_more = page.locator('button:has-text("Load more")').first
                if load_more.is_visible(timeout=2000):
                    load_more.click()
                    page.wait_for_timeout(3000)
            except Exception:
                pass

            # Extract all data
            result = page.evaluate("""() => {
                const html = document.body.innerHTML;

                // Get activity IDs from comment URNs in HTML
                const commentUrnMatches = html.match(/urn:li:comment:\\(urn:li:activity:(\\d+)/g) || [];
                const activityFromComments = commentUrnMatches.map(m => {
                    const match = m.match(/activity:(\\d+)/);
                    return match ? match[1] : null;
                }).filter(Boolean);

                // Also get from direct activity URNs
                const activityMatches = (html.match(/urn:li:activity:(\\d+)/g) || []).map(m => {
                    return m.match(/activity:(\\d+)/)[1];
                });

                const allActivities = [...new Set([...activityFromComments, ...activityMatches])];

                // Get post previews from list items
                const posts = [];
                const listItems = document.querySelectorAll('li');
                listItems.forEach(li => {
                    const text = li.innerText || '';
                    if (text.length < 100) return;
                    if (!text.includes('Like') || !text.includes('Comment')) return;

                    // Find author (company or person name)
                    const authorLink = li.querySelector('a[href*="/company/"], a[href*="/in/"]');
                    const author = authorLink ? authorLink.textContent.trim().split('\\n')[0] : null;
                    const companyUrl = authorLink ? authorLink.getAttribute('href') : null;

                    // Get post text
                    const paragraphs = li.querySelectorAll('p, span');
                    let preview = '';
                    paragraphs.forEach(p => {
                        const t = p.innerText.trim();
                        if (t.length > 50 && t.length < 2000 && !t.includes('Like') && !t.includes('Comment')) {
                            if (preview.length < 300) preview += t + ' ';
                        }
                    });

                    if (author || preview.length > 50) {
                        posts.push({
                            author: author ? author.substring(0, 100) : null,
                            company_url: companyUrl,
                            preview: preview.trim().substring(0, 300),
                        });
                    }
                });

                return {
                    activity_ids: allActivities,
                    posts: posts.slice(0, 15),
                };
            }""")

            return result

        finally:
            context.close()
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
        browser, context = get_browser_context(p)
        page = context.new_page()

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
            context.close()
            browser.close()


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

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
