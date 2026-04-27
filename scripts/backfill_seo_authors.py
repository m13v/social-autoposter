#!/usr/bin/env python3
"""Rewrite SEO page bylines to match `seo_author` from config.json.

Fixes drift where Claude copied a fabricated persona from a seed page
(c0nsl/Liam Nabut, tenxats/Nhat Nguyen, brand-only "Cyrano Security",
"PieLine Team", etc.) instead of using a configured author.

Per project, walks `landing_pages.repo` and rewrites in every
`/t/<slug>/page.tsx`, `/best/<slug>/page.tsx`, `/alternative/<slug>/page.tsx`:

  - <ArticleMeta author="..." authorRole="..." />
  - articleSchema({ ..., author: "...", authorUrl: "..." })
  - JSON-LD raw object form: author: { "@type": "Person", "name": "..." }

Idempotent: skips files already at target. Dry-run by default; pass
`--apply` to write, and `--commit` to commit + push per repo.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


def resolve_author(project: dict, defaults: dict) -> dict:
    a = project.get("seo_author")
    if isinstance(a, dict) and a.get("name"):
        return a
    a = defaults.get("seo_author")
    if isinstance(a, dict) and a.get("name"):
        return a
    return {
        "name": "Matthew Diakonov",
        "role": "Written with AI",
        "url": "https://m13v.com",
    }


def find_seo_pages(repo: Path) -> list[Path]:
    if not repo.is_dir():
        return []
    pages: list[Path] = []
    for sub in ("t", "best", "alternative"):
        for p in repo.rglob(f"src/app/**/{sub}/*/page.tsx"):
            if "node_modules" in p.parts or ".next" in p.parts:
                continue
            pages.append(p)
    return sorted(set(pages))


def rewrite(text: str, target: dict) -> tuple[str, int]:
    name = target["name"]
    role = target.get("role", "")
    url = target.get("url", "")

    new = text
    changes = 0

    def sub(pattern: str, replacement: str, flags: int = 0) -> None:
        nonlocal new, changes
        new2, n = re.subn(pattern, replacement, new, flags=flags)
        if n:
            changes += n
            new = new2

    # ArticleMeta JSX prop: author="..."
    sub(r'author="[^"]*"', f'author="{name}"')

    # ArticleMeta JSX prop: authorRole="..."
    sub(r'authorRole="[^"]*"', f'authorRole="{role}"')

    # JSON-LD via articleSchema(): author: "..."
    sub(r'(\bauthor:\s*)"[^"]*"', rf'\1"{name}"')

    # JSON-LD via articleSchema(): authorUrl: "..."
    sub(r'(\bauthorUrl:\s*)"[^"]*"', rf'\1"{url}"')

    # Raw JSON-LD object form: author: { "@type": "Person"|"Organization", "name": "..." }
    sub(
        r'(author:\s*\{\s*"@type":\s*"(?:Person|Organization)"\s*,\s*name:\s*)"[^"]*"',
        rf'\1"{name}"',
    )
    # Raw JSON-LD with quoted "name" key
    sub(
        r'(author:\s*\{\s*"@type":\s*"(?:Person|Organization)"\s*,\s*"name":\s*)"[^"]*"',
        rf'\1"{name}"',
    )
    # Object form where @type comes after name
    sub(
        r'(author:\s*\{\s*name:\s*)"[^"]*"(\s*,\s*"@type":\s*"(?:Person|Organization)"\s*\})',
        rf'\1"{name}"\2',
    )

    # If <ArticleMeta ...> block exists but lacks authorRole, inject it after
    # the author= line. Preserves existing indentation. Skips blocks that
    # already declare authorRole.
    if role:
        def inject_role(match: re.Match) -> str:
            block = match.group(0)
            if "authorRole=" in block:
                return block
            # Insert authorRole on its own line right after the author= line
            inner = re.sub(
                r'(\bauthor="[^"]*")',
                rf'\1\n            authorRole="{role}"',
                block,
                count=1,
            )
            return inner

        new2, n = re.subn(
            r'<ArticleMeta\b[^>]*?/>',
            inject_role,
            new,
            flags=re.DOTALL,
        )
        if new2 != new:
            changes += n
            new = new2

    return new, changes


def process_repo(project: dict, defaults: dict, apply: bool, commit: bool, dry_diff: int) -> dict:
    name = project["name"]
    lp = project.get("landing_pages") or {}
    raw_repo = lp.get("repo") or ""
    if not raw_repo:
        return {"name": name, "skipped": "no landing_pages.repo"}
    repo = Path(raw_repo).expanduser()
    if not repo.is_dir():
        return {"name": name, "skipped": f"missing repo {repo}"}

    target = resolve_author(project, defaults)
    pages = find_seo_pages(repo)
    changed_files: list[Path] = []
    total_subs = 0
    sample_diff_shown = 0

    for page in pages:
        text = page.read_text(encoding="utf-8")
        new_text, n = rewrite(text, target)
        if n == 0 or new_text == text:
            continue
        changed_files.append(page)
        total_subs += n
        if not apply and sample_diff_shown < dry_diff:
            sample_diff_shown += 1
            print(f"\n--- {page.relative_to(repo)} ({n} subs) ---")
            for old_line, new_line in zip(text.splitlines(), new_text.splitlines()):
                if old_line != new_line:
                    print(f"  - {old_line.rstrip()}")
                    print(f"  + {new_line.rstrip()}")
        if apply:
            page.write_text(new_text, encoding="utf-8")

    result = {
        "name": name,
        "repo": str(repo),
        "target": target,
        "pages_scanned": len(pages),
        "files_changed": len(changed_files),
        "substitutions": total_subs,
        "applied": apply,
    }

    if apply and commit and changed_files:
        try:
            rel_paths = [str(p.relative_to(repo)) for p in changed_files]
            subprocess.run(
                ["git", "-C", str(repo), "add", "--", *rel_paths],
                check=True,
            )
            msg = (
                f"Standardize SEO byline to {target['name']}"
                f" ({target.get('role', '')})"
            )
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-m", msg],
                check=True,
            )
            push = subprocess.run(
                ["git", "-C", str(repo), "push", "origin", "HEAD"],
                capture_output=True, text=True,
            )
            result["commit_pushed"] = push.returncode == 0
            if push.returncode != 0:
                result["push_stderr"] = push.stderr.strip()
        except subprocess.CalledProcessError as e:
            result["commit_error"] = str(e)

    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default: dry-run)")
    ap.add_argument("--commit", action="store_true",
                    help="commit and push per repo (requires --apply)")
    ap.add_argument("--project", action="append",
                    help="limit to one or more project names")
    ap.add_argument("--dry-diff", type=int, default=2,
                    help="number of sample diffs to print per repo in dry-run")
    args = ap.parse_args()

    cfg = json.loads(CONFIG_PATH.read_text())
    defaults = cfg.get("defaults", {})
    projects = cfg.get("projects", [])
    if args.project:
        wanted = {p.lower() for p in args.project}
        projects = [p for p in projects if p["name"].lower() in wanted]

    summary = []
    for p in projects:
        result = process_repo(p, defaults, args.apply, args.commit, args.dry_diff)
        summary.append(result)
        print(json.dumps(result, indent=2))

    print("\n=== SUMMARY ===")
    for r in summary:
        if r.get("skipped"):
            print(f"  {r['name']:20s} skipped: {r['skipped']}")
            continue
        print(
            f"  {r['name']:20s} files_changed={r.get('files_changed',0):4d} "
            f"subs={r.get('substitutions',0):4d} "
            f"target={r.get('target',{}).get('name','?')}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
