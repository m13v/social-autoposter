#!/usr/bin/env python3
"""Add dark: variant classes next to hardcoded light classes in @m13v/seo-components.

Strategy:
- For each light Tailwind class (bg-white, text-zinc-900, etc.), insert a paired
  `dark:<equivalent>` right after it, preserving the light class so the package
  still renders correctly on light-mode consumer sites.
- Token boundaries are defined so we never match inside another class
  (e.g. `bg-white/50` or `dark:bg-white` are left alone).
- SitemapSidebar already has `dark:` variants; we bump its dim text one step
  (`dark:text-zinc-500` -> `dark:text-zinc-400`, `dark:text-zinc-400` -> `dark:text-zinc-300`).
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

REPO = Path("/Users/matthewdi/seo-components")
COMPONENTS = REPO / "src" / "components"

# Additive pairs: when <light> appears as a standalone class, become "<light> dark:<dark>".
LIGHT_TO_DARK = [
    # Backgrounds
    ("bg-white", "dark:bg-zinc-900"),
    ("bg-zinc-50", "dark:bg-zinc-900/50"),
    ("bg-zinc-100", "dark:bg-zinc-800/60"),
    ("bg-teal-50", "dark:bg-teal-900/30"),
    ("from-teal-50", "dark:from-teal-900/20"),
    ("bg-orange-50", "dark:bg-orange-900/20"),
    # Text
    ("text-zinc-900", "dark:text-zinc-100"),
    ("text-zinc-800", "dark:text-zinc-200"),
    ("text-zinc-700", "dark:text-zinc-300"),
    ("text-zinc-600", "dark:text-zinc-400"),
    ("text-zinc-500", "dark:text-zinc-400"),  # bump one step for contrast
    ("text-teal-700", "dark:text-teal-300"),
    ("text-teal-600", "dark:text-teal-400"),
    ("text-orange-700", "dark:text-orange-300"),
    # Borders
    ("border-zinc-200", "dark:border-zinc-800"),
    ("border-zinc-100", "dark:border-zinc-900"),
    ("border-zinc-300", "dark:border-zinc-700"),
    ("border-teal-200", "dark:border-teal-800"),
    ("border-teal-300", "dark:border-teal-700"),
    # Divide (for MetricsRow grid lines)
    ("divide-zinc-200", "dark:divide-zinc-800"),
]

# Components that currently have NO dark: variants — safe to additively pair.
ADDITIVE_TARGETS = [
    "InlineTestimonial.tsx",
    "FlowDiagram.tsx",
    "StepTimeline.tsx",
    "BentoGrid.tsx",
    "MetricsRow.tsx",
    "GlowCard.tsx",
    "ComparisonTable.tsx",
    "FaqSection.tsx",
    "SeoPageComments.tsx",
    "ProofBanner.tsx",
    "NewsletterSignup.tsx",
]

def token_bounds(cls: str) -> str:
    """Regex that matches `cls` only when it is a whole Tailwind token."""
    # Disallow neighbors that are word chars, `-`, `/`, or `:` — those would
    # indicate we're inside a larger class name like `bg-white/50` or
    # `dark:bg-white` or `bg-white-foo`.
    escaped = re.escape(cls)
    return rf"(?<![\w/:\-]){escaped}(?![\w/:\-])"

def add_dark_variants(src: str) -> tuple[str, dict]:
    counts: dict[str, int] = {}
    for light, dark in LIGHT_TO_DARK:
        # Only replace occurrences that are NOT already followed by the dark variant
        # (avoids double-insertion if re-run).
        pattern = token_bounds(light) + rf"(?!\s+{re.escape(dark)}\b)"
        n = 0
        def repl(_m):
            nonlocal n
            n += 1
            return f"{light} {dark}"
        src = re.sub(pattern, repl, src)
        if n:
            counts[light] = n
    return src, counts

def bump_sidebar_contrast(src: str) -> tuple[str, dict]:
    """SitemapSidebar already has dark: variants. Bump text-zinc-500 -> 400 and 400 -> 300."""
    # Use placeholders to avoid double-bumping
    src = re.sub(token_bounds("dark:text-zinc-500"), "dark:text-zinc-__BUMP500__", src)
    src = re.sub(token_bounds("dark:text-zinc-400"), "dark:text-zinc-__BUMP400__", src)
    n500 = src.count("dark:text-zinc-__BUMP500__")
    n400 = src.count("dark:text-zinc-__BUMP400__")
    src = src.replace("dark:text-zinc-__BUMP500__", "dark:text-zinc-400")
    src = src.replace("dark:text-zinc-__BUMP400__", "dark:text-zinc-300")
    return src, {"dark:text-zinc-500 -> dark:text-zinc-400": n500,
                 "dark:text-zinc-400 -> dark:text-zinc-300": n400}

def main():
    grand_total = 0
    for name in ADDITIVE_TARGETS:
        path = COMPONENTS / name
        src = path.read_text()
        new, counts = add_dark_variants(src)
        if new != src:
            path.write_text(new)
        inserted = sum(counts.values())
        grand_total += inserted
        summary = ", ".join(f"{k}={v}" for k, v in counts.items()) or "(nothing)"
        print(f"{name}: +{inserted} dark: pairs  [{summary}]")

    # Sidebar contrast bump
    sidebar = COMPONENTS / "SitemapSidebar.tsx"
    src = sidebar.read_text()
    new, counts = bump_sidebar_contrast(src)
    if new != src:
        sidebar.write_text(new)
    total_sb = sum(counts.values())
    grand_total += total_sb
    print(f"SitemapSidebar.tsx: +{total_sb} contrast bumps  [{counts}]")

    print(f"\nTOTAL edits: {grand_total}")

if __name__ == "__main__":
    main()
