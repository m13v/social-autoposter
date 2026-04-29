#!/usr/bin/env python3
"""
Migrate fazm-website content/blog/*.mdx files to static TSX pages.

For each .mdx file:
  1. Parse YAML frontmatter
  2. Convert markdown body to HTML (mistune, GFM tables)
  3. Write src/app/blog/{slug}/page.tsx
  4. Append to manifest

Then write src/app/blog/_manifest.ts with all post metadata.
After migration, the dynamic [slug]/page.tsx and content/blog/ can be deleted.

Usage:
  python3 scripts/migrate_blog_mdx.py [--dry-run] [--slug SLUG]
"""

import argparse
import math
import os
import re
import sys
import yaml
import mistune
from pathlib import Path

FAZM = Path(__file__).parent.parent.parent / "fazm-website"
BLOG_DIR = FAZM / "content" / "blog"
OUT_DIR = FAZM / "src" / "app" / "blog"
MANIFEST_PATH = FAZM / "src" / "app" / "blog" / "_manifest.ts"

SITE_URL = "https://fazm.ai"
SITE_NAME = "Fazm"
PUBLISHER_LOGO = "https://fazm.ai/logo-112.png"
DEFAULT_AUTHOR = "Matthew Diakonov"

# mistune renderer that wraps tables in a scroll div
class BlogRenderer(mistune.HTMLRenderer):
    def table(self, header, body):
        return (
            '<div class="table-wrapper">'
            f'<table><thead>{header}</thead><tbody>{body}</tbody></table>'
            '</div>'
        )

md = mistune.create_markdown(
    renderer=BlogRenderer(escape=False),
    plugins=["table", "strikethrough", "url"],
)

def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Split YAML frontmatter and body from .mdx content."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.index("---", 3)
    fm = yaml.safe_load(raw[3:end]) or {}
    body = raw[end + 3:].lstrip("\n")
    return fm, body

def md_to_html(body: str) -> str:
    """Convert markdown body to HTML. Handles inline SVG/HTML passthrough."""
    # className= in inline SVG is JSX, not HTML. Replace with class= for dangerouslySetInnerHTML.
    html = md(body)
    html = re.sub(r'\bclassName=', 'class=', html)
    return html

def estimate_reading_time(text: str) -> str:
    words = len(text.split())
    minutes = max(1, math.ceil(words / 200))
    return f"{minutes} min read"

def js_string(value: str) -> str:
    """Escape a string for use inside a JS template literal."""
    # Escape backticks and ${} interpolations
    value = value.replace("\\", "\\\\")
    value = value.replace("`", "\\`")
    value = value.replace("${", "\\${")
    return value

def tags_to_ts(tags: list) -> str:
    if not tags:
        return "[]"
    items = ", ".join(f'"{t}"' for t in tags)
    return f"[{items}]"

def write_page(slug: str, fm: dict, html_content: str, dry_run: bool = False) -> None:
    title = fm.get("title", "")
    description = fm.get("description", "")
    date = str(fm.get("date", ""))
    last_modified = str(fm.get("lastModified", "")) if fm.get("lastModified") else ""
    author = fm.get("author", DEFAULT_AUTHOR) or DEFAULT_AUTHOR
    tags = fm.get("tags", []) or []
    image = fm.get("image", "") or ""

    page_url = f"{SITE_URL}/blog/{slug}"
    og_image = image if image else f"{SITE_URL}/og-default.png"

    title_esc = title.replace('"', '\\"')
    desc_esc = description.replace('"', '\\"')

    if last_modified:
        last_modified_const = f'const LAST_MODIFIED = "{last_modified}";\n'
        last_modified_og_line = '    ...(LAST_MODIFIED ? { modifiedTime: LAST_MODIFIED } : {}),\n'
    else:
        last_modified_const = "const LAST_MODIFIED = undefined;\n"
        last_modified_og_line = ""

    tags_ts = tags_to_ts(tags)
    html_escaped = js_string(html_content)

    tsx = (
        'import type { Metadata } from "next";\n'
        'import { BlogPostLayout } from "@seo/components";\n'
        'import { Navbar } from "@/components/navbar";\n'
        'import { Footer } from "@/components/footer";\n'
        'import { ClaudeMeterCta } from "@seo/components";\n'
        'import { shouldShowClaudeMeterCta } from "@/lib/claude-meter-slugs";\n'
        'import { RelatedPosts } from "@/components/blog/related-posts";\n'
        '\n'
        f'const SLUG = "{slug}";\n'
        f'const TITLE = "{title_esc}";\n'
        f'const DESCRIPTION = "{desc_esc}";\n'
        f'const DATE = "{date}";\n'
        + last_modified_const +
        f'const AUTHOR = "{author}";\n'
        f'const TAGS: string[] = {tags_ts};\n'
        f'const IMAGE = "{og_image}";\n'
        '\n'
        'export const metadata: Metadata = {\n'
        '  title: `${TITLE} - Fazm Blog`,\n'
        '  description: DESCRIPTION,\n'
        '  authors: [{ name: AUTHOR }],\n'
        '  openGraph: {\n'
        '    title: TITLE,\n'
        '    description: DESCRIPTION,\n'
        '    type: "article",\n'
        f'    url: "{page_url}",\n'
        '    siteName: "Fazm",\n'
        '    publishedTime: DATE,\n'
        + last_modified_og_line +
        '    images: [{ url: IMAGE }],\n'
        '  },\n'
        '  twitter: {\n'
        '    card: "summary_large_image",\n'
        '    title: TITLE,\n'
        '    description: DESCRIPTION,\n'
        '    images: [IMAGE],\n'
        '  },\n'
        '  alternates: {\n'
        f'    canonical: "{page_url}",\n'
        '  },\n'
        '};\n'
        '\n'
        f'const HTML_CONTENT = `{html_escaped}`;\n'
        '\n'
        'export default function Page() {\n'
        '  const showCta = shouldShowClaudeMeterCta(SLUG);\n'
        '  return (\n'
        '    <main className="noise-overlay">\n'
        '      <Navbar />\n'
        '      {showCta && <ClaudeMeterCta placement="top" site="fazm" />}\n'
        '      <BlogPostLayout\n'
        '        slug={SLUG}\n'
        '        title={TITLE}\n'
        '        description={DESCRIPTION}\n'
        '        date={DATE}\n'
        '        lastModified={LAST_MODIFIED}\n'
        '        author={AUTHOR}\n'
        '        tags={TAGS}\n'
        '        image={IMAGE}\n'
        f'        siteUrl="{SITE_URL}"\n'
        f'        siteName="{SITE_NAME}"\n'
        f'        publisherLogo="{PUBLISHER_LOGO}"\n'
        '        htmlContent={HTML_CONTENT}\n'
        '      />\n'
        '      {showCta && <ClaudeMeterCta placement="bottom" site="fazm" />}\n'
        '      <RelatedPosts currentSlug={SLUG} currentTags={TAGS} />\n'
        '      <Footer />\n'
        '    </main>\n'
        '  );\n'
        '}\n'
    )
    out_path = OUT_DIR / slug / "page.tsx"
    if dry_run:
        print(f"[dry-run] would write {out_path}")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(tsx, encoding="utf-8")


def build_manifest(posts: list[dict]) -> str:
    """Generate _manifest.ts from post metadata list."""
    lines = [
        'export interface BlogPostMeta {',
        '  slug: string;',
        '  title: string;',
        '  description: string;',
        '  date: string;',
        '  lastModified?: string;',
        '  author: string;',
        '  tags: string[];',
        '  image?: string;',
        '  readingTime: string;',
        '}',
        '',
        'export const BLOG_MANIFEST: BlogPostMeta[] = [',
    ]
    for p in sorted(posts, key=lambda x: x["date"], reverse=True):
        lm = f'"{p["lastModified"]}"' if p.get("lastModified") else "undefined"
        img = f'"{p["image"]}"' if p.get("image") else "undefined"
        tags = tags_to_ts(p.get("tags", []))
        lines.append(
            f'  {{ slug: "{p["slug"]}", title: {json_str(p["title"])}, '
            f'description: {json_str(p["description"])}, date: "{p["date"]}", '
            f'lastModified: {lm}, author: "{p["author"]}", tags: {tags}, '
            f'image: {img}, readingTime: "{p["readingTime"]}" }},'
        )
    lines.append('];')
    lines.append('')
    return '\n'.join(lines)


def json_str(s: str) -> str:
    """Wrap string as a JSON string literal (double-quoted, escaped)."""
    import json
    return json.dumps(s)


def main():
    parser = argparse.ArgumentParser(description="Migrate blog MDX to TSX")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--slug", help="Migrate only this slug")
    args = parser.parse_args()

    if not BLOG_DIR.exists():
        print(f"ERROR: {BLOG_DIR} does not exist", file=sys.stderr)
        sys.exit(1)

    mdx_files = sorted(BLOG_DIR.glob("*.mdx")) + sorted(BLOG_DIR.glob("*.md"))
    if args.slug:
        mdx_files = [f for f in mdx_files if f.stem == args.slug]
        if not mdx_files:
            print(f"ERROR: no file found for slug '{args.slug}'", file=sys.stderr)
            sys.exit(1)

    posts = []
    errors = []
    for mdx_path in mdx_files:
        slug = mdx_path.stem
        try:
            raw = mdx_path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)
            html = md_to_html(body)
            reading_time = estimate_reading_time(body)

            post_meta = {
                "slug": slug,
                "title": fm.get("title", ""),
                "description": fm.get("description", ""),
                "date": str(fm.get("date", "")),
                "lastModified": str(fm.get("lastModified", "")) if fm.get("lastModified") else None,
                "author": fm.get("author", DEFAULT_AUTHOR) or DEFAULT_AUTHOR,
                "tags": fm.get("tags", []) or [],
                "image": fm.get("image", "") or None,
                "readingTime": reading_time,
            }
            posts.append(post_meta)
            write_page(slug, fm, html, dry_run=args.dry_run)

        except Exception as e:
            errors.append((slug, str(e)))
            print(f"ERROR {slug}: {e}", file=sys.stderr)

    if not args.dry_run and not args.slug:
        manifest = build_manifest(posts)
        MANIFEST_PATH.write_text(manifest, encoding="utf-8")
        print(f"Wrote manifest with {len(posts)} posts to {MANIFEST_PATH}")

    print(f"Migrated {len(posts) - len(errors)}/{len(posts)} posts. {len(errors)} errors.")
    if errors:
        for slug, err in errors[:10]:
            print(f"  {slug}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
