#!/usr/bin/env python3
"""Convert the 25 fazm /t/* guide pages from light theme to dark.

Hardcoded light-theme Tailwind classes are swapped for dark equivalents.
Writes files in place. Prints summary.
"""
import re
import sys
from pathlib import Path

REPO = Path("/Users/matthewdi/fazm-website")

# Order matters: longer/specific patterns first to avoid partial-match clashes.
REPLACEMENTS = [
    # Article wrapper (the top-level light surface)
    ('<article className="bg-white min-h-screen">', '<article className="min-h-screen">'),

    # Teal accents with opacity suffix first
    ("bg-teal-50/30", "bg-teal-900/20"),

    # Backgrounds
    ("bg-white", "bg-transparent"),
    ("bg-zinc-50", "bg-zinc-900/50"),
    ("bg-zinc-100", "bg-zinc-800/60"),
    ("bg-teal-50", "bg-teal-900/30"),
    ("bg-orange-50", "bg-orange-900/20"),

    # Text colors
    ("text-zinc-900", "text-zinc-100"),
    ("text-zinc-800", "text-zinc-200"),
    ("text-zinc-700", "text-zinc-300"),
    ("text-zinc-600", "text-zinc-400"),
    ("text-teal-700", "text-teal-300"),
    ("text-teal-600", "text-teal-400"),
    ("text-orange-700", "text-orange-300"),
    ("text-emerald-600", "text-emerald-400"),

    # Borders
    ("border-zinc-200", "border-zinc-800"),
    ("border-zinc-100", "border-zinc-900"),
    ("border-zinc-300", "border-zinc-700"),
    ("border-teal-200", "border-teal-800"),
]

def convert(path: Path) -> dict:
    src = path.read_text()
    original = src
    counts = {}
    for old, new in REPLACEMENTS:
        if old == new:
            continue
        c = src.count(old)
        if c:
            src = src.replace(old, new)
            counts[old] = c
    changed = src != original
    if changed:
        path.write_text(src)
    return {"path": str(path.relative_to(REPO)), "changed": changed, "counts": counts}


def main():
    pages = sorted(REPO.glob("src/app/t/*/page.tsx"))
    targets = [p for p in pages if "bg-white min-h-screen" in p.read_text()]
    print(f"Found {len(targets)} target pages.", file=sys.stderr)
    total = 0
    for p in targets:
        r = convert(p)
        summary = ", ".join(f"{k}={v}" for k, v in r["counts"].items()) or "(no hits)"
        print(f"  {r['path']}: {summary}")
        total += sum(r["counts"].values())
    print(f"\nTotal class replacements: {total}")


if __name__ == "__main__":
    main()
