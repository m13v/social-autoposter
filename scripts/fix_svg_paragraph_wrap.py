#!/usr/bin/env python3
"""
Repair fazm-website blog _content/*.ts files where the now-retired
mistune-based MDX migration (scripts/migrate_blog_mdx.py) wrapped
inner SVG primitives in <p>...</p> on every blank-line boundary.

Strips <p> and </p> tags from inside <svg>...</svg> blocks, and removes
the dangling </p> mistune emitted right after </svg> (mistune had opened
a paragraph at the last blank line inside the SVG body, then closed it
after </svg>).
"""

from __future__ import annotations

import re
from pathlib import Path

CONTENT_DIR = Path.home() / "fazm-website" / "src" / "app" / "blog" / "_content"

# <svg ...>...</svg>, optionally followed by a stray </p> from mistune.
SVG_BLOCK = re.compile(r"(<svg\b[^>]*>)(.*?)(</svg>)(\s*</p>)?", re.DOTALL)
P_TAG = re.compile(r"</?p\s*>")


def repair(html: str) -> tuple[str, int]:
    n = 0

    def sub(m: re.Match) -> str:
        nonlocal n
        open_tag, body, close_tag, trailing = m.group(1), m.group(2), m.group(3), m.group(4)
        cleaned_body = P_TAG.sub("", body)
        if cleaned_body != body or trailing:
            n += 1
        return f"{open_tag}{cleaned_body}{close_tag}"

    return SVG_BLOCK.sub(sub, html), n


def main() -> None:
    files = sorted(CONTENT_DIR.glob("*.ts"))
    changed = 0
    blocks = 0
    samples: list[str] = []
    for p in files:
        text = p.read_text(encoding="utf-8")
        new, n = repair(text)
        if new != text:
            p.write_text(new, encoding="utf-8")
            changed += 1
            blocks += n
            if len(samples) < 5:
                samples.append(p.name)
    print(f"Patched {changed} files ({blocks} SVG blocks cleaned) of {len(files)} total")
    for s in samples:
        print(f"  e.g. {s}")


if __name__ == "__main__":
    main()
