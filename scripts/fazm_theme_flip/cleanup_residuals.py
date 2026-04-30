"""Fix two categories of leftover bugs from the theme-flip rewrite:

1) Triple-token leftovers from an early buggy version of the script that got
   auto-committed before the script was fixed. Pattern (in any class string):
       <color>-black/<N>  dark:<color>-white/<N>  dark:<color>-(zinc|...)-(\\d+)/<N>
   where <color> is one of bg/text/border/ring/divide/from/to/via/outline/shadow.
   Collapse to:
       <color>-(zinc|...)-50/<N>  dark:<color>-(zinc|...)-(\\d+)/<N>

2) Unpaired high-alpha bg-black overlays: `bg-black/<N>` with N >= 30 and no
   sibling `dark:bg-black/...` already in the same className. On a light page
   these render as dark slabs (the homepage "demo" code-block was the visible
   offender in demo-showcase.tsx). Pair with a subtle light-gray substitute so
   the dark intent survives in dark mode.

Run:
    python3 scripts/fazm_theme_flip/cleanup_residuals.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path("/Users/matthewdi/fazm-website")
SRC = REPO / "src"


# ---- Pass 1: triple-token cleanup -----------------------------------------

# The auto-committed buggy output looked like:
#   bg-black/40 dark:bg-white/40 dark:bg-zinc-950/40
# Anchored to the SAME prefix family (bg/text/border/...), and the SAME alpha.
# The intended output is `bg-zinc-50/40 dark:bg-zinc-950/40` (or the zinc family
# matching the original). Collapse with one regex per category.

PROP_FAMILIES = ("bg", "text", "border", "ring", "divide", "from", "to", "via",
                 "outline", "shadow")
NEUTRAL_FAMILIES = ("zinc", "slate", "neutral", "gray", "stone")

# Build a regex that matches the triple-token signature.
# Capture variant chain on the leading token so we preserve responsive prefixes
# like `md:bg-black/40 md:dark:bg-white/40 md:dark:bg-zinc-950/40`.
def _make_triple_pattern(prop: str) -> re.Pattern:
    variant = r'(?P<vc>(?:[a-z][\w-]*(?:\[[^\]]+\])?:)*?)'
    alpha = r'(?P<a>(?:\d+(?:\.\d+)?|\[[^\]]+\]))'
    return re.compile(
        r'(?<![\w/\.\[\]:-])'
        + variant
        + re.escape(f'{prop}-black') + r'/' + alpha
        + r'\s+dark:(?P=vc)' + re.escape(f'{prop}-white') + r'/(?P=a)'
        + r'\s+dark:(?P=vc)' + re.escape(prop) + r'-'
        + r'(?P<n>(?:zinc|slate|neutral|gray|stone))'
        + r'-(?P<lvl>\d+)/(?P=a)'
        + r'(?![\w/\.\[\]-])'
    )


_TRIPLE_PATTERNS = [_make_triple_pattern(p) for p in PROP_FAMILIES]


def collapse_triples(text: str) -> tuple[str, int]:
    changes = 0
    for pat in _TRIPLE_PATTERNS:
        def repl(m: re.Match) -> str:
            nonlocal changes
            vc = m.group('vc')
            alpha = m.group('a')
            family = m.group('n')
            lvl = m.group('lvl')
            # Pick which property prefix this is.
            full = m.group(0)
            for prop in PROP_FAMILIES:
                if f'{prop}-black' in full[:len(prop) + 6 + len(vc) + 1]:
                    p = prop
                    break
            else:
                return full
            light = f'{vc}{p}-{family}-50/{alpha}'
            dark = f'dark:{vc}{p}-{family}-{lvl}/{alpha}'
            changes += 1
            return f'{light} {dark}'
        text = pat.sub(repl, text)
    return text, changes


# ---- Pass 2: pair unpaired high-alpha bg-black ----------------------------

# Match `bg-black/N` (N >= 30) inside a class context where there is no
# matching `dark:bg-black/N` partner already in the same class string.
# We can't easily check sibling tokens with a single regex; do it per
# className-string by tokenizing.

HIGH_ALPHA_BLACK_RE = re.compile(
    r'(?<![\w/\.\[\]:-])'
    r'(?P<vc>(?:[a-z][\w-]*(?:\[[^\]]+\])?:)*?)'
    r'bg-black'
    r'/(?P<a>(?:[3-9]\d|100)(?:\.\d+)?|\[[^\]]+\])'
    r'(?![\w/\.\[\]-])'
)


def _pair_high_alpha_black_in_string(s: str) -> tuple[str, int]:
    """If a class string contains `bg-black/N` (N>=30) without a `dark:`
    partner in the SAME variant chain, pair it with a light alternative."""

    changes = 0

    def repl(m: re.Match) -> str:
        nonlocal changes
        vc = m.group('vc')
        alpha = m.group('a')
        # Already paired? Look for `dark:{vc}bg-black/{alpha}` elsewhere in s.
        partner = re.compile(
            r'(?<![\w/\.\[\]:-])'
            + r'dark:'
            + re.escape(vc)
            + r'bg-black/'
            + re.escape(alpha)
            + r'(?![\w/\.\[\]-])'
        )
        if partner.search(s):
            return m.group(0)
        # Also skip if the original token already has dark: in its variant chain
        # (which would mean we're looking at `dark:bg-black/40` itself).
        if re.search(r'(?:^|:)dark:', f':{vc}'):
            return m.group(0)
        light_token = f'{vc}bg-zinc-100/{alpha}'
        dark_token = f'dark:{vc}bg-black/{alpha}'
        changes += 1
        return f'{light_token} {dark_token}'

    new_s = HIGH_ALPHA_BLACK_RE.sub(repl, s)
    return new_s, changes


# ---- String-literal scanner (re-uses the main script's logic) -------------

# Inline a small scanner here to avoid coupling. Mirrors rewrite_classes.py.

def _find_string_literals(text: str):
    """Walk past JS comment syntax as plain chars: JSX text like `system/*`
    would otherwise trick the scanner into skipping the rest of the file."""
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            j = _scan_simple_string(text, i, c)
            yield (i, j)
            i = j
        elif c == '`':
            j = _scan_template(text, i)
            yield (i, j)
            i = j
        else:
            i += 1


def _scan_simple_string(text: str, start: int, quote: str) -> int:
    n = len(text)
    i = start + 1
    while i < n:
        c = text[i]
        if c == '\\' and i + 1 < n:
            i += 2
        elif c == quote:
            return i + 1
        elif c == '\n':
            return i
        else:
            i += 1
    return n


def _scan_template(text: str, start: int) -> int:
    n = len(text)
    i = start + 1
    while i < n:
        c = text[i]
        if c == '\\' and i + 1 < n:
            i += 2
        elif c == '`':
            return i + 1
        elif c == '$' and i + 1 < n and text[i+1] == '{':
            i = _find_template_expr_end(text, i)
        else:
            i += 1
    return n


def _find_template_expr_end(body: str, start: int) -> int:
    assert body[start:start+2] == '${'
    depth = 1
    i = start + 2
    n = len(body)
    while i < n and depth > 0:
        c = body[i]
        if c == '{':
            depth += 1; i += 1
        elif c == '}':
            depth -= 1; i += 1
        elif c == '"' or c == "'":
            quote = c
            i += 1
            while i < n and body[i] != quote:
                if body[i] == '\\' and i + 1 < n: i += 2
                else: i += 1
            i += 1
        elif c == '`':
            i += 1
            while i < n and body[i] != '`':
                if body[i] == '\\' and i + 1 < n: i += 2
                elif body[i:i+2] == '${':
                    i = _find_template_expr_end(body, i)
                else:
                    i += 1
            i += 1
        else:
            i += 1
    return i


def process_text(text: str) -> tuple[str, int, int]:
    """Apply pass 1 (triples) globally, then pass 2 (high-alpha black)
    per-string-literal."""
    text, n1 = collapse_triples(text)

    parts: list[str] = []
    last = 0
    n2 = 0
    for start, end in _find_string_literals(text):
        if start > last:
            parts.append(text[last:start])
        whole = text[start:end]
        if not whole or len(whole) < 2:
            parts.append(whole)
            last = end
            continue
        quote = whole[0]
        body = whole[1:-1] if whole.endswith(quote) else whole[1:]
        closing = quote if whole.endswith(quote) else ''

        if 'bg-black' not in body:
            parts.append(whole)
            last = end
            continue

        if quote == '`' and '${' in body:
            inner_parts: list[str] = []
            i = 0
            while i < len(body):
                if body[i:i+2] == '${':
                    abs_pos = start + 1 + i
                    abs_end = _find_template_expr_end(text, abs_pos)
                    expr_inner = text[abs_pos+2:abs_end-1]
                    inner_text, inner_n2 = process_inner(expr_inner)
                    n2 += inner_n2
                    inner_parts.append('${' + inner_text + '}')
                    i = abs_end - (start + 1)
                else:
                    j = body.find('${', i)
                    if j < 0:
                        j = len(body)
                    static = body[i:j]
                    new_static, c = _pair_high_alpha_black_in_string(static)
                    n2 += c
                    inner_parts.append(new_static)
                    i = j
            parts.append(quote + ''.join(inner_parts) + closing)
        else:
            new_body, c = _pair_high_alpha_black_in_string(body)
            n2 += c
            parts.append(quote + new_body + closing)
        last = end

    if last < len(text):
        parts.append(text[last:])
    return ''.join(parts), n1, n2


def process_inner(text: str) -> tuple[str, int]:
    """Recurse: handle string literals inside a template expression."""
    parts: list[str] = []
    last = 0
    n2 = 0
    for start, end in _find_string_literals(text):
        if start > last:
            parts.append(text[last:start])
        whole = text[start:end]
        if not whole or len(whole) < 2:
            parts.append(whole)
            last = end
            continue
        quote = whole[0]
        body = whole[1:-1] if whole.endswith(quote) else whole[1:]
        closing = quote if whole.endswith(quote) else ''

        if 'bg-black' not in body:
            parts.append(whole)
            last = end
            continue

        new_body, c = _pair_high_alpha_black_in_string(body)
        n2 += c
        parts.append(quote + new_body + closing)
        last = end

    if last < len(text):
        parts.append(text[last:])
    return ''.join(parts), n2


# ---- Driver ---------------------------------------------------------------

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
    total_pass1 = 0
    total_pass2 = 0

    for path in list(root.rglob("*.tsx")) + list(root.rglob("*.ts")):
        if "node_modules" in path.parts:
            continue
        total_files += 1
        original = path.read_text(encoding="utf-8")
        rewritten, n1, n2 = process_text(original)
        if rewritten == original:
            continue
        changed_files += 1
        total_pass1 += n1
        total_pass2 += n2
        rel = path.relative_to(root)
        if args.dry_run:
            print(f"DRY {rel}: triples={n1} bg-black-pairs={n2}")
        else:
            path.write_text(rewritten, encoding="utf-8")
            print(f"OK  {rel}: triples={n1} bg-black-pairs={n2}")

    print()
    print(f"scanned {total_files} files; {changed_files} changed; "
          f"{total_pass1} triple-collapses; {total_pass2} bg-black pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
