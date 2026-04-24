#!/usr/bin/env python3
"""
Audit the root layout.tsx in every site registered in config.json for the
sticky-sidebar anchor bug that stranded fde10x.com and 10xats.com (2026-04-24).

The bug: `<SitemapSidebar>`, `<GuideChatPanel>`, and their custom-wrapped
equivalents (`<SiteSidebar>`, `<GuideChat>`) render an `<aside>` with
`lg:sticky top-0 h-screen`. `sticky` only works if the aside is a flex-row
sibling of the main content. When the component is a direct child of `<body>`,
sticky has no anchor, the aside takes the full viewport height as a block, and
the first 900px of the page is dead whitespace above the header.

This script enforces the Phase 8 static check from the setup-client-website
skill: walk the JSX of `src/app/layout.tsx` from the opening `<body>` tag and
fail if any of the four sidebar/chat components sits at depth 0 (direct child
of body). That is the exact failure shape we've seen in the wild.

It does NOT inspect the body className (some sites ship `<body className="flex
flex-col">` and still work because they add an inner `<div className="flex
min-h-screen">` wrapper — the discriminator is where the sidebar lives, not
what the body class says).

Exit code 1 if any site fails; 0 otherwise.

Run from ~/social-autoposter:
  python3 scripts/check_layout_wiring.py             # audit all projects
  python3 scripts/check_layout_wiring.py --only fazm # one project
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"

STICKY_ASIDE_TAGS = {
    "SitemapSidebar",
    "GuideChatPanel",
    "SiteSidebar",
    "GuideChat",
}

LAYOUT_CANDIDATES = (
    "src/app/layout.tsx",
    "src/app/layout.jsx",
    "app/layout.tsx",
    "app/layout.jsx",
)

# Matches any JSX tag: opening, closing, or self-closing. Captures the tag
# name so we can walk depth. Deliberately lenient — we only need to count
# ancestors, not parse attributes.
TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9_]*)[^>]*?(/?)>", re.DOTALL)

# Void HTML elements that never nest children. JSX custom components always
# self-close with `/>`; this list is only for the lowercase HTML tags that
# might appear without the `/`.
VOID_HTML_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


@dataclass
class LayoutReport:
    name: str
    repo: Path
    exists: bool = True
    layout_file: Path | None = None
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stranded_tags: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def expand(path: str) -> Path:
    return Path(os.path.expanduser(path))


def find_layout(repo: Path) -> Path | None:
    for candidate in LAYOUT_CANDIDATES:
        p = repo / candidate
        if p.exists():
            return p
    return None


def walk_body(src: str) -> tuple[list[str], str | None]:
    """Walk JSX from `<body>` to `</body>` and return (stranded_tags, error).

    A tag is stranded if it appears at depth 0 (direct child of body) AND is
    one of the sticky-sidebar components. Depth tracks nesting relative to
    <body>. Self-closing and void tags do not change depth.
    """
    body_open = re.search(r"<body[^>]*>", src)
    if not body_open:
        return ([], "no <body> tag found")

    i = body_open.end()
    depth = 0
    stranded: list[str] = []

    while i < len(src):
        m = TAG_RE.search(src, i)
        if not m:
            return (stranded, "unterminated <body>: no matching </body>")
        closing = bool(m.group(1))
        tag = m.group(2)
        self_close = bool(m.group(3))

        # We hit </body>; walk is done.
        if tag == "body" and closing:
            return (stranded, None)

        is_void = self_close or tag.lower() in VOID_HTML_TAGS

        if (
            not closing
            and tag in STICKY_ASIDE_TAGS
            and depth == 0
        ):
            stranded.append(tag)

        if not closing and not is_void:
            depth += 1
        elif closing:
            depth = max(0, depth - 1)

        i = m.end()

    return (stranded, "walked past end of file without closing </body>")


def check_site(name: str, repo_path: str) -> LayoutReport:
    repo = expand(repo_path)
    report = LayoutReport(name=name, repo=repo)

    if not repo.exists():
        report.exists = False
        report.violations.append(f"repo missing: {repo}")
        return report

    layout = find_layout(repo)
    report.layout_file = layout
    if layout is None:
        report.violations.append(
            "no src/app/layout.tsx found (looked in "
            f"{', '.join(LAYOUT_CANDIDATES)})"
        )
        return report

    try:
        src = layout.read_text()
    except (OSError, UnicodeDecodeError) as e:
        report.violations.append(f"cannot read layout: {e}")
        return report

    stranded, err = walk_body(src)
    if err:
        report.warnings.append(f"layout walk skipped: {err}")
    if stranded:
        report.stranded_tags = stranded
        tags = ", ".join(f"<{t}>" for t in stranded)
        report.violations.append(
            f"{tags} rendered as direct child of <body>. Sticky anchor "
            "will be lost — wrap in <div className=\"flex min-h-screen\"> "
            "as a flex-row sibling of main content. "
            "(Setup-client-website skill, Phase 2d.)"
        )

    return report


def format_report(report: LayoutReport) -> str:
    head = "OK   " if report.ok else "FAIL "
    lines = [f"{head}{report.name}  ({report.repo})"]
    if report.layout_file:
        rel = report.layout_file.relative_to(report.repo)
        lines.append(f"  layout: {rel}")
    for v in report.violations:
        lines.append(f"  violation: {v}")
    for w in report.warnings:
        lines.append(f"  warning: {w}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit root layout.tsx across every project in config.json for "
            "the sticky-sidebar anchor bug (fde10x/10xats 2026-04-24 incident)."
        ),
    )
    parser.add_argument(
        "--only", metavar="NAME", default=None,
        help="Restrict the audit to one project by name (case-sensitive).",
    )
    args = parser.parse_args()

    try:
        config = json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read config.json: {e}", file=sys.stderr)
        return 2

    projects = config.get("projects", []) or []
    reports: list[LayoutReport] = []
    skipped: list[str] = []

    for proj in projects:
        name = proj.get("name")
        lp = proj.get("landing_pages") or {}
        repo = lp.get("repo") if isinstance(lp, dict) else None
        if not name or not repo:
            if name:
                skipped.append(f"{name} (no landing_pages.repo)")
            continue
        if args.only and name != args.only:
            continue
        reports.append(check_site(name, repo))

    for report in reports:
        print(format_report(report))
        print()

    header = f"{'project':<22}  {'layout':<24}  {'result':<6}"
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    failures = 0
    for report in reports:
        layout_col = (
            report.layout_file.relative_to(report.repo).as_posix()
            if report.layout_file else "(missing)"
        )
        result = "ok" if report.ok else "FAIL"
        print(f"{report.name[:22]:<22}  {layout_col[:24]:<24}  {result:<6}")
        if not report.ok:
            failures += 1
    print("=" * len(header))
    print(
        f"audited: {len(reports)}   "
        f"failing: {failures}   "
        f"passing: {len(reports) - failures}"
    )
    if skipped:
        print(f"skipped: {', '.join(skipped)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
