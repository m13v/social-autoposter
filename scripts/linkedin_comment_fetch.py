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
                tried: [], total_comments_on_page: 0 };

  // Strategy 1: data-id attribute match (most reliable when present)
  const all = Array.from(document.querySelectorAll('[data-id]'));
  out.total_comments_on_page = all.filter(e => (e.getAttribute('data-id')||'').includes('urn:li:comment')).length;

  const exact = all.find(e => (e.getAttribute('data-id') || '') === targetUrn);
  if (exact) {
    out.tried.push('data-id-exact');
    const nameEl = exact.querySelector(
      '.comments-comment-meta__description-title, .comments-post-meta__name-text, ' +
      '[class*="comments-post-meta__name"], [class*="comments-comment-meta__description-title"]'
    );
    const bodyEl = exact.querySelector(
      '.comments-comment-item__main-content, [class*="comment-item__main-content"], ' +
      '.update-components-text, [class*="comments-comment-item"] .feed-shared-text'
    );
    if (bodyEl) {
      out.found = true;
      out.author = nameEl ? nameEl.innerText.trim() : null;
      out.content = bodyEl.innerText.trim();
      return out;
    }
  }

  // Strategy 2: any element whose data-id *contains* the urn substring
  const partial = all.find(e => (e.getAttribute('data-id') || '').includes(targetUrn.replace(/^urn:li:comment:/,'')));
  if (partial) {
    out.tried.push('data-id-partial');
    const bodyEl = partial.querySelector(
      '.comments-comment-item__main-content, [class*="comment-item__main-content"], ' +
      '.update-components-text'
    );
    const nameEl = partial.querySelector(
      '.comments-comment-meta__description-title, [class*="comments-post-meta__name"]'
    );
    if (bodyEl) {
      out.found = true;
      out.author = nameEl ? nameEl.innerText.trim() : null;
      out.content = bodyEl.innerText.trim();
      return out;
    }
  }

  // Strategy 3: HTML substring — find the URN in the raw HTML, walk up to
  // the nearest comment container, then extract its text.
  const rawHtml = document.body.innerHTML;
  const idx = rawHtml.indexOf(targetUrn);
  if (idx >= 0) {
    out.tried.push('html-substring');
    // Re-query the DOM for any element referencing the URN via attribute
    const refs = Array.from(document.querySelectorAll('*')).filter(e => {
      const attrs = e.getAttributeNames();
      return attrs.some(a => {
        const v = e.getAttribute(a) || '';
        return v.includes(targetUrn);
      });
    });
    for (const ref of refs) {
      // Walk up to a comment container
      let container = ref;
      for (let i = 0; i < 10 && container; i++) {
        if (container.matches && container.matches(
          'article.comments-comment-entity, [class*="comments-comment-item"]'
        )) break;
        container = container.parentElement;
      }
      if (container) {
        const bodyEl = container.querySelector(
          '.comments-comment-item__main-content, [class*="comment-item__main-content"], ' +
          '.update-components-text'
        );
        const nameEl = container.querySelector(
          '.comments-comment-meta__description-title, [class*="comments-post-meta__name"]'
        );
        if (bodyEl) {
          out.found = true;
          out.author = nameEl ? nameEl.innerText.trim() : null;
          out.content = bodyEl.innerText.trim();
          return out;
        }
      }
    }
  }

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
