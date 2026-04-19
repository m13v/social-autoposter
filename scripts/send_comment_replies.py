#!/usr/bin/env python3
"""Post Reddit comment replies by finding target author's reply to our comment
within a thread, then invoking the existing reply_to_comment helper.

Usage: send_comment_replies.py  (reads a list of (thread_url, target_author, text) from stdin JSON)
"""
import json
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reddit_browser as rb  # type: ignore


def find_reply_permalink(page, thread_url, target_author, our_username, target_text=""):
    """In a thread, find a comment by target_author that we haven't replied to yet."""
    from urllib.parse import urlsplit
    old_url = rb._to_old_reddit(thread_url)
    page.goto(old_url, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    rb._ensure_old_reddit(page)
    args_target_text = target_text

    script = """
    (args) => {
        const target = args.target;
        const ours = args.ours;
        const targetText = (args.targetText || '').trim().substring(0, 80).toLowerCase();
        const comments = document.querySelectorAll('.comment');
        const candidates = [];
        for (const c of comments) {
            const a = c.querySelector('a.author');
            if (!a || a.textContent.trim() !== target) continue;
            // skip comments that already have our direct child reply
            let alreadyReplied = false;
            const childComments = c.querySelectorAll(':scope > .child .comment');
            for (const cc of childComments) {
                const parentOfCc = cc.parentElement && cc.parentElement.closest('.comment');
                if (parentOfCc !== c) continue;
                const ca = cc.querySelector('a.author');
                if (ca && ca.textContent.trim() === ours) {
                    alreadyReplied = true;
                    break;
                }
            }
            const link = c.querySelector('a.bylink');
            const body = (c.querySelector('.usertext-body') || {}).textContent || '';
            candidates.push({
                href: link ? link.getAttribute('href') : null,
                alreadyReplied: alreadyReplied,
                bodyLower: body.toLowerCase(),
            });
        }
        // Prefer: not-already-replied AND body contains target text
        if (targetText) {
            for (const c of candidates) {
                if (!c.alreadyReplied && c.bodyLower.includes(targetText)) {
                    return c.href;
                }
            }
        }
        // Next: any not-already-replied (prefer last)
        for (let i = candidates.length - 1; i >= 0; i--) {
            if (!candidates[i].alreadyReplied) return candidates[i].href;
        }
        // Fallback: last match
        if (candidates.length > 0) return candidates[candidates.length - 1].href;
        return null;
    }
    """
    href = page.evaluate(script, {"target": target_author, "ours": our_username, "targetText": args_target_text})
    if href:
        if href.startswith("http"):
            return href
        return "https://old.reddit.com" + href
    return None


def main():
    items = json.load(sys.stdin)
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        browser, page, is_cdp = rb.get_browser_and_page(p)
        try:
            for item in items:
                thread_url = item["thread_url"]
                author = item["author"]
                text = item["text"]
                target_text = item.get("target_text", "") or ""
                try:
                    permalink = find_reply_permalink(page, thread_url, author, rb.OUR_USERNAME, target_text)
                except Exception as e:
                    results.append({"author": author, "ok": False, "error": f"find_failed: {e}"})
                    continue
                if not permalink:
                    results.append({"author": author, "ok": False, "error": "no_reply_permalink"})
                    continue
                results.append({"author": author, "permalink": permalink})
        finally:
            page.context.close()
            if not is_cdp:
                browser.close()

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
