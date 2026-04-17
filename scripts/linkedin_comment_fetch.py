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
  // Multiple elements share the same key (LinkedIn double-renders for SSR+hydrate);
  // dedupe by URN and use the first one seen.
  const all = Array.from(document.querySelectorAll('[componentkey^="replaceableComment_urn:li:comment:"]'));
  const seen = new Set();
  const comments = [];

  for (const el of all) {
    const key = el.getAttribute('componentkey') || '';
    const urn = key.replace(/^replaceableComment_/, '');
    if (seen.has(urn)) continue;
    seen.add(urn);

    // Author: find the profile anchor with /in/<slug>/ that has a non-empty
    // text label (LinkedIn renders two: an icon link with empty text, and the
    // named link). The commenter's profile is the one with text.
    const profileAnchors = Array.from(el.querySelectorAll('a[href*="/in/"]'))
      .filter(a => (a.innerText || '').trim().length > 0);
    let profileHref = null, authorText = null;
    if (profileAnchors.length > 0) {
      const a = profileAnchors[0];
      profileHref = a.getAttribute('href') || null;
      // Collapse "Name Premium Profile You Name • You Headline" -> "Name"
      const raw = (a.innerText || '').trim().replace(/\s+/g, ' ');
      authorText = raw.split(/ Premium | • | Profile /)[0].trim();
    }

    // Content: the longest <p>/<span dir=ltr> block that isn't chrome.
    const paraEls = Array.from(el.querySelectorAll('p, span[dir="ltr"]'));
    const chromeRe = /^(Reaction button|Like|Reply|Love|Insightful|Support|Funny|Celebrate|Curious|Author|\d+ impressions?|\d+ reactions?|\d+ reaction|\(edited\).*|\d+[wdhms]|…more|See translation|more)$/i;
    let bodyText = '';
    for (const p of paraEls) {
      const t = (p.innerText || '').trim();
      if (!t || chromeRe.test(t)) continue;
      // Skip the author block (contains the author's name repeated)
      if (authorText && t.startsWith(authorText) && t.length < authorText.length + 40) continue;
      if (t.length > bodyText.length) bodyText = t;
    }
    // Strip trailing "… more" LinkedIn adds on truncated comments
    bodyText = bodyText.replace(/\s*…\s*more\s*$/, '').trim();

    comments.push({
      urn,
      profile_href: profileHref,
      author: authorText,
      content: bodyText || null,
    });
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
