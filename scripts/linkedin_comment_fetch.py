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


def _we_already_hold_lock():
    """True if the browser lock file is owned by the current process.

    When our caller (e.g. scan_linkedin_notifications.py) already grabbed the
    lock via linkedin_browser.get_browser_and_page and never released it
    (is_cdp=True path doesn't release), re-acquiring would sys.exit(1). This
    check lets us bypass re-acquisition safely within the same process.
    """
    try:
        with open(lb.LOCK_FILE) as f:
            return json.load(f).get("session_id") == lb._LOCK_SESSION_ID
    except (OSError, json.JSONDecodeError):
        return False


def _get_cdp_page(playwright):
    """Connect to the linkedin-agent CDP browser without touching the lock.
    Caller must already hold the lock. Returns (browser, page) or (None, None)."""
    port = lb.find_linkedin_cdp_port()
    if not port:
        return None, None
    try:
        browser = playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
        contexts = browser.contexts
        if not contexts:
            return None, None
        ctx = contexts[0]
        for pg in ctx.pages:
            if "linkedin.com" in pg.url and "login" not in pg.url:
                return browser, pg
        if ctx.pages:
            return browser, ctx.pages[0]
    except Exception:
        pass
    return None, None


def fetch_comments(url, target_urn=None, settle_ms=4000, max_expand_rounds=6):
    """Load page and aggressively expand comments until the target URN appears
    (or we run out of expansion rounds)."""
    from playwright.sync_api import sync_playwright

    expand_js = r"""() => {
      const btns = Array.from(document.querySelectorAll('button, a[role="button"]'));
      const labels = /(show|load|see)\s+(more|all|previous|next|earlier)|view\s+(all|more)\s+(repl|comment)|more\s+comments|\d+\s+more\s+(comment|repl)/i;
      let clicked = 0;
      for (const b of btns) {
        const t = (b.innerText||'').trim();
        if (t && labels.test(t)) { try { b.click(); clicked++; } catch(e){} }
      }
      return clicked;
    }"""

    has_target_js = r"""(targetUrn) => {
      if (!targetUrn) return false;
      const key = 'replaceableComment_' + targetUrn;
      return !!document.querySelector(`[componentkey="${key.replace(/"/g,'\\"')}"]`);
    }"""

    with sync_playwright() as p:
        if _we_already_hold_lock():
            browser, page = _get_cdp_page(p)
            if page is None:
                return {"comments": [], "total": 0, "note": "self-locked, no cdp page"}
            is_cdp = True
        else:
            browser, page, is_cdp = lb.get_browser_and_page(p)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)
            for _ in range(max_expand_rounds):
                try:
                    page.evaluate("window.scrollBy(0, 2000)")
                    page.wait_for_timeout(800)
                    clicked = page.evaluate(expand_js)
                    if clicked:
                        page.wait_for_timeout(1800)
                except Exception:
                    pass
                if target_urn:
                    try:
                        if page.evaluate(has_target_js, target_urn):
                            break
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


def pick_non_us_content(comments, target_author=None, our_href_fragment="/in/m13v/"):
    """Pick the best non-us comment body from a comment list.

    Strategy:
      1. Exclude any comment whose profile_href contains our_href_fragment.
      2. If target_author given, prefer comments whose author matches.
      3. Fall back to the first remaining comment with non-empty content.
    Returns a string or None.
    """
    non_us = [
        c for c in comments
        if our_href_fragment not in (c.get("profile_href") or "").lower()
    ]
    if target_author:
        for c in non_us:
            if _match_author(c.get("author"), target_author) and c.get("content"):
                return c["content"]
    for c in non_us:
        if c.get("content"):
            return c["content"]
    return None


def fetch_live_content(activity_id, comment_urn, target_author=None):
    """End-to-end helper: build the deep-link URL, fetch, and pick best non-us content.

    Returns the comment text string (trimmed to 500 chars) or None on any failure.
    Catches BaseException so that a SystemExit from linkedin_browser's lock
    path (which fires sys.exit(1) on contention) cannot kill the caller.
    """
    import urllib.parse
    try:
        if not activity_id or not comment_urn:
            return None
        encoded = urllib.parse.quote(comment_urn, safe="")
        url = f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}?commentUrn={encoded}"
        page_data = fetch_comments(url, target_urn=comment_urn)
        picked = pick_non_us_content(page_data.get("comments", []), target_author=target_author)
        return (picked or "")[:500] or None
    except BaseException:
        return None


def main():
    if len(sys.argv) < 2:
        print("Usage: linkedin_comment_fetch.py <comment_url> [target_author] [target_urn]", file=sys.stderr)
        sys.exit(2)
    url = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else None
    target_urn = sys.argv[3] if len(sys.argv) > 3 else None
    page_data = fetch_comments(url, target_urn=target_urn)
    result = {"url": url, "target_author": target, "target_urn": target_urn, "all": page_data}
    if target:
        result["picked"] = pick_reply(page_data.get("comments", []), target)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
