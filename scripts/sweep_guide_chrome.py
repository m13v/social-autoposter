#!/usr/bin/env python3
"""
Sweep-delete per-page <GuideNavbar>/<GuideFooter> imports and renders from a
consumer site. Intended to run once per site after wiring <SiteNavbar> +
<SiteFooter> into the intermediate SEO layout.

Usage: python3 scripts/sweep_guide_chrome.py <repo_root>
"""
import re
import sys
from pathlib import Path

TARGETS = {"GuideNavbar", "GuideFooter"}

# Named imports from the site-local guide modules.
IMPORT_MODULES = {
    "@/components/guide",
    "@/components/guide-navbar",
    "@/components/guide-footer",
    "@/components/guide-theme",
}


def process_imports(src: str) -> tuple[str, bool]:
    """Remove GuideNavbar/GuideFooter from imports. Drop the whole import
    line when the remaining name list is empty. Drop *_THEME imports only
    when they no longer have any in-file uses after render-stripping."""
    changed = False
    lines = src.splitlines(keepends=True)
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Handle multi-line imports: collect until closing }
        if (
            line.lstrip().startswith("import {")
            or line.lstrip().startswith("import type {")
            or (line.lstrip().startswith("import") and "{" in line)
        ):
            # Multi-line: accumulate
            chunk = line
            while "}" not in chunk and i + 1 < len(lines):
                i += 1
                chunk += lines[i]
            # Only rewrite if the module matches one we care about
            m = re.search(r'from\s+["\']([^"\']+)["\']', chunk)
            if m and m.group(1) in IMPORT_MODULES:
                # Extract names between the outermost { and }
                left = chunk.index("{")
                right = chunk.rindex("}")
                header = chunk[: left + 1]
                names_blob = chunk[left + 1 : right]
                footer = chunk[right:]
                names = [n.strip() for n in names_blob.split(",")]
                names = [n for n in names if n and n not in TARGETS]
                if not names:
                    # Drop entire import statement
                    changed = True
                else:
                    new_chunk = (
                        header + " " + ", ".join(names) + " " + footer
                    )
                    if not new_chunk.endswith("\n"):
                        new_chunk += "\n"
                    if new_chunk != chunk:
                        changed = True
                    out.append(new_chunk)
            else:
                out.append(chunk)
            i += 1
            continue
        out.append(line)
        i += 1
    return "".join(out), changed


def strip_renders(src: str) -> tuple[str, bool]:
    """Remove <GuideNavbar .../> and <GuideFooter .../> JSX tags. Supports
    both self-closing and paired tags, single-line or multi-line."""
    changed = False
    for name in TARGETS:
        # Self-closing, possibly multi-line: <Name ... />
        pattern_self = re.compile(
            rf"\n?[ \t]*<{name}\b[^>]*?/>[ \t]*\n?",
            re.DOTALL,
        )
        new_src = pattern_self.sub("", src)
        if new_src != src:
            changed = True
            src = new_src
        # Paired tag: <Name ...>...</Name>  (unused on assrt, defensive)
        pattern_paired = re.compile(
            rf"\n?[ \t]*<{name}\b[^>]*>.*?</{name}>[ \t]*\n?",
            re.DOTALL,
        )
        new_src = pattern_paired.sub("", src)
        if new_src != src:
            changed = True
            src = new_src
    return src, changed


def strip_unused_theme_imports(src: str) -> tuple[str, bool]:
    """After render stripping, any *_THEME that only existed to be passed
    to <GuideNavbar theme={X_THEME}/> will be unused. Remove it from
    imports if its only other occurrence was that prop."""
    changed = False
    # Find all imported theme names that look like ALL_CAPS_THEME
    theme_names = re.findall(
        r"(?:,|\{)\s*([A-Z][A-Z0-9_]*_THEME)\s*(?:,|\})", src
    )
    for theme in set(theme_names):
        # Count non-import uses
        non_import_uses = 0
        for line in src.splitlines():
            ls = line.lstrip()
            if ls.startswith("import"):
                continue
            if theme in line:
                non_import_uses += 1
        if non_import_uses == 0:
            # Remove from all imports
            new_src = re.sub(
                rf"(?:,\s*{theme}|{theme}\s*,)\s*",
                "",
                src,
            )
            if new_src != src:
                changed = True
                src = new_src
            # Handle the case where it was the only name
            new_src = re.sub(
                rf"\{{\s*{theme}\s*\}}",
                "{}",
                src,
            )
            if new_src != src:
                changed = True
                src = new_src
    # Drop entire `import {} from "...";` lines
    new_src = re.sub(
        r"""^\s*import\s+(?:type\s+)?\{\s*\}\s*from\s+["'][^"']+["'];?\s*\n""",
        "",
        src,
        flags=re.MULTILINE,
    )
    if new_src != src:
        changed = True
        src = new_src
    return src, changed


def process_file(path: Path) -> bool:
    src = path.read_text()
    orig = src
    # Only touch files that actually reference one of the targets
    if not any(t in src for t in TARGETS):
        return False
    src, c1 = strip_renders(src)
    src, c2 = process_imports(src)
    src, c3 = strip_unused_theme_imports(src)
    if src != orig:
        path.write_text(src)
        return True
    return False


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        sys.exit(2)
    app_dir = root / "src" / "app"
    changed = 0
    for path in app_dir.rglob("*.tsx"):
        if process_file(path):
            changed += 1
    print(f"rewrote {changed} file(s)")


if __name__ == "__main__":
    main()
