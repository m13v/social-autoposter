"""Rewrite dark-only Tailwind classes in fazm-website to theme-aware pairs.

For each occurrence of a dark-only class (e.g. `bg-zinc-900`, `text-white`) that
is NOT already paired with a `dark:` variant, emit:
    {prefixes}{light_replacement}  dark:{prefixes}{original}

Operates on raw file text. The match is anchored so it only fires on tokens that
look like Tailwind classes (preceded/followed by class-string boundaries).

Run:
    python3 scripts/fazm_theme_flip/rewrite_classes.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path("/Users/matthewdi/fazm-website")
SRC = REPO / "src"


# Map: dark-only base class -> light-mode replacement.
# Alpha modifiers (e.g. `text-white/30`) are handled by a special expansion below.
LIGHT_PAIRS: dict[str, str] = {
    # Solid backgrounds (page/section)
    "bg-zinc-950": "bg-white",
    "bg-zinc-900": "bg-white",
    "bg-zinc-800": "bg-zinc-100",
    "bg-zinc-700": "bg-zinc-200",
    "bg-slate-950": "bg-white",
    "bg-slate-900": "bg-white",
    "bg-slate-800": "bg-slate-100",
    "bg-slate-700": "bg-slate-200",
    "bg-neutral-950": "bg-white",
    "bg-neutral-900": "bg-white",
    "bg-neutral-800": "bg-neutral-100",
    "bg-neutral-700": "bg-neutral-200",
    "bg-gray-950": "bg-white",
    "bg-gray-900": "bg-white",
    "bg-gray-800": "bg-gray-100",
    "bg-gray-700": "bg-gray-200",
    "bg-black": "bg-white",

    # Text colors (light text on dark -> dark text on light)
    "text-white": "text-zinc-900",
    "text-zinc-50": "text-zinc-900",
    "text-zinc-100": "text-zinc-900",
    "text-zinc-200": "text-zinc-800",
    "text-zinc-300": "text-zinc-700",
    "text-zinc-400": "text-zinc-600",
    "text-slate-50": "text-slate-900",
    "text-slate-100": "text-slate-900",
    "text-slate-200": "text-slate-800",
    "text-slate-300": "text-slate-700",
    "text-slate-400": "text-slate-600",
    "text-neutral-50": "text-neutral-900",
    "text-neutral-100": "text-neutral-900",
    "text-neutral-200": "text-neutral-800",
    "text-neutral-300": "text-neutral-700",
    "text-neutral-400": "text-neutral-600",
    "text-gray-50": "text-gray-900",
    "text-gray-100": "text-gray-900",
    "text-gray-200": "text-gray-800",
    "text-gray-300": "text-gray-700",
    "text-gray-400": "text-gray-600",
    "text-stone-100": "text-stone-900",
    "text-stone-200": "text-stone-800",
    "text-stone-300": "text-stone-700",
    "text-stone-400": "text-stone-600",

    # Borders (dark-only neutrals)
    "border-zinc-800": "border-zinc-200",
    "border-zinc-700": "border-zinc-300",
    "border-zinc-900": "border-zinc-200",
    "border-slate-800": "border-slate-200",
    "border-slate-700": "border-slate-300",
    "border-neutral-800": "border-neutral-200",
    "border-neutral-700": "border-neutral-300",
    "border-gray-800": "border-gray-200",
    "border-gray-700": "border-gray-300",

    # Rings (dark-only neutrals)
    "ring-zinc-800": "ring-zinc-200",
    "ring-zinc-700": "ring-zinc-300",
    "ring-slate-800": "ring-slate-200",
    "ring-neutral-800": "ring-neutral-200",
    "ring-gray-800": "ring-gray-200",

    # Divide (dark-only neutrals)
    "divide-zinc-800": "divide-zinc-200",
    "divide-slate-800": "divide-slate-200",
    "divide-neutral-800": "divide-neutral-200",
    "divide-gray-800": "divide-gray-200",

    # Gradient stops - only the obviously-dark direction
    "from-zinc-950": "from-zinc-50",
    "from-zinc-900": "from-zinc-50",
    "from-zinc-800": "from-zinc-100",
    "from-slate-950": "from-slate-50",
    "from-slate-900": "from-slate-50",
    "from-neutral-950": "from-neutral-50",
    "from-neutral-900": "from-neutral-50",
    "from-gray-950": "from-gray-50",
    "from-gray-900": "from-gray-50",
    "from-black": "from-white",
    "to-zinc-950": "to-zinc-50",
    "to-zinc-900": "to-zinc-50",
    "to-zinc-800": "to-zinc-100",
    "to-slate-950": "to-slate-50",
    "to-slate-900": "to-slate-50",
    "to-neutral-950": "to-neutral-50",
    "to-neutral-900": "to-neutral-50",
    "to-gray-950": "to-gray-50",
    "to-gray-900": "to-gray-50",
    "to-black": "to-white",
    "via-zinc-900": "via-zinc-100",
    "via-zinc-800": "via-zinc-200",
    "via-slate-900": "via-slate-100",
    "via-neutral-900": "via-neutral-100",
    "via-gray-900": "via-gray-100",
    "via-black": "via-white",
}


# Bases that take an alpha modifier (`/30`, `/[0.05]`, etc.).
# When matched, the generated light replacement uses the same modifier on a
# darkened base so the visual weight is preserved (white/10 -> black/10).
ALPHA_FLIPS: dict[str, str] = {
    # Only `-white` direction. Translucent white = subtle highlight on dark bg;
    # on a light page it's invisible, so flip to `-black` (subtle shadow).
    # Translucent black is already light-friendly, leave alone.
    "bg-white": "bg-black",
    "border-white": "border-black",
    "text-white": "text-black",
    "ring-white": "ring-black",
    "divide-white": "divide-black",
    "outline-white": "outline-black",
    "shadow-white": "shadow-black",
    "from-white": "from-black",
    "to-white": "to-black",
    "via-white": "via-black",
}


# Char that may legally appear inside a Tailwind class token (after the prefixes).
# We use this to anchor the regex so we don't catch a substring like
# `bg-zinc-900` inside `bg-zinc-9000` (none such, but defensively).
TOKEN_BOUNDARY_BEFORE = r'(?<![\w/\.\[\]-])'
TOKEN_BOUNDARY_AFTER = r'(?![\w/\.\[\]-])'

# Variant prefix chain: zero or more `name:` segments.
# A variant name can contain letters, digits, hyphens, and brackets (for
# arbitrary variants like `data-[state=open]:`). We do not capture `dark:`
# as part of "name" because we treat it as a sentinel.
VARIANT_CHAIN = r'((?:(?:[a-z][\w-]*|\[[^\]]+\]|[a-z][\w-]*-\[[^\]]+\]):)*?)'


def is_dark_paired(prefixes: str) -> bool:
    """Return True if the prefix chain already includes a `dark:` variant."""
    return bool(re.search(r'(?:^|:)dark:', f':{prefixes}'))


def make_solid_pattern(base: str) -> re.Pattern:
    """Match optional variant chain + base class, with no alpha modifier."""
    return re.compile(
        TOKEN_BOUNDARY_BEFORE
        + VARIANT_CHAIN
        + re.escape(base)
        + TOKEN_BOUNDARY_AFTER
    )


def make_alpha_pattern(base: str) -> re.Pattern:
    """Match optional variant chain + base class + REQUIRED alpha modifier.

    Alpha modifier: `/N` where N is a number, OR `/[arbitrary]`.
    """
    return re.compile(
        TOKEN_BOUNDARY_BEFORE
        + VARIANT_CHAIN
        + re.escape(base)
        + r'(/(?:\d+(?:\.\d+)?|\[[^\]]+\]))'
        + TOKEN_BOUNDARY_AFTER
    )


def rewrite_text(text: str) -> tuple[str, int]:
    """Apply all rewrites to a file's content. Return (new_text, change_count)."""
    changes = 0

    # 1) Alpha-flips first (longer, more specific): bg-white/5 etc.
    #    Order matters: alpha pattern requires a `/...` so it wouldn't match the
    #    bare `bg-white` token, but doing alpha first avoids accidentally splitting
    #    an alpha-class via a solid-base pattern that doesn't exist for the base.
    for base, flipped in ALPHA_FLIPS.items():
        pattern = make_alpha_pattern(base)

        def repl(m: re.Match) -> str:
            nonlocal changes
            prefixes = m.group(1) or ""
            alpha = m.group(2)
            if is_dark_paired(prefixes):
                return m.group(0)
            light_token = f'{prefixes}{flipped}{alpha}'
            dark_token = f'dark:{prefixes}{base}{alpha}'
            changes += 1
            return f'{light_token} {dark_token}'

        text = pattern.sub(repl, text)

    # 2) Solid bases (no alpha): bg-zinc-900, text-white, etc.
    for base, light in LIGHT_PAIRS.items():
        pattern = make_solid_pattern(base)

        def repl(m: re.Match) -> str:
            nonlocal changes
            prefixes = m.group(1) or ""
            if is_dark_paired(prefixes):
                return m.group(0)
            light_token = f'{prefixes}{light}'
            dark_token = f'dark:{prefixes}{base}'
            changes += 1
            return f'{light_token} {dark_token}'

        text = pattern.sub(repl, text)

    return text, changes


def walk_files(root: Path):
    for p in root.rglob("*.tsx"):
        if "node_modules" in p.parts:
            continue
        yield p
    for p in root.rglob("*.ts"):
        if "node_modules" in p.parts:
            continue
        # Skip declaration / config-style files; rewrite only files that look
        # like they may contain JSX classNames (most .ts files won't, but
        # err on the side of inclusion for module-level constants).
        yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", default=str(SRC))
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"not a dir: {root}", file=sys.stderr)
        return 2

    total_files = 0
    changed_files = 0
    total_changes = 0

    for path in walk_files(root):
        total_files += 1
        original = path.read_text(encoding="utf-8")
        rewritten, n = rewrite_text(original)
        if n == 0 or rewritten == original:
            continue
        changed_files += 1
        total_changes += n
        if args.dry_run:
            print(f"DRY {path.relative_to(root)}: {n} changes")
        else:
            path.write_text(rewritten, encoding="utf-8")
            print(f"OK  {path.relative_to(root)}: {n} changes")

    print()
    print(f"scanned {total_files} files; {changed_files} changed; {total_changes} class rewrites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
