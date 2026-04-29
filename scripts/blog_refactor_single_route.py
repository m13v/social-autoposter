#!/usr/bin/env python3
"""
Refactor fazm-website blog from 1500 individual page.tsx files into:
- ONE dynamic route: src/app/blog/[slug]/page.tsx (uses generateStaticParams)
- Per-slug content modules: src/app/blog/_content/<slug>.ts (exports default HTML)

Why: 1500 separate route files made webpack compile 1500 chunks, hitting
Vercel's 45-min build timeout. A single dynamic route compiles in seconds
and still pre-renders every post statically via generateStaticParams.

Reads page.tsx from git HEAD (since the working tree may have been partially
mutated by an earlier failed run) and uses an explicit end-marker scan to
extract HTML_CONTENT, instead of a regex that can be tripped by embedded
escaped backticks inside code samples.

Run from anywhere: python3 scripts/blog_refactor_single_route.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

FAZM = Path.home() / "fazm-website"
BLOG_DIR = FAZM / "src" / "app" / "blog"
CONTENT_DIR = BLOG_DIR / "_content"

# Sentinel that always appears immediately after the HTML_CONTENT template
# literal in pages produced by scripts/migrate_blog_mdx.py. We anchor on this
# rather than on `;\n at end-of-line because the latter is ambiguous when
# the HTML body contains escaped backticks inside code samples (which the
# migration script wrote as \\`...\\`;\\n inside <pre>/<code> blocks).
END_MARKER = "\nexport default function Page() {"

# const NAME = "..."; (double-quoted, with backslash-escaped quotes)
def _grab(text: str, name: str) -> str | None:
    m = re.search(
        rf'^const {re.escape(name)} = "((?:[^"\\]|\\.)*)";',
        text, re.MULTILINE)
    if m:
        return m.group(1)
    if re.search(rf'^const {re.escape(name)} = undefined;', text, re.MULTILINE):
        return None
    return None


def extract_metadata(text: str) -> dict | None:
    slug = _grab(text, "SLUG")
    title = _grab(text, "TITLE")
    description = _grab(text, "DESCRIPTION")
    date = _grab(text, "DATE")
    last_modified = _grab(text, "LAST_MODIFIED")
    author = _grab(text, "AUTHOR")
    image = _grab(text, "IMAGE")
    if not (slug and title and date):
        return None

    tags: list[str] = []
    tm = re.search(
        r'^const TAGS(?::\s*string\[\])?\s*=\s*\[([^\]]*)\]\s*;',
        text, re.MULTILINE)
    if tm:
        for s in re.finditer(r'"((?:[^"\\]|\\.)*)"', tm.group(1)):
            tags.append(s.group(1))

    # Robust HTML_CONTENT extraction: find the literal opening token and the
    # known sentinel that always follows the closing backtick+semicolon.
    open_tok = "const HTML_CONTENT = `"
    start = text.find(open_tok)
    if start < 0:
        return None
    body_start = start + len(open_tok)
    end_pos = text.find(END_MARKER, body_start)
    if end_pos < 0:
        return None
    # The bytes immediately before END_MARKER are `;\n  → drop those 3 chars
    # (or `;\r\n on Windows checkouts; tolerate both).
    bytes_before = text[end_pos - 3:end_pos]
    if bytes_before == "`;\n":
        html = text[body_start:end_pos - 3]
    elif text[end_pos - 4:end_pos] == "`;\r\n":
        html = text[body_start:end_pos - 4]
    else:
        # Unexpected layout. Fall back to scanning backwards for `;
        idx = text.rfind("`;", body_start, end_pos)
        if idx < 0:
            return None
        html = text[body_start:idx]

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
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    out = CONTENT_DIR / f"{meta['slug']}.ts"
    out.write_text(
        f"// Auto-generated from blog_refactor_single_route.py\n"
        f"const HTML_CONTENT = `{meta['html']}`;\n"
        f"export default HTML_CONTENT;\n",
        encoding="utf-8")
    return out


def list_slugs_in_git_head() -> list[str]:
    """Return every slug that has a tracked src/app/blog/<slug>/page.tsx in HEAD."""
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD",
         "src/app/blog/"],
        cwd=FAZM, capture_output=True, text=True, check=True,
    ).stdout
    slugs: list[str] = []
    for line in out.splitlines():
        m = re.match(r"src/app/blog/([^/]+)/page\.tsx$", line)
        if m and m.group(1) not in ("[slug]", "_content", "tag"):
            slugs.append(m.group(1))
    return slugs


def read_page_from_git(slug: str) -> str | None:
    """git show HEAD:<path> for the per-slug page.tsx."""
    rel = f"src/app/blog/{slug}/page.tsx"
    res = subprocess.run(
        ["git", "show", f"HEAD:{rel}"],
        cwd=FAZM, capture_output=True, text=True,
    )
    if res.returncode != 0:
        return None
    return res.stdout


def main():
    if not BLOG_DIR.exists():
        raise SystemExit(f"missing {BLOG_DIR}")

    # Wipe any partial content modules from a previous run so we never leave
    # truncated files behind.
    if CONTENT_DIR.exists():
        shutil.rmtree(CONTENT_DIR)

    slugs = list_slugs_in_git_head()
    print(f"Found {len(slugs)} slugs in git HEAD")

    written = 0
    failures: list[str] = []
    for slug in slugs:
        text = read_page_from_git(slug)
        if not text:
            failures.append(f"git show: {slug}")
            continue
        meta = extract_metadata(text)
        if not meta:
            failures.append(f"parse: {slug}")
            continue
        if meta["slug"] != slug:
            failures.append(f"slug mismatch: dir={slug} parsed={meta['slug']}")
            continue
        write_content_module(meta)
        written += 1

    print(f"Wrote {written}/{len(slugs)} content modules to {CONTENT_DIR}")
    if failures:
        print(f"Failures ({len(failures)}):")
        for f in failures[:20]:
            print(f"  {f}")

    # Now also remove any tracked per-slug page.tsx files in the working tree
    # (they were already deleted by an earlier run, but make this idempotent).
    removed = 0
    for slug in slugs:
        d = BLOG_DIR / slug
        p = d / "page.tsx"
        if p.exists():
            p.unlink()
        if d.exists():
            try:
                d.rmdir()
            except OSError:
                pass
            removed += 1
    print(f"Cleaned {removed} per-slug dirs (deleted page.tsx, removed dir if empty)")


if __name__ == "__main__":
    main()
