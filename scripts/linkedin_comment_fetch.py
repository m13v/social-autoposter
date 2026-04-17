#!/usr/bin/env python3
"""Fetch the text body of a single LinkedIn comment by URN.

Given a comment permalink URL and its commentUrn, navigates via the existing
linkedin-agent CDP session and extracts only that specific comment's text
(and the comment author name), not the full page dump.

Usage:
    python3 scripts/linkedin_comment_fetch.py \
        "https://www.linkedin.com/feed/update/urn:li:activity:<ACT>?commentUrn=..." \
        "urn:li:comment:(urn:li:activity:<ACT>,<CID>)"
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linkedin_browser as lb


JS_EXTRACTOR = r"""
() => {
  // LinkedIn renders each comment with: componentkey="replaceableComment_<URN>"
  // Each URN appears on multiple elements; dedupe by URN, keep the outermost.
  const all = Array.from(document.querySelectorAll('[componentkey^="replaceableComment_urn:li:comment:"]'));
  const seen = new Set();
  const comments = [];
  const chrome = new Set(['Like','Reply','Love','Insightful','Support','Funny','Celebrate',
                          'Curious','Author','•','·','…more','See translation','more']);
  const agoRe = /^\d+[wdhms]$/i;
  const reactRe = /^(\d+\s)?(Like|Reply|reaction|replies?)$/i;

  for (const el of all) {
    const key = el.getAttribute('componentkey') || '';
    const urn = key.replace(/^replaceableComment_/, '');
    if (seen.has(urn)) continue;
    // Prefer the outermost element for this URN; skip if this is nested inside
    // another componentkey for the same URN.
    const outer = el.closest(`[componentkey="${key.replace(/"/g,'\\"')}"]`);
    if (outer !== el) continue;
    seen.add(urn);

    const textNodes = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    let n;
    while ((n = walker.nextNode())) {
      const t = (n.nodeValue || '').trim();
      if (t) textNodes.push(t);
    }
    const cleaned = textNodes.filter(t => !chrome.has(t) && !agoRe.test(t) && !reactRe.test(t));
    const author = cleaned[0] || null;
    let body = '';
    for (const t of cleaned.slice(1)) if (t.length > body.length) body = t;
    comments.push({ urn, author, content: body || null });
  }
  return { comments, total: comments.length };
}
"""


OUR_NAMES = {"matthew diakonov", "matt diakonov", "m13v"}


def _normalize(name):
    return (name or "").lower().replace("premium profile you", "").replace("you", "").strip(" ·•").strip()


def _match_author(comment_author, target_author):
    """Match comment author to target 'their_author' from notification.

    LinkedIn renders authors as 'First Last Premium Profile You' or just 'First Last';
    notifications may include extra text like ', MBA and 1 other'. Match on
    prefix/substring after normalization.
    """
    c = _normalize(comment_author)
    t = _normalize(target_author)
    if not c or not t:
        return False
    # Trim target to the first author ("Scott Benson, MBA and 1 other" -> "Scott Benson")
    t_first = t.split(" and ")[0].split(",")[0].strip()
    return t_first and (t_first in c or c in t_first)


def fetch_comments(url, settle_ms=5000):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = lb.get_browser_and_page(p)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)
            try:
                page.evaluate("window.scrollBy(0, 1200)")
                page.wait_for_timeout(1500)
                # expand collapsed reply threads when visible
                page.evaluate(r"""() => {
                  const btns = Array.from(document.querySelectorAll('button'));
                  for (const b of btns) if (/show|load|view.*repl/i.test(b.innerText||'')) b.click();
                }""")
                page.wait_for_timeout(2000)
            except Exception:
                pass
            return page.evaluate(JS_EXTRACTOR)
        finally:
            if not is_cdp:
                page.close()
                browser.close()


def pick_reply(comments, target_author):
    """From a list of comments, pick the one authored by target (not us)."""
    # First pass: exact-ish match on target_author, excluding us
    for c in comments:
        author_norm = _normalize(c.get("author", ""))
        if any(n in author_norm for n in OUR_NAMES):
            continue
        if _match_author(c.get("author"), target_author):
            return c
    # Fallback: first non-us comment with any content
    for c in comments:
        author_norm = _normalize(c.get("author", ""))
        if any(n in author_norm for n in OUR_NAMES):
            continue
        if c.get("content"):
            return c
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: linkedin_comment_fetch.py <comment_url> [target_author]", file=sys.stderr)
        sys.exit(2)
    url = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else None
    page_data = fetch_comments(url)
    result = {"url": url, "target_author": target, "all": page_data}
    if target:
        result["picked"] = pick_reply(page_data.get("comments", []), target)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
