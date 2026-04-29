#!/usr/bin/env python3
"""
Refactor fazm-website blog from 1500 individual page.tsx files into:
- ONE dynamic route: src/app/blog/[slug]/page.tsx (uses generateStaticParams)
- Per-slug content modules: src/app/blog/_content/<slug>.ts (exports default HTML)

Why: 1500 separate route files made webpack compile 1500 chunks, hitting
Vercel's 45-min build timeout. A single dynamic route compiles in seconds
and still pre-renders every post statically via generateStaticParams.

Run from anywhere: python3 scripts/blog_refactor_single_route.py
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

FAZM = Path.home() / "fazm-website"
BLOG_DIR = FAZM / "src" / "app" / "blog"
CONTENT_DIR = BLOG_DIR / "_content"

# JS template-literal escape: backslashes already-doubled in input come back
# as themselves; we just write the HTML string into a TS template literal.
def extract_metadata(text: str) -> dict | None:
    """Pull SLUG/TITLE/DESCRIPTION/DATE/LAST_MODIFIED/AUTHOR/TAGS/IMAGE/HTML_CONTENT."""
    def grab(name: str) -> str | None:
        # const NAME = "..."; (double-quoted)
        m = re.search(
            rf'^const {re.escape(name)} = "((?:[^"\\]|\\.)*)";',
            text, re.MULTILINE)
        if m:
            return m.group(1)
        # const NAME = undefined;
        if re.search(rf'^const {re.escape(name)} = undefined;', text,
                     re.MULTILINE):
            return None
        return None

    slug = grab("SLUG")
    title = grab("TITLE")
    description = grab("DESCRIPTION")
    date = grab("DATE")
    last_modified = grab("LAST_MODIFIED")
    author = grab("AUTHOR")
    image = grab("IMAGE")
    if not (slug and title and date):
        return None

    tags: list[str] = []
    tm = re.search(
        r'^const TAGS(?::\s*string\[\])?\s*=\s*\[([^\]]*)\]\s*;',
        text, re.MULTILINE)
    if tm:
        for s in re.finditer(r'"((?:[^"\\]|\\.)*)"', tm.group(1)):
            tags.append(s.group(1))

    cm = re.search(r'^const HTML_CONTENT = `(.*?)`;\s*$',
                   text, re.MULTILINE | re.DOTALL)
    html = cm.group(1) if cm else ""

    return {
        "slug": slug,
        "title": title,
        "description": description or "",
        "date": date,
        "lastModified": last_modified,
        "author": author or "Matthew Diakonov",
        "tags": tags,
        "image": image,
        "html": html,
    }


def write_content_module(meta: dict) -> Path:
    """Write src/app/blog/_content/<slug>.ts exporting default HTML."""
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    out = CONTENT_DIR / f"{meta['slug']}.ts"
    # The HTML_CONTENT in page.tsx already has backticks/${} escaped (from the
    # migration script). We just wrap it back in a template literal.
    out.write_text(
        f"// Auto-generated from blog_refactor_single_route.py\n"
        f"const HTML_CONTENT = `{meta['html']}`;\n"
        f"export default HTML_CONTENT;\n",
        encoding="utf-8")
    return out


def main():
    if not BLOG_DIR.exists():
        raise SystemExit(f"missing {BLOG_DIR}")

    page_files = list(BLOG_DIR.glob("*/page.tsx"))
    print(f"Found {len(page_files)} per-slug page.tsx files")

    extracted: list[dict] = []
    for p in page_files:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  read fail {p}: {e}")
            continue
        meta = extract_metadata(text)
        if not meta:
            print(f"  parse fail {p}")
            continue
        write_content_module(meta)
        extracted.append(meta)

    print(f"Wrote {len(extracted)} content modules to {CONTENT_DIR}")

    # Delete the per-slug page.tsx files and their now-empty dirs.
    removed = 0
    for p in page_files:
        try:
            p.unlink()
            # Remove dir if empty
            d = p.parent
            try:
                d.rmdir()
            except OSError:
                pass
            removed += 1
        except Exception as e:
            print(f"  delete fail {p}: {e}")
    print(f"Deleted {removed} per-slug page.tsx files")


if __name__ == "__main__":
    main()
