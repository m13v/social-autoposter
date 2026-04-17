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
(targetUrn) => {
  const out = { target_urn: targetUrn, found: false, author: null, content: null,
                strategy: null };

  // LinkedIn marks each comment with: componentkey="replaceableComment_<URN>"
  const key = 'replaceableComment_' + targetUrn;
  const escaped = key.replace(/"/g, '\\"');
  const host = document.querySelector(`[componentkey="${escaped}"]`);
  if (!host) return out;

  // Walk down inside the host to find the author line and the body text.
  // LinkedIn nests the content a few levels; grab the deepest non-trivial text
  // block that isn't the author name or a timestamp/"Author" badge.
  const textNodes = [];
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT, null);
  let n;
  while ((n = walker.nextNode())) {
    const t = (n.nodeValue || '').trim();
    if (t) textNodes.push(t);
  }

  // Drop obvious UI chrome
  const chrome = new Set(['Like','Reply','Love','Insightful','Support','Funny','Celebrate',
                          'Curious','Author','•','·','…more','See translation','more']);
  const cleaned = textNodes.filter(t => !chrome.has(t) && !/^\d+[wdhms]$/i.test(t) &&
                                        !/^(\d+\s)?(Like|Reply|reaction|replies?)$/i.test(t));

  // Author is typically the first meaningful block; body is the longest.
  out.author = cleaned[0] || null;
  let body = '';
  for (const t of cleaned.slice(1)) if (t.length > body.length) body = t;
  out.content = body || null;
  out.found = !!body;
  out.strategy = 'componentkey';
  return out;
}
"""


def fetch_comment(url, comment_urn, settle_ms=5000):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, is_cdp = lb.get_browser_and_page(p)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)
            # A deep-linked comment URL usually auto-scrolls; give it a nudge
            try:
                page.evaluate("window.scrollBy(0, 600)")
                page.wait_for_timeout(1200)
            except Exception:
                pass
            return page.evaluate(JS_EXTRACTOR, comment_urn)
        finally:
            if not is_cdp:
                page.close()
                browser.close()


def main():
    if len(sys.argv) < 3:
        print("Usage: linkedin_comment_fetch.py <comment_url> <comment_urn>", file=sys.stderr)
        sys.exit(2)
    url = sys.argv[1]
    urn = sys.argv[2]
    result = fetch_comment(url, urn)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
