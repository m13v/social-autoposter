#!/usr/bin/env python3
"""
Audit text contrast across every website registered in config.json.

Catches the two failure modes that have silently shipped in 2026-04:
  A. Library components whose colors live in CSS custom properties
     (e.g. GradientText reads var(--seo-accent-*)) are used by a consumer
     that never declares the bridge tokens. background-image collapses to
     `none`, text-transparent stays transparent, text is invisible.
  B. A CTA component hardcodes Tailwind classes and concatenates the
     caller's className naively. Two conflicting utilities land in the
     same class list (e.g. bg-white AND bg-teal-500). Whichever rule wins
     in the emitted stylesheet decides the final paint, and the loser
     often lands text/bg on the same color.

Runs per project.website + up to --samples random /t/* URLs from sitemap.
On each page, walks visible text leaves and flags:
  - color: transparent with no background-image gradient (case A).
  - contrast ratio < --min-contrast between foreground and the first
    non-transparent ancestor background (case B).

Exit code: 1 on any fail, 0 otherwise.

Usage:
  python3 scripts/check_contrast.py                     # all sites, 3 /t/ samples each
  python3 scripts/check_contrast.py --only fazm         # one project
  python3 scripts/check_contrast.py --urls URL1 URL2    # ad-hoc pages, no sitemap
  python3 scripts/check_contrast.py --samples 5         # wider sampling
  python3 scripts/check_contrast.py --min-contrast 2.0  # stricter fail threshold

Requires: playwright. If it is not importable, the script prints install
instructions and exits with code 2 without touching any site.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"

# Per-page probe cap. Higher catches more, but slows the run linearly.
DEFAULT_SAMPLES = 3
DEFAULT_MIN_CONTRAST = 1.5
PAGE_TIMEOUT_MS = 20_000
VIEWPORT = {"width": 1280, "height": 900}
USER_AGENT = "check-contrast/1.0 (+https://github.com/m13v/social-autoposter)"

# Page-side analysis. Walks visible text leaves and returns structured
# findings. Kept self-contained so we can inject via page.evaluate().
PAGE_SCRIPT = r"""
(({minContrast}) => {
  // Canvas-based color parser. Handles every CSS color notation the
  // browser understands: rgb(), rgba(), hsl(), lab(), oklch(), named,
  // hex, color(). Returns null for 'transparent' / invalid input.
  const __canvas = document.createElement('canvas');
  __canvas.width = __canvas.height = 1;
  const __ctx = __canvas.getContext('2d', { willReadFrequently: true });
  function parseRgb(s) {
    if (!s) return null;
    try {
      __ctx.fillStyle = '#000000';      // reset so failed parse shows
      __ctx.fillStyle = s;              // browser parses
      __ctx.clearRect(0, 0, 1, 1);
      __ctx.fillRect(0, 0, 1, 1);
      const d = __ctx.getImageData(0, 0, 1, 1).data;
      const a = d[3] / 255;
      if (a < 0.001 && s !== 'rgb(0, 0, 0)' && s !== '#000000') return { r: 0, g: 0, b: 0, a: 0 };
      return { r: d[0], g: d[1], b: d[2], a };
    } catch (e) {
      return null;
    }
  }
  function relLum({ r, g, b }) {
    const f = (v) => {
      v = v / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  }
  function contrast(l1, l2) {
    const [a, b] = l1 > l2 ? [l1, l2] : [l2, l1];
    return (a + 0.05) / (b + 0.05);
  }
  function compositeOver(top, bot) {
    // Source-over compositing: top's alpha controls the mix.
    const a = top.a + bot.a * (1 - top.a);
    if (a < 0.001) return { r: 0, g: 0, b: 0, a: 0 };
    const blend = (ct, cb) => (ct * top.a + cb * bot.a * (1 - top.a)) / a;
    return { r: blend(top.r, bot.r), g: blend(top.g, bot.g), b: blend(top.b, bot.b), a };
  }
  function effectiveBg(el) {
    // Returns { bg, src, image } where image=true means a gradient/image
    // is in the paint stack (skip contrast). Walks up compositing every
    // semi-transparent layer over the next painted ancestor, so a 10%
    // teal tint over a dark body resolves to the actual visible color
    // (mostly dark), not the 10% layer's RGB stripped of alpha.
    let cur = el;
    let acc = { r: 0, g: 0, b: 0, a: 0 };
    let topSrc = null;
    while (cur) {
      const cs = getComputedStyle(cur);
      const img = cs.backgroundImage || 'none';
      if (img !== 'none' && img !== 'initial') return { bg: null, src: cur, image: true };
      const bg = parseRgb(cs.backgroundColor);
      if (bg && bg.a > 0.001) {
        if (!topSrc) topSrc = cur;
        acc = compositeOver(acc, bg);
        if (acc.a > 0.95) return { bg: acc, src: topSrc, image: false };
      }
      cur = cur.parentElement;
    }
    // Reached <html> without an opaque paint; composite remaining stack
    // over the canvas default (white). That is the browser's actual
    // backdrop in the absence of any declared bg.
    const final = compositeOver(acc, { r: 255, g: 255, b: 255, a: 1 });
    return { bg: final, src: topSrc, image: false };
  }
  function isInsideSvg(el) {
    let cur = el;
    while (cur) {
      if (cur.tagName === 'svg' || cur.tagName === 'SVG') return true;
      cur = cur.parentElement;
    }
    return false;
  }
  function isVisible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return false;
    if (parseFloat(cs.opacity) < 0.05) return false;
    return true;
  }
  function snippet(text) {
    const t = (text || '').replace(/\s+/g, ' ').trim();
    return t.length <= 80 ? t : t.slice(0, 77) + '...';
  }
  function describe(el) {
    const cls = (el.getAttribute('class') || '').slice(0, 200);
    const id = el.id ? '#' + el.id : '';
    return el.tagName.toLowerCase() + id + (cls ? '.' + cls.trim().replace(/\s+/g, '.') : '');
  }

  const findings = [];
  const all = document.querySelectorAll('a, button, h1, h2, h3, h4, p, span, li, strong, em, div');
  for (const el of all) {
    if (!isVisible(el)) continue;
    // Text inside an <svg> is painted by SVG <fill>, not CSS background.
    // We can't reliably compute contrast without sampling pixels, and
    // foreignObject HTML inside SVG diagrams produces near-100% false
    // positives. Skip the whole SVG subtree.
    if (isInsideSvg(el)) continue;
    // Only leaf-ish text nodes. If the element has a visible element child
    // that contains the same text, the child will be audited instead.
    const text = el.innerText || '';
    if (!text.trim()) continue;
    if (text.length > 200) continue;
    // Skip if this element contains another element that has the same
    // trimmed text (parent wrapper of the actual text leaf).
    let hasTextyChild = false;
    for (const child of el.children) {
      if (child.innerText && child.innerText.trim() === text.trim() && isVisible(child)) {
        hasTextyChild = true; break;
      }
    }
    if (hasTextyChild) continue;

    const cs = getComputedStyle(el);
    const color = parseRgb(cs.color);
    if (!color) continue;

    // Case A: text is transparent (typical of bg-clip-text) AND no gradient
    // paints it. Element is text-bearing but renders nothing.
    const bgImage = cs.backgroundImage || 'none';
    if (color.a < 0.05) {
      if (bgImage === 'none' || bgImage === 'initial') {
        findings.push({
          kind: 'transparent_no_paint',
          tag: describe(el),
          text: snippet(text),
          color: cs.color,
          backgroundImage: bgImage,
        });
      }
      continue;
    }

    // Case B: contrast ratio against effective background. Skip when
    // the effective paint is an image/gradient (can't compute ratio
    // without sampling pixels) — too many false positives on CTAs with
    // bg-gradient-to-r that are perfectly readable.
    const eff = effectiveBg(el);
    if (eff.image || !eff.bg) continue;
    const ratio = contrast(relLum(color), relLum(eff.bg));
    if (ratio < {minContrast}) {
      findings.push({
        kind: 'low_contrast',
        tag: describe(el),
        text: snippet(text),
        color: cs.color,
        bg: `rgb(${eff.bg.r}, ${eff.bg.g}, ${eff.bg.b})`,
        ratio: Math.round(ratio * 100) / 100,
      });
    }
  }
  return findings;
})({min_contrast});
"""


@dataclass
class PageResult:
    url: str
    ok: bool
    findings: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass
class SiteReport:
    name: str
    website: str
    pages: list[PageResult] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def ok(self) -> bool:
        if self.skipped_reason:
            return True
        return all(p.ok for p in self.pages)


def load_projects() -> list[dict]:
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read config.json: {e}", file=sys.stderr)
        sys.exit(2)
    return cfg.get("projects", []) or []


def sitemap_urls(website: str, want: int, timeout: int = 10) -> list[str]:
    """Pull up to `want` content URLs from /sitemap.xml, preferring /t/*
    slugs (where the CTA-bug pattern tends to live). Falls back to any
    loc entry if no /t/* URLs are present. Silently empty on any error."""
    if not website:
        return []
    base = website.rstrip("/")
    try:
        req = urllib.request.Request(
            base + "/sitemap.xml", headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return []
    locs: list[str] = []
    try:
        root = ET.fromstring(body)
        ns = root.tag.rsplit("}", 1)[0] + "}" if root.tag.startswith("{") else ""
        for loc in root.iter(f"{ns}loc"):
            if loc.text:
                locs.append(loc.text.strip())
    except ET.ParseError:
        # Also handle sitemap-of-sitemaps: grep URLs out of raw bytes.
        locs = re.findall(rb"<loc>([^<]+)</loc>", body)
        locs = [u.decode("utf-8", errors="ignore").strip() for u in locs]
    if not locs:
        return []
    t_urls = [u for u in locs if "/t/" in u]
    pool = t_urls if t_urls else locs
    random.shuffle(pool)
    return pool[:want]


def run_page(page, url: str, min_contrast: float) -> PageResult:
    try:
        page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="networkidle")
    except Exception as e:
        return PageResult(url=url, ok=False, error=f"load failed: {e.__class__.__name__}: {e}")
    try:
        script = PAGE_SCRIPT.replace("{min_contrast}", f"{min_contrast}").replace(
            "{minContrast}", "minContrast"
        )
        findings = page.evaluate(script)
    except Exception as e:
        return PageResult(url=url, ok=False, error=f"evaluate failed: {e.__class__.__name__}: {e}")
    return PageResult(url=url, ok=not findings, findings=findings or [])


def audit_site(pw, project: dict, samples: int, min_contrast: float) -> SiteReport:
    website = (project.get("website") or "").strip()
    name = project.get("name") or "(unnamed)"
    if not website:
        return SiteReport(name=name, website="", skipped_reason="no website in config")
    urls = [website.rstrip("/") + "/"]
    urls.extend(sitemap_urls(website, samples))
    # Deduplicate while preserving order.
    seen, unique = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    report = SiteReport(name=name, website=website)
    browser = pw.chromium.launch(headless=True)
    try:
        ctx = browser.new_context(
            viewport=VIEWPORT, user_agent=USER_AGENT, ignore_https_errors=True
        )
        page = ctx.new_page()
        for url in unique:
            report.pages.append(run_page(page, url, min_contrast))
        ctx.close()
    finally:
        browser.close()
    return report


def format_finding(f: dict) -> str:
    if f.get("kind") == "transparent_no_paint":
        return (
            f"      transparent_no_paint: {f.get('tag','?')}\n"
            f"        text: {f.get('text','')!r}\n"
            f"        color={f.get('color')} backgroundImage={f.get('backgroundImage')}"
        )
    ratio = f.get("ratio", "?")
    return (
        f"      low_contrast (ratio={ratio}): {f.get('tag','?')}\n"
        f"        text: {f.get('text','')!r}\n"
        f"        color={f.get('color')} bg={f.get('bg')}"
    )


def print_report(report: SiteReport) -> None:
    if report.skipped_reason:
        print(f"SKIP {report.name} ({report.skipped_reason})")
        return
    head = "OK  " if report.ok else "FAIL"
    print(f"{head} {report.name}  ({report.website})")
    for p in report.pages:
        if p.error:
            print(f"  ERROR  {p.url} -> {p.error}")
            continue
        mark = "ok" if p.ok else "fail"
        print(f"  [{mark}] {p.url}  findings={len(p.findings)}")
        for f in p.findings[:10]:
            print(format_finding(f))
        if len(p.findings) > 10:
            print(f"      (+{len(p.findings) - 10} more)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--only", metavar="NAME", default=None)
    parser.add_argument("--urls", nargs="+", default=None, help="Audit these URLs directly, skip config.json")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--min-contrast", type=float, default=DEFAULT_MIN_CONTRAST)
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "error: playwright is not installed for this Python interpreter.\n"
            "  Install: pip install playwright && playwright install chromium\n"
            f"  Interpreter: {sys.executable}",
            file=sys.stderr,
        )
        return 2

    reports: list[SiteReport] = []
    with sync_playwright() as pw:
        if args.urls:
            proj = {"name": "ad-hoc", "website": args.urls[0]}
            report = SiteReport(name="ad-hoc", website=args.urls[0])
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    viewport=VIEWPORT, user_agent=USER_AGENT, ignore_https_errors=True
                )
                page = ctx.new_page()
                for url in args.urls:
                    report.pages.append(run_page(page, url, args.min_contrast))
                ctx.close()
            finally:
                browser.close()
            reports.append(report)
        else:
            for proj in load_projects():
                if args.only and proj.get("name") != args.only:
                    continue
                reports.append(audit_site(pw, proj, args.samples, args.min_contrast))

    print()
    failures = 0
    for r in reports:
        print_report(r)
        if not r.ok:
            failures += 1
    print()
    print("=" * 60)
    print(
        f"audited: {len(reports)}   "
        f"failing: {failures}   "
        f"passing: {sum(1 for r in reports if r.ok and not r.skipped_reason)}   "
        f"skipped: {sum(1 for r in reports if r.skipped_reason)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
