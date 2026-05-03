#!/usr/bin/env python3
"""
Add light-mode contrast to fazm-website MDX blog posts.

The original MDX files were authored for dark mode and use hardcoded
`text-white`, `text-slate-300`, `text-slate-400`, etc. directly in custom
JSX className attributes (callouts, cards, CTAs). Now that the site
defaults to light mode, those tokens render as illegible washed-out gray
or pure white on white.

This pass converts each dark-only token into a `light-default dark:original`
pair so light mode shows high-contrast slate-900/slate-800/slate-700 text
while dark mode preserves the original look.

It only matches standalone tokens, not prefixed variants like
`hover:text-white`, `dark:text-white`, or arbitrary variants like
`[&>code]:text-slate-300`.
"""

from __future__ import annotations

import re
from pathlib import Path

CONTENT_DIR = Path.home() / "fazm-website" / "content" / "blog"

MAPPINGS: list[tuple[str, str]] = [
    ("text-white", "text-slate-900 dark:text-white"),
    ("text-slate-100", "text-slate-900 dark:text-slate-100"),
    ("text-slate-200", "text-slate-800 dark:text-slate-200"),
    ("text-slate-300", "text-slate-700 dark:text-slate-300"),
    ("text-slate-400", "text-slate-600 dark:text-slate-400"),
    ("text-muted", "text-slate-600 dark:text-slate-400"),
]


def patch(text: str) -> tuple[str, int]:
    n = 0
    for old, new in MAPPINGS:
        # Match the class as a standalone token: not preceded by `:` or `-`
        # or word, not followed by word or `-`. Allow optional `/NN` opacity
        # suffix (carry through into the dark: side).
        pattern = re.compile(
            rf"(?<![:\w-]){re.escape(old)}(/\d+)?(?![\w-])"
        )

        def repl(m: re.Match) -> str:
            nonlocal n
            n += 1
            opacity = m.group(1) or ""
            light_token, _, dark_token = new.partition(" dark:")
            return f"{light_token}{opacity} dark:{dark_token}{opacity}"

        text = pattern.sub(repl, text)
    return text, n


def main() -> None:
    files = sorted(CONTENT_DIR.glob("*.mdx"))
    changed = 0
    total = 0
    for p in files:
        original = p.read_text(encoding="utf-8")
        new, n = patch(original)
        if new != original:
            p.write_text(new, encoding="utf-8")
            changed += 1
            total += n
    print(f"Patched {changed} MDX files ({total} class tokens) of {len(files)} total")


if __name__ == "__main__":
    main()
