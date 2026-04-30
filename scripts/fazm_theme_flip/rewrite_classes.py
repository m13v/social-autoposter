"""Rewrite dark-only Tailwind classes in fazm-website to theme-aware pairs.

For each occurrence of a dark-only class (e.g. `bg-zinc-900`, `text-white`) that
is NOT already paired with a `dark:` variant, emit:
    {prefixes}{light_replacement}  dark:{prefixes}{original}

Operates on raw file text. We scan each .tsx/.ts file, find string literals
(double, single, or simple template strings), and rewrite class tokens within
each literal.

Context-aware exception: when a className string contains a saturated colored
background (e.g. `bg-accent`, `bg-teal-500`, `bg-blue-600`), the white-ish text
classes (`text-white`, `text-zinc-100`, etc.) inside the same string are NOT
flipped, since they're providing intentional contrast on a colored fill that
won't change between themes.

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


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------

# Solid (no alpha modifier) dark-only base -> light-mode replacement.
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

    # Text colors (light-on-dark -> dark-on-light)
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

    # Gradient stops - only dark direction
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

# Alpha-flips (e.g. `bg-white/5` -> `bg-black/5 dark:bg-white/5`).
# Only the white direction; black-with-alpha is already light-friendly.
ALPHA_FLIPS: dict[str, str] = {
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

# When a className contains a saturated colored bg, light text tokens are
# providing intentional contrast on a colored fill - leave them alone.
LIGHT_TEXT_BASES_TO_SKIP_ON_COLORED_BG = {
    "text-white", "text-zinc-50", "text-zinc-100",
    "text-slate-50", "text-slate-100",
    "text-neutral-50", "text-neutral-100",
    "text-gray-50", "text-gray-100",
    "text-stone-50", "text-stone-100",
}

# Saturated colored backgrounds (bg-accent + named-color-{300..900}).
# A `bg-{color}-{N}` where N >= 300 is "saturated enough" that white text is
# intentional. `bg-{color}-{50,100,200}` is light enough to need flipping logic.
SATURATED_BG_RE = re.compile(
    r'(?<![\w/-])'
    r'(?:[a-z][\w-]*(?:\[[^\]]+\])?:)*'           # variant chain
    r'(?:'
        r'bg-accent(?:-light|-dark|-dim|-contrast)?'
        r'|'
        r'bg-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)'
        r'-(?:[3-9]\d{2})'                         # 300-999
    r')'
    r'(?:/\d+(?:\.\d+)?|/\[[^\]]+\])?'             # optional alpha
    r'(?![\w/-])'
)


# ---------------------------------------------------------------------------
# Token-level rewrite
# ---------------------------------------------------------------------------

# A single Tailwind class token, decomposed into (variant_prefixes, base, alpha).
# Tokens may contain dashes, slashes, brackets, dots, colons.
TOKEN_BOUNDARY_BEFORE = r'(?<![\w/\.\[\]-])'
TOKEN_BOUNDARY_AFTER = r'(?![\w/\.\[\]-])'
VARIANT_CHAIN = r'((?:(?:[a-z][\w-]*(?:\[[^\]]+\])?|[a-z][\w-]*-\[[^\]]+\]):)*?)'


def _is_dark_paired(prefixes: str) -> bool:
    return bool(re.search(r'(?:^|:)dark:', f':{prefixes}'))


def _make_solid_pattern(base: str) -> re.Pattern:
    return re.compile(
        TOKEN_BOUNDARY_BEFORE
        + VARIANT_CHAIN
        + re.escape(base)
        + TOKEN_BOUNDARY_AFTER
    )


def _make_alpha_pattern(base: str) -> re.Pattern:
    return re.compile(
        TOKEN_BOUNDARY_BEFORE
        + VARIANT_CHAIN
        + re.escape(base)
        + r'(/(?:\d+(?:\.\d+)?|\[[^\]]+\]))'
        + TOKEN_BOUNDARY_AFTER
    )


_SOLID_PATTERNS = [(base, light, _make_solid_pattern(base))
                   for base, light in LIGHT_PAIRS.items()]
_ALPHA_PATTERNS = [(base, flipped, _make_alpha_pattern(base))
                   for base, flipped in ALPHA_FLIPS.items()]


def rewrite_class_string(s: str) -> tuple[str, int]:
    """Rewrite Tailwind class tokens inside a single class string."""
    if not s:
        return s, 0

    has_colored_bg = bool(SATURATED_BG_RE.search(s))
    changes = 0

    # Pass 1: alpha-flips (e.g. bg-white/5)
    for base, flipped, pattern in _ALPHA_PATTERNS:
        # Skip light-text alpha flips when on a colored bg
        if has_colored_bg and base in LIGHT_TEXT_BASES_TO_SKIP_ON_COLORED_BG:
            continue

        def repl(m: re.Match, _base=base, _flipped=flipped) -> str:
            nonlocal changes
            prefixes = m.group(1) or ""
            alpha = m.group(2)
            if _is_dark_paired(prefixes):
                return m.group(0)
            light_token = f'{prefixes}{_flipped}{alpha}'
            dark_token = f'dark:{prefixes}{_base}{alpha}'
            changes += 1
            return f'{light_token} {dark_token}'

        s = pattern.sub(repl, s)

    # Pass 2: solid bases
    for base, light, pattern in _SOLID_PATTERNS:
        if has_colored_bg and base in LIGHT_TEXT_BASES_TO_SKIP_ON_COLORED_BG:
            continue

        def repl(m: re.Match, _base=base, _light=light) -> str:
            nonlocal changes
            prefixes = m.group(1) or ""
            if _is_dark_paired(prefixes):
                return m.group(0)
            light_token = f'{prefixes}{_light}'
            dark_token = f'dark:{prefixes}{_base}'
            changes += 1
            return f'{light_token} {dark_token}'

        s = pattern.sub(repl, s)

    return s, changes


# ---------------------------------------------------------------------------
# String-literal scanner
# ---------------------------------------------------------------------------

# Match string literals: "..." or '...' or `...` (template).
# The value group captures the content. Templates with ${...} interpolations
# are handled by allowing ${...} blocks inside via a permissive pattern.
STRING_LITERAL_RE = re.compile(
    r'(?P<dq>"(?:[^"\\\n]|\\.)*")'
    r'|'
    r"(?P<sq>'(?:[^'\\\n]|\\.)*')"
    r'|'
    r'(?P<bt>`(?:[^`\\]|\\.|\$\{(?:[^{}]|\{[^{}]*\})*\})*`)'
)

# A string literal is "class-y" if it contains at least one dark-only base
# class we know about. Cheap precheck so we don't spend regex time on every
# JSX text fragment.
TARGET_BASES = sorted(set(LIGHT_PAIRS) | set(ALPHA_FLIPS), key=len, reverse=True)
QUICK_CHECK_RE = re.compile(
    r'(?:' + '|'.join(re.escape(b) for b in TARGET_BASES) + r')'
)


def rewrite_text(text: str) -> tuple[str, int]:
    total_changes = 0

    def replace_literal(m: re.Match) -> str:
        nonlocal total_changes
        whole = m.group(0)
        # Strip the opening/closing quote/backtick.
        quote = whole[0]
        body = whole[1:-1]

        if not QUICK_CHECK_RE.search(body):
            return whole

        # Templates with ${...} interpolation: rewrite the static parts AND
        # recursively scan inside ${...} blocks (they may contain ternaries,
        # cn() calls, or other string literals that need rewriting).
        if quote == '`' and '${' in body:
            parts: list[str] = []
            i = 0
            while i < len(body):
                if body[i:i+2] == '${':
                    depth = 1
                    j = i + 2
                    while j < len(body) and depth > 0:
                        if body[j] == '{':
                            depth += 1
                        elif body[j] == '}':
                            depth -= 1
                        j += 1
                    # j points one past the closing '}'. The interior is
                    # body[i+2:j-1]. Recurse via rewrite_text so nested
                    # string literals get scanned too.
                    inner = body[i+2:j-1]
                    rewritten_inner, n = rewrite_text(inner)
                    total_changes += n
                    parts.append('${' + rewritten_inner + '}')
                    i = j
                else:
                    j = body.find('${', i)
                    if j < 0:
                        j = len(body)
                    static = body[i:j]
                    rewritten, n = rewrite_class_string(static)
                    total_changes += n
                    parts.append(rewritten)
                    i = j
            return quote + ''.join(parts) + quote
        else:
            rewritten, n = rewrite_class_string(body)
            total_changes += n
            return quote + rewritten + quote

    new_text = STRING_LITERAL_RE.sub(replace_literal, text)
    return new_text, total_changes


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def walk_files(root: Path):
    for p in root.rglob("*.tsx"):
        if "node_modules" in p.parts:
            continue
        yield p
    for p in root.rglob("*.ts"):
        if "node_modules" in p.parts:
            continue
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
