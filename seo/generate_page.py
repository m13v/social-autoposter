#!/usr/bin/env python3
"""
Unified SEO page generator.

Called by run_serp_pipeline.sh (discovery) and run_gsc_pipeline.sh (proven demand).
Also usable directly for manual/adhoc triggers. Future pipelines just call generate().

Design: no templates. Creative brief prompt + dynamic palette loaded from
`@m13v/seo-components`'s registry.json (in the consumer repo's node_modules).
Claude decides structure, angle, and content. The generator enforces
observability (stream-json tool capture) and verification (commit lands on
origin/main, live URL 200) before marking state done.

Usage:
    python3 generate_page.py --product Fazm --keyword "local ai agent" \\
        --slug local-ai-agent --trigger serp

    from generate_page import generate
    result = generate(product="Fazm", keyword="local ai agent",
                      slug="local-ai-agent", trigger="serp")
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
CONFIG_PATH = ROOT_DIR / "config.json"

# Dev-mode fallback path for the seo-components registry. In production the
# generator reads the registry from the consumer repo's node_modules copy of
# @m13v/seo-components. If that file is missing (e.g. the consumer hasn't
# upgraded yet) we fall back to the local source checkout so local dev keeps
# working. Hard error only if both are missing.
LOCAL_SEO_COMPONENTS_REGISTRY = Path.home() / "seo-components" / "registry.json"

# Load .env so DATABASE_URL is available when we import db_helpers
ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(SCRIPT_DIR))
import db_helpers  # noqa: E402
from claude_wait import wait_for_claude  # noqa: E402


CLAUDE_TIMEOUT_SECONDS = 1200  # 20 minutes, generous for research + generation


# Content-type routing. Each entry owns the route prefix, the candidate file
# paths Claude should write to, and the example directories the generator tells
# Claude to read for component-composition patterns. Adding a new type (e.g.
# "comparison", "integration") is a matter of adding a row here and, if the
# website repo has a shell component for it, teaching the prompt about it.
CONTENT_TYPES = {
    "guide": {
        "route_prefix": "/t/",
        "path_candidates": [
            "src/app/(content)/t/{slug}/page.tsx",
            "src/app/(main)/t/{slug}/page.tsx",
            "src/app/(landing)/t/{slug}/page.tsx",
            "src/app/t/{slug}/page.tsx",
            "website/src/app/(content)/t/{slug}/page.tsx",
            "website/src/app/(main)/t/{slug}/page.tsx",
            "website/src/app/t/{slug}/page.tsx",
        ],
        "example_dirs": ["src/app/(content)/t/"],
        "description": "a keyword-targeted guide page",
    },
    "alternative": {
        "route_prefix": "/alternative/",
        "path_candidates": [
            "src/app/alternative/{slug}/page.tsx",
        ],
        "example_dirs": ["src/app/alternative/", "src/app/t/"],
        "description": "an alternative/comparison page against a competitor product",
    },
    "use_case": {
        "route_prefix": "/use-case/",
        "path_candidates": [
            "src/app/use-case/{slug}/page.tsx",
        ],
        "example_dirs": ["src/app/use-case/", "src/app/t/"],
        "description": "a use-case page describing one specific job the product does",
    },
    "cross_roundup": {
        "route_prefix": "/best/",
        "path_candidates": [
            "src/app/best/{slug}/page.tsx",
            "src/app/(content)/best/{slug}/page.tsx",
            "src/app/(main)/best/{slug}/page.tsx",
            "website/src/app/best/{slug}/page.tsx",
        ],
        "example_dirs": ["src/app/best/", "src/app/t/"],
        "description": "a dated best-of listicle that ranks the host product alongside 6-10 sibling projects, including cross-industry picks, with every entry wired to trackCrossProductClick for attribution",
    },
}


_ALTERNATIVE_RE = re.compile(
    r"\b(vs|alternative|alternatives|replacement|replace|competitor|competitors)\b",
    re.IGNORECASE,
)


def classify_content_type(keyword: str) -> str:
    """Cheap regex classifier. Defaults to 'guide' (safe fallback, no shell).

    Conservative on purpose: misrouting a keyword to the wrong shell is worse
    than leaving it on the general /t/ guide path. Expand the patterns as we
    build more page-type shells in the website repo.
    """
    kw = (keyword or "").lower().strip()
    if _ALTERNATIVE_RE.search(kw):
        return "alternative"
    return "guide"


def detect_consumer_theme(repo_path: str) -> str:
    """Return 'dark' or 'light' based on the consumer repo's root layout."""
    if not repo_path:
        return "light"
    candidates = [
        Path(repo_path) / "src" / "app" / "layout.tsx",
        Path(repo_path) / "app" / "layout.tsx",
        Path(repo_path) / "src" / "app" / "(main)" / "layout.tsx",
    ]
    for p in candidates:
        try:
            content = p.read_text()
        except (FileNotFoundError, OSError):
            continue
        if 'data-theme="dark"' in content or 'className="dark"' in content:
            return "dark"
        return "light"
    return "light"


def layout_candidates_for_check(root: Path) -> list[Path]:
    return [
        root / "src" / "app" / "layout.tsx",
        root / "app" / "layout.tsx",
    ]


def check_consumer_setup(repo_path: str) -> dict:
    """
    Verify the consumer repo has the @seo/components infrastructure installed
    before we try to ship a page into it. A missing piece means generated pages
    render with white-on-dark component backgrounds, unreadable FaqSection
    headers, no sidebar, and no guide chat (see setup-client-website Phase
    2c/2d/4a/4d/4e).

    Returns {"ok": bool, "missing": [reasons]}. If ok=False, generation must
    refuse until the consumer site is onboarded.
    """
    if not repo_path or not os.path.isdir(repo_path):
        return {"ok": False, "missing": ["repo missing on disk"]}

    root = Path(repo_path)
    missing: list[str] = []

    # Phase 2c: cascade layer ordering so library CSS wins over consumer theme
    globals_css_candidates = [
        root / "src" / "app" / "globals.css",
        root / "app" / "globals.css",
    ]
    globals_css = next((p for p in globals_css_candidates if p.exists()), None)
    if not globals_css:
        missing.append("globals.css not found (Phase 2c)")
    else:
        css_text = globals_css.read_text(errors="ignore")
        if "@layer seo-components" not in css_text:
            missing.append(
                f"{globals_css.relative_to(root)} missing "
                "'@layer seo-components, theme, base, components, utilities' (Phase 2c)"
            )
        has_source_pragma = (
            "@source" in css_text and "seo-components" in css_text
        )
        if not has_source_pragma:
            missing.append(
                f"{globals_css.relative_to(root)} missing "
                "'@source \"../../node_modules/@seo/components/src\"' pragma (Phase 2c). "
                "Without it Tailwind v4 never scans the library and utilities "
                "used inside @seo/components render unstyled."
            )
        # SeoComponentsStyles is the legacy second-stylesheet pattern. It injects
        # duplicates of .hidden / .xl:flex inside @layer seo-components, which
        # loses to the consumer's @layer utilities and forces GuideChatPanel /
        # SitemapSidebar to display:none forever. The @source pragma above
        # already makes this component unnecessary, so flag any lingering usage.
        for lay_p in layout_candidates_for_check(root):
            if lay_p.exists() and "SeoComponentsStyles" in lay_p.read_text(
                errors="ignore"
            ):
                missing.append(
                    f"{lay_p.relative_to(root)} still renders <SeoComponentsStyles /> "
                    "(Phase 2d). Remove it — it collides with @layer utilities "
                    "and forces GuideChatPanel/SitemapSidebar to display:none."
                )
                break

    # Phase 4a: withSeoContent wrapper so /api/guide-chat can read MDX at runtime
    next_cfg_candidates = [
        root / "next.config.ts",
        root / "next.config.mjs",
        root / "next.config.js",
    ]
    next_cfg = next((p for p in next_cfg_candidates if p.exists()), None)
    if not next_cfg:
        missing.append("next.config.* not found (Phase 4a)")
    else:
        cfg_text = next_cfg.read_text(errors="ignore")
        if "withSeoContent" not in cfg_text:
            missing.append(
                f"{next_cfg.name} missing withSeoContent wrapper (Phase 4a)"
            )

    # Phase 4d/4e: sidebar + guide chat + api route mounted in layout
    layout_candidates = layout_candidates_for_check(root)
    layout = next((p for p in layout_candidates if p.exists()), None)
    if not layout:
        missing.append("layout.tsx not found (Phase 2d)")
    else:
        lay_text = layout.read_text(errors="ignore")
        if "HeadingAnchors" not in lay_text:
            missing.append(
                f"{layout.relative_to(root)} missing HeadingAnchors import (Phase 2d)"
            )
        if "SiteSidebar" not in lay_text and "SitemapSidebar" not in lay_text:
            missing.append(
                f"{layout.relative_to(root)} missing SiteSidebar mount (Phase 4d)"
            )
        if "GuideChat" not in lay_text and "GuideChatPanel" not in lay_text:
            missing.append(
                f"{layout.relative_to(root)} missing GuideChat mount (Phase 4e)"
            )

    api_route_candidates = [
        root / "src" / "app" / "api" / "guide-chat" / "route.ts",
        root / "app" / "api" / "guide-chat" / "route.ts",
        root / "src" / "app" / "api" / "guide-chat" / "route.tsx",
    ]
    api_route = next((p for p in api_route_candidates if p.exists()), None)
    if not api_route:
        missing.append("src/app/api/guide-chat/route.ts not found (Phase 4e)")

    # Phase 4a/4e: contentDir in next.config.* (withSeoContent wrapper) and
    # api/guide-chat/route.ts (createGuideChatHandler) must match. If they drift
    # (e.g. route group added to one but not the other), the chat claims "no
    # guides" at runtime and the page-gen pipeline writes MDX to a dir the
    # runtime never scans.
    content_dir_re = re.compile(r'contentDir\s*:\s*"([^"]+)"')
    cfg_dir = None
    route_dir = None
    if next_cfg and next_cfg.exists():
        m = content_dir_re.search(next_cfg.read_text(errors="ignore"))
        if m:
            cfg_dir = m.group(1)
    if api_route and api_route.exists():
        m = content_dir_re.search(api_route.read_text(errors="ignore"))
        if m:
            route_dir = m.group(1)
    if cfg_dir and route_dir and cfg_dir != route_dir:
        missing.append(
            f"contentDir mismatch (Phase 4a/4e): "
            f"{next_cfg.name} has {cfg_dir!r} but "
            f"{api_route.relative_to(root)} has {route_dir!r}. "
            "Both must point to the same directory for withSeoContent's "
            "build-time manifest to match the runtime guide-chat handler."
        )

    return {"ok": len(missing) == 0, "missing": missing}


def load_product_config(product: str) -> dict:
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    lower = product.lower()
    for p in cfg.get("projects", []):
        if p["name"].lower() == lower:
            return p
    raise SystemExit(f"Product '{product}' not found in config.json")


def resolve_source_paths(product_cfg: dict) -> list[dict]:
    """Return list of {path, description} with paths expanded and existence-checked."""
    sources = product_cfg.get("landing_pages", {}).get("product_source", [])
    out = []
    for s in sources:
        raw = s.get("path", "")
        path = os.path.expanduser(raw)
        out.append({
            "path": path,
            "description": s.get("description", "").strip(),
            "exists": os.path.isdir(path),
        })
    return out


def build_cross_roundup_block(host_cfg: dict) -> str:
    """Return a prompt block listing every sibling project for the cross_roundup
    content type. The model picks 6-10 of these (including at least a few
    cross-industry ones) and writes a dated listicle for the host product.
    Each entry's CTA must call trackCrossProductClick so the click attributes
    to the dashboard's Cross Product column."""
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    host_slug = (host_cfg.get("name") or "").lower()
    siblings = []
    for p in cfg.get("projects", []):
        if (p.get("name") or "").lower() == host_slug:
            continue
        if not p.get("website"):
            continue
        sr = p.get("seo_roundup") or {}
        category = sr.get("category") or ""
        siblings.append({
            "name": p["name"],
            "slug": (p["name"] or "").lower(),
            "website": p.get("website", ""),
            "category": category,
            "tagline": p.get("tagline") or p.get("description") or "",
            "get_started_link": p.get("get_started_link") or p.get("website", ""),
            "booking_link": p.get("booking_link") or "",
        })
    host_sr = host_cfg.get("seo_roundup") or {}
    host_category = host_sr.get("category") or ""
    siblings_json = json.dumps(siblings, indent=2, default=str)
    return (
        "=== CROSS-ROUNDUP INPUT ===\n"
        f"Host product: {host_cfg.get('name')} ({host_cfg.get('website','')})\n"
        f"Host niche (use this in the H1 and <title>): {host_category}\n"
        "Today's date is the date the page is generated. Use it in the H1 as:\n"
        '  "Best {host_niche} for {Month} {Day}, {Year}"\n'
        "Slug must include YYYY-MM-DD and lives under /best/<slug>.\n\n"
        "Sibling projects (JSON, pick 6-10 for the list — include at least\n"
        "two that are CROSS-INDUSTRY, not just same-niche; write each entry\n"
        "through the host audience's lens, not as a generic product blurb):\n\n"
        f"{siblings_json}\n\n"
        "MANDATORY rules for this content type:\n"
        "  1. H1 MUST be exactly: \"Best <host niche> for <Month Day, Year>\".\n"
        "     The <title> meta should mirror the H1. Use today's real date.\n"
        "  2. List between 6 and 10 products in total. The host product MUST\n"
        "     be one of them, featured near the top (#1 or #2 is typical for\n"
        "     a first-party page).\n"
        "  3. For sibling entries, write a 2-4 sentence 'why this fits' blurb\n"
        "     from the host audience's point of view. Do not copy the sibling's\n"
        "     own marketing copy verbatim; rewrite it.\n"
        "  4. Every sibling entry's primary CTA MUST be a button/link that calls\n"
        "     trackCrossProductClick on click. Import from @seo/components:\n"
        "       import { trackCrossProductClick } from \"@seo/components\";\n"
        "     Call with:\n"
        "       trackCrossProductClick({\n"
        f"         site: \"{host_slug}\",\n"
        "         targetProduct: <sibling slug>,\n"
        "         destination: <sibling get_started_link>,\n"
        "         text: <visible button text>,\n"
        "         component: \"CrossRoundupEntry\",\n"
        "         section: <e.g. \"entry-3\">,\n"
        "       });\n"
        "     The <a href> on the button is the sibling's get_started_link.\n"
        "     Use target=\"_blank\" rel=\"noopener noreferrer\".\n"
        "  5. The HOST product's CTA can stay as your normal primary CTA\n"
        "     (GetStartedCTA, trackGetStartedClick, or a book-a-call button).\n"
        "     Do NOT fire cross_product_click for the host's own button.\n"
        "  6. Each entry should be visually distinct from the plain guide\n"
        "     template. Use BentoGrid, GlowCard, or numbered rank cards as\n"
        "     the list layout. No bare <ul><li> of product names.\n"
        "  7. Intro paragraph must state the date plainly so readers searching\n"
        "     \"best X April 2026\" see the match immediately.\n"
        "=== END CROSS-ROUNDUP INPUT ===\n"
    )


def format_source_block(sources: list[dict]) -> str:
    if not sources:
        return ("(no external product source configured for this product)\n"
                "Treat the website repo as the product surface. Read widely in it "
                "for landing copy, component implementations, fixtures, and data.")
    parts = []
    for s in sources:
        missing = "" if s["exists"] else " [MISSING ON DISK — do not try to read]"
        parts.append(f"- {s['path']}{missing}\n  {s['description']}")
    return "\n\n".join(parts)


def load_component_registry(repo_path: str) -> dict:
    """Load registry.json that describes the @m13v/seo-components palette.

    The registry is the single source of truth for which components exist and
    how they are described to Claude. It ships inside the npm package at
    `node_modules/@m13v/seo-components/registry.json`. Fall back to the local
    seo-components checkout during development.
    """
    candidates = [
        Path(repo_path) / "node_modules" / "@m13v" / "seo-components" / "registry.json",
        LOCAL_SEO_COMPONENTS_REGISTRY,
    ]
    for p in candidates:
        if p.is_file():
            with open(p) as f:
                return json.load(f)
    tried = "\n  ".join(str(p) for p in candidates)
    raise SystemExit(
        "registry.json not found for @m13v/seo-components. Tried:\n"
        f"  {tried}\n"
        "Upgrade @m13v/seo-components to >= 0.10.0 in the consumer repo, or "
        "ensure the local source checkout has a registry.json."
    )


def _components_in_group(registry: dict, group_key: str) -> list[dict]:
    return [c for c in registry.get("components", []) if c.get("group") == group_key]


def _components_with_quota_tag(registry: dict, tag: str) -> list[dict]:
    out = []
    for c in registry.get("components", []):
        if tag in (c.get("quotaTags") or []):
            out.append(c)
    return out


def _quota_names(registry: dict, tag: str) -> list[str]:
    """Expand quota-tagged components into the specific React component names.

    An entry like 'AnimatedMetric / MetricsRow' ships a `componentNames` list
    so each underlying component gets enumerated in the quota line.
    """
    names: list[str] = []
    for c in _components_with_quota_tag(registry, tag):
        for n in c.get("componentNames") or [c["name"]]:
            if n not in names:
                names.append(n)
    return names


def render_palette(registry: dict) -> str:
    """Render the '### Available components' block from the registry."""
    lines: list[str] = []
    header = registry.get("header", "### Available components")
    lines.append(header)
    lines.append("")

    for group in registry.get("groups", []):
        items = _components_in_group(registry, group["key"])
        mode = group.get("renderMode", "list")
        heading = group["heading"]

        if mode == "inline":
            names = ", ".join(c["name"] for c in items)
            trailing = group.get("trailingNote", "")
            trailing_part = f", {trailing}" if trailing else ""
            lines.append(f"{heading}: {names}{trailing_part}")
            lines.append("")
        else:
            lines.append(heading)
            for c in items:
                sig = c.get("signature", "").strip()
                desc = c.get("description", "").strip()
                sig_part = f" {sig}" if sig else ""
                sep = " — " if desc else ""
                lines.append(f"- `{c['name']}`{sig_part}{sep}{desc}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_quotas(registry: dict) -> str:
    """Render the '### Mandatory component diversity' block with dynamic names.

    Rule text is policy and stays in this generator. Only the component name
    lists are pulled from the registry, so adding a new magic-ui component with
    `quotaTags: ['magic-mandatory']` will extend the quota automatically.
    """
    video_names = ", ".join(f"`{n}`" for n in _quota_names(registry, "video-mandatory"))
    magic_names = ", ".join(f"`{n}`" for n in _quota_names(registry, "magic-mandatory"))
    visual_rich_names = ", ".join(_quota_names(registry, "visual-rich-mandatory"))

    return f"""### Mandatory component diversity (non-negotiable)

A page that only uses code blocks, diagrams, and tables is not rich enough. Every page MUST satisfy ALL of the following quotas, distinct components only:

1. **At least ONE "video-style" component**, from this group: {video_names}.
   - Prefer `RemotionClip` for the hero / concept intro (it literally renders a Remotion composition live). Use `MotionSequence` if you want a longer narrative with visual frames. If you cannot think of what to put in it, use `RemotionClip` with the product name as the title, a one-line subtitle, and 4-5 captions that capture the angle.

2. **At least TWO Magic UI style components**, from this group: {magic_names}.
   - `ShimmerButton` counts toward this quota only if it is the primary CTA, not decoration.
   - `GradientText` counts only if it wraps a meaningful word or phrase in a heading, not an entire paragraph.
   - `Marquee` is an excellent fit for "works with" or "trusted by" strips, integration logos, or feature chip rows.
   - `AnimatedBeam` fits any "inputs → system → outputs" story. If your angle involves the product receiving, processing, and returning something, this is almost always a win.
   - `OrbitingCircles` fits ecosystem / integrations stories.
   - `NumberTicker` should appear inside any metric section that uses concrete numbers.
   - `BackgroundGrid` should wrap the hero section or a high-signal callout.

3. **At least THREE components total** from the "Visual content" and "Rich layout and animation" groups ({visual_rich_names}).

A page that satisfies (3) but not (1) and (2) is incomplete. If your angle genuinely does not want a video component, you still need one — use `RemotionClip` for the hero intro or `MotionSequence` to open the page. This is a hard requirement, not a suggestion.
"""


def render_content_guardrails(product_cfg: dict) -> str:
    """Emit hard content rules for products that define content_guardrails.

    This is the only place where per-product content policy reaches the LLM.
    The voice block is marketing guidance; this block is a set of do-not-cross
    lines for traditions or domains that require it (e.g. the Goenka Vipassana
    tradition reserves technique transmission for authorized teachers at
    10-day courses, so the site must not teach the technique).
    """
    cg = product_cfg.get("content_guardrails") or {}
    if not cg:
        return ""
    lines: list[str] = ["## Content guardrails (hard rules, non-negotiable)", ""]
    summary = (cg.get("summary") or "").strip()
    if summary:
        lines.append(summary)
        lines.append("")
    do_not = cg.get("do_not") or []
    if do_not:
        lines.append("### Do NOT")
        for item in do_not:
            lines.append(f"- {item}")
        lines.append("")
    do_instead = cg.get("do_instead") or []
    if do_instead:
        lines.append("### Do instead")
        for item in do_instead:
            lines.append(f"- {item}")
        lines.append("")
    forbidden = cg.get("forbidden_phrases") or []
    if forbidden:
        lines.append("### Forbidden phrases (must not appear in the page)")
        for item in forbidden:
            lines.append(f"- {item!r}")
        lines.append("")
    redirect = (cg.get("redirect_language") or "").strip()
    if redirect:
        lines.append("### Redirect policy")
        lines.append(redirect)
        lines.append("")
    lines.append(
        "A page that violates any rule in this block must not be written. "
        "If the keyword itself forces a violation to answer it, stop and report "
        "`{\"success\": false, \"error\": \"content_guardrail_violation: <reason>\"}` "
        "as the final JSON instead of shipping a page."
    )
    return "\n".join(lines).rstrip() + "\n"


def render_book_call_block(product_cfg: dict, registry: dict) -> str:
    """Emit the BookCallCTA requirement block if the product has a Cal.com link.

    Reads `booking_link` from config.json. If unset, returns an empty string
    (the page will still render; no booking CTA will be injected). If present,
    we require two BookCallCTA instances: one `footer` near the end of the
    article body and one `sticky` that follows the reader on scroll.
    """
    booking = (product_cfg.get("booking_link") or "").strip()
    if not booking:
        return ""
    has_book_cta = bool(_components_with_quota_tag(registry, "book-call-mandatory"))
    if not has_book_cta:
        return ""
    site_slug = product_cfg.get("name") or product_cfg.get("slug") or ""
    return f"""### Book-a-call CTA (mandatory)

This product has a booking link: `{booking}`.

Every page MUST render exactly TWO `BookCallCTA` instances from `@seo/components`, both pointing at that booking link. They are tracked by the canonical `schedule_click` PostHog event, which feeds the Project Funnel Stats dashboard.

1. **Footer variant** near the end of the article body (after the last prose section, before the FAQ):
   ```tsx
   <BookCallCTA
     appearance="footer"
     destination="{booking}"
     site="{site_slug}"
     heading="<one-line hook tailored to the angle>"
     description="<one sentence explaining what the call unlocks>"
   />
   ```

2. **Sticky variant** so the CTA follows the reader on long pages:
   ```tsx
   <BookCallCTA
     appearance="sticky"
     destination="{booking}"
     site="{site_slug}"
     description="<short benefit-oriented line>"
   />
   ```

Do NOT hard-code `href="https://cal.com/..."` in a raw `<a>` tag or copy the booking URL into a custom button; always use `BookCallCTA` so the PostHog event fires with the canonical shape. Do NOT render more than two BookCallCTA instances per page.

**Booking attribution is automatic.** `BookCallCTA` (and `InlineCta` / `StickyBottomCta` with `trackAs="schedule"`) rewrite the Cal.com URL at click time, appending `metadata[utm_source]=<hostname>`, `metadata[utm_medium]=schedule_click`, and `metadata[utm_campaign]=<pathname>`. Cal.com mirrors those into the booking payload, our webhook writes them to `cal_bookings.utm_source / utm_medium / utm_campaign`, and the top-pages pipeline uses `utm_campaign` to score bookings per landing page. If you build a custom Book-a-Call CTA for any reason, you MUST route its href through `withBookingAttribution` from `@seo/components` or page-level booking attribution breaks.
"""


def build_prompt(product: str, keyword: str, slug: str, trigger: str,
                 product_cfg: dict, source_block: str,
                 content_type: str = "guide",
                 human_guidance: str | None = None,
                 setup_missing: list[str] | None = None) -> str:
    repo = os.path.expanduser(product_cfg.get("landing_pages", {}).get("repo", ""))
    website = (product_cfg.get("landing_pages", {}).get("base_url")
               or product_cfg.get("website", ""))
    differentiator = product_cfg.get("differentiator", "")
    accent_cfg = product_cfg.get("landing_pages", {}).get("accent", {})
    accent_hex = accent_cfg.get("hex", "")
    accent_hex_dark = accent_cfg.get("hex_dark", "")
    accent_name = accent_cfg.get("name", "teal")
    accent_gradient_from = accent_cfg.get(
        "gradient_from",
        "cyan" if accent_name == "teal" else accent_name,
    )

    trigger_context = {
        "serp": "This topic came from discovery. Top-ranking pages leave a real gap, and the product fits the commercial intent behind this search.",
        "gsc": "This query is already driving impressions to the site in Google Search Console. Real users are searching for this. Capture the demand.",
        "manual": "This is an adhoc trigger. Treat the keyword as worth building.",
        "reddit": "This page is being created to drop into a high-performing Reddit thread. Match the thread audience's vocabulary and pain points; the page should genuinely help someone who landed on it from a Reddit comment.",
        "roundup": "This is a weekly cross-product roundup: a listicle hosted on this product's own domain that ranks the host alongside 6-10 sibling projects. The SEO target is the freshness query 'best <niche> <Month Year>'. Click tracking attributes every sibling CTA to the dashboard's Cross Product column.",
    }.get(trigger, "")

    ct = CONTENT_TYPES.get(content_type, CONTENT_TYPES["guide"])
    route_prefix = ct["route_prefix"]
    primary_path = ct["path_candidates"][0].format(slug=slug)
    example_dirs_str = ", ".join(f"`{repo}/{d}`" for d in ct["example_dirs"])
    page_url = f"{website.rstrip('/')}{route_prefix}{slug}"

    registry = load_component_registry(repo)
    palette_block = render_palette(registry)
    quotas_block = render_quotas(registry)
    book_call_block = render_book_call_block(product_cfg, registry)
    guardrails_block = render_content_guardrails(product_cfg)
    consumer_theme = detect_consumer_theme(repo)

    type_context = {
        "guide": "This is a general guide/explainer page. You have the most creative freedom here — the angle, section shape, and length are all yours.",
        "alternative": f"This is an alternative/comparison page. Readers arrived by searching for a competitor product. Your job is to show them {product} is the better pick for the use case their keyword implies. Read an existing alternative page in `{repo}/src/app/alternative/` to see if a shell component exists (e.g. AlternativePageShell) — if it does, use it and emit only a typed data object. If no shell exists in this repo, compose raw sections using the trust-signal components below.",
        "use_case": f"This is a use-case page describing one concrete job {product} does. Readers want to know whether {product} can handle their specific workflow. Show them, with at least one anchor_fact drawn from real product source. If a UseCasePageShell exists in `{repo}/src/components/seo/`, prefer it; otherwise compose raw sections.",
        "cross_roundup": f"This is a dated, cross-product roundup listicle hosted on {product}'s domain. Target query: \"best <niche> <Month Year>\". See the CROSS-ROUNDUP INPUT block below for the host niche, the sibling project list, the mandatory H1/title format, and the mandatory trackCrossProductClick wiring on every sibling entry's CTA. Skip the Step 1 'find an angle' research — for a roundup the angle IS the rank order plus the per-entry reason-it-fits paragraph.",
    }.get(content_type, "")

    cross_roundup_block = ""
    if content_type == "cross_roundup":
        cross_roundup_block = build_cross_roundup_block(product_cfg)

    guidance_block = ""
    if human_guidance:
        # Prepended at the very top so the model cannot miss it. The reply
        # came from a human after a previous escalation; treat it as binding
        # context, not a suggestion. The text is verbatim; do not summarize
        # or interpret -- just follow the instructions.
        guidance_block = (
            "=== HUMAN GUIDANCE (escalation reply) ===\n"
            "A previous attempt to build this page was blocked and escalated\n"
            "to a human. The human's reply is below. Treat it as a binding\n"
            "instruction. If it tells you to apply a specific phase of\n"
            "setup-client-website, do that first. If it says 'skip', mark the\n"
            "row done with notes='skipped per human guidance' and exit\n"
            "without writing any page.\n\n"
            f"{human_guidance}\n"
            "=== END HUMAN GUIDANCE ===\n\n"
        )

    setup_self_heal_block = ""
    if setup_missing:
        # Injected when the consumer-site setup gate found a small number of
        # missing pieces (below the escalation threshold). The model is
        # expected to apply the relevant phases of the setup-client-website
        # skill before doing anything else, then continue with the page
        # build. Hard-cap the tool budget so a broken setup can't burn the
        # whole page-gen budget on yak-shaving.
        bullet_lines = "\n".join(f"  - {item}" for item in setup_missing)
        setup_self_heal_block = (
            "=== SETUP SELF-HEAL (do this BEFORE Step 1) ===\n"
            f"This consumer site is missing {len(setup_missing)} piece(s) of\n"
            "@seo/components / analytics setup. Fix them before writing the\n"
            "page. Budget: <=30 tool calls for setup work. If you can't fix\n"
            "it within that budget, escalate (see ESCALATION RUBRIC below)\n"
            "instead of half-fixing it.\n\n"
            "Missing:\n"
            f"{bullet_lines}\n\n"
            "How: invoke the `setup-client-website` skill and apply only\n"
            "the phases that match the missing items above (typically\n"
            "Phase 2c/2d for mounts and Phase 4a/4d/4e for analytics).\n"
            "Verify with `python3 ~/social-autoposter/scripts/check_analytics_wiring.py`\n"
            "(must show this product as OK) before continuing.\n"
            "=== END SETUP SELF-HEAL ===\n\n"
        )

    escalation_rubric_block = (
        "=== ESCALATION RUBRIC ===\n"
        "If you hit a wall this prompt cannot solve, escalate to a human.\n"
        "DO NOT silently give up, write a stub, or invent facts.\n\n"
        "How to escalate (single shell command, then exit cleanly):\n"
        "  python3 ~/social-autoposter/seo/escalate.py open \\\n"
        f"    --product \"{product}\" \\\n"
        f"    --keyword \"{keyword}\" \\\n"
        f"    --slug \"{slug}\" \\\n"
        "    --trigger model_initiated \\\n"
        "    --reason \"<one to three sentences: what you tried, what's blocking>\"\n\n"
        "After escalating, exit. The pipeline will pause this row and resume\n"
        "it once a human replies (their reply lands as HUMAN GUIDANCE on the\n"
        "next attempt).\n\n"
        "VALID reasons to escalate:\n"
        "  - The keyword and product are semantically incompatible and no\n"
        "    honest angle exists (e.g. keyword is about a topic the product\n"
        "    genuinely does not address).\n"
        "  - Required source files / scripts referenced in this prompt do\n"
        "    not exist on disk and you cannot find equivalents.\n"
        "  - The repo is broken in a way the setup-client-website skill\n"
        "    cannot repair (e.g. unknown framework, corrupted package.json,\n"
        "    custom build system you don't understand).\n"
        "  - You hit a real auth/API wall (missing credential, revoked token,\n"
        "    403 from an external service that requires human intervention).\n"
        "  - Setup self-heal is required and you cannot complete it within\n"
        "    the 30 tool-call budget.\n\n"
        "INVALID reasons (push through instead):\n"
        "  - 'I'm not sure which file to read' -> read more files.\n"
        "  - 'The first WebSearch returned weak results' -> try other queries.\n"
        "  - 'A tool returned an error once' -> retry, read the error, adapt.\n"
        "  - 'This is taking many tool calls' -> volume alone is not a block.\n"
        "  - 'I want to confirm the angle is good' -> commit and ship.\n\n"
        "Threshold: only escalate after you have made at least 10 substantive\n"
        "tool calls trying to make forward progress on the actual blocker.\n"
        "Process noise (linting, log reads, unrelated failures) does not count.\n"
        "=== END ESCALATION RUBRIC ===\n\n"
    )

    return f"""{guidance_block}{setup_self_heal_block}{escalation_rubric_block}You are building one SEO page for {product}. You decide the angle and the content. Your one job is to find something real about the product that no competitor page mentions, and build a page around that.

CONTENT TYPE: {content_type} ({ct['description']})
{type_context}

KEYWORD: "{keyword}"
SLUG: "{slug}"
PRODUCT: {product}
WEBSITE: {website}
REPO (your current working directory): {repo}
DIFFERENTIATOR: {differentiator}

TRIGGER: {trigger}
{trigger_context}

{guardrails_block}
{cross_roundup_block}
## Step 1 — Find an angle no competitor has

Before you write anything, do research. Budget ~15 minutes for this step. It matters more than the writing.

1a. Read the product source.

{source_block}

These are not prompts to extract facts from. They are where the real implementation, real behavior, and real constraints live. Open files. Trace what happens when the product actually does the thing the keyword describes. You are looking for something specific a reader would not find anywhere else.

1b. Run scripts for real data.

If the product has a `scripts/` folder in any of the paths above, look there. That is where database queries, analytics pulls, and data exports live. Run what is available instead of trying to connect to databases directly. Real numbers from the product beat invented benchmarks.

1c. Check what currently ranks.

Use WebSearch for "{keyword}" and read the top 5 results that rank today. Note what they all cover. Note what they all miss. Your angle should be in the gap.

1d. Commit to an angle.

Pick ONE specific thing the product does that is not covered by the top-ranking pages. That thing is the spine of your page.

## Step 2 — Write the concept

Before writing any code, output this block (prose, not JSON, not a code fence):

CONCEPT
  angle: <one sentence describing the specific product behavior your page is built around>
  source: <the exact file path or script command you verified this from>
  anchor_fact: <one concrete, checkable thing — a file name, a number, a specific behavior — that makes the page uncopyable>
  competitor_gap: <what the top-ranking pages miss that your angle fills>

If you cannot fill in all four lines with specific non-generic answers, stop and do more research. Do not proceed to Step 3 with a generic concept.

### SEO jargon must not appear in the rendered page

The words "SERP", "keyword", "search intent", "search results", "top 10", "top 5", "ranking", "rank for", and "SEO" are INTERNAL vocabulary for this prompt. Never write them into the page body, section headers, FAQ answers, hero captions, schema descriptions, or meta descriptions. They break authenticity: a reader who arrived via Google should not be reminded that you wrote this for Google.

When you need to reference competing content, write it the way a subject-matter expert would: "every other guide on this", "most articles about X", "the pages that currently rank", "the existing playbooks", "common advice online", or paraphrase entirely. When you need to reference the query itself, write "this topic", "this question", or the actual subject. If a sentence cannot be rewritten without using one of the forbidden words, rewrite the sentence.

## Step 3 — Pick your component palette

You are working in an existing website repo with a shared SEO component library (`@seo/components`). Import everything from `@seo/components`. If the repo also has local components (e.g. in `@/components/`), you may use those too.

{palette_block}
### Differentiation rule

**Do NOT clone the structure of existing pages.** Read one existing page in {example_dirs_str} ONLY to understand the import syntax and color conventions. Do NOT copy its section ordering, component selection, or layout pattern.

Each page must feel editorially distinct. Pick visual components that match YOUR angle:
- A "how it works" angle might use FlowDiagram + StepTimeline + AnimatedDemo
- A "vs. competitors" angle might use BeforeAfter + ComparisonTable + MetricsRow + GlowCard
- A "deep dive" angle might use SequenceDiagram + AnimatedCodeBlock + BentoGrid
- A "getting started" angle might use AnimatedDemo + StepTimeline + TerminalOutput
- A "feature showcase" angle might use BentoGrid + GlowCard + ParallaxSection + MetricsRow

You must use at least 3 visual content components (not counting trust signals). Using only prose sections with no visual components is a failure.

{quotas_block}
### Lottie

If you reference a Lottie animation via `LottiePlayer`, you MUST also create the JSON file at a real path in `public/` (e.g. `public/lottie/<slug>-hero.json`). Do not reference a Lottie path that does not exist. If you cannot produce a real Lottie JSON, skip it.

### Page chrome: do NOT render a navbar or footer

The site's intermediate layout (`src/app/t/layout.tsx` or `src/app/(main)/layout.tsx`) renders the shared `<SiteNavbar>` and `<SiteFooter>` once for every SEO page. Your page.tsx MUST start directly with the article body (`<article>`, breadcrumbs, hero, etc.) and MUST NOT import or render any of: `SiteNavbar`, `SiteFooter`, `GuideNavbar`, `GuideFooter`, `Navbar`, `Header`, `Footer`. No top-level `<nav>`, `<header>`, or `<footer>` elements either. Rendering one yourself causes a double-navbar / double-footer. If you read an existing page for syntax reference and it renders one of these, IGNORE that part and omit it from your output.

### Color palette (mandatory)

CONSUMER THEME DETECTED: {consumer_theme}. The consumer site's root layout uses a {consumer_theme}-mode global, so your page MUST match. Using the wrong theme renders the article body as a contrasting slab between the navbar and footer.

{f'''DARK theme palette (use these exact classes, do NOT use the light palette below). BRAND ACCENT FAMILY: `{accent_name}` (do NOT substitute teal or any other color — this is the consumer's brand color):

- Article wrapper: `<article className="min-h-screen">` (no explicit bg; let the site-wide dark root show through). Never `bg-white`.
- Headings: `text-zinc-100` (NOT text-zinc-900).
- Body text: `text-zinc-400`. Muted/lede: `text-zinc-500`.
- Primary pill: `bg-{accent_name}-900/30 text-{accent_name}-300`. Secondary pill: `bg-zinc-800/60 text-zinc-300`. Outline pill: `bg-transparent border border-zinc-800 text-zinc-300`.
- Section bands: `bg-zinc-950/40 border-y border-zinc-800/60`.
- Tinted boxes: `bg-{accent_name}-500/10 border border-{accent_name}-500/30`.
- Inline code: `bg-zinc-900 border border-zinc-800 text-{accent_name}-300 font-mono`.
- Dividers/borders: `border-zinc-800/60` (NOT border-zinc-200).
- Links: `text-{accent_name}-300` (NOT text-{accent_name}-600). Accent gradients for CTAs: `from-{accent_gradient_from}-500 to-{accent_name}-500` still works.
- CTA button: `bg-{accent_name}-500 text-zinc-950 hover:bg-{accent_name}-400 font-semibold`.

NEVER emit on a dark consumer: bg-white (solid), bg-zinc-50, bg-zinc-100, text-zinc-900, text-zinc-700, text-zinc-600, text-{accent_name}-700, text-{accent_name}-600, border-zinc-200, bg-{accent_name}-50. Translucent overlays like `bg-white/5` and `bg-black/30` are fine.

NEVER use violet, indigo, or purple anywhere. NEVER use teal unless the brand accent family IS teal.''' if consumer_theme == 'dark' else f'''LIGHT theme palette. BRAND ACCENT FAMILY: `{accent_name}` (do NOT substitute teal or any other color — this is the consumer's brand color):

bg-white base, text-zinc-900 for headings, text-zinc-500/text-gray-600 for secondary text. Accent colors: `from-{accent_gradient_from}-500 to-{accent_name}-500` gradient for CTAs, `text-{accent_name}-600` for links, `bg-{accent_name}-50 text-{accent_name}-700` for badges/pills, `bg-{accent_name}-50 border-{accent_name}-200` for tinted boxes. NEVER use violet, indigo, or purple anywhere. NEVER use teal unless the brand accent family IS teal.

Do NOT use Tailwind semantic theme tokens like `text-foreground`, `text-muted`, `bg-card`, `bg-background`, `bg-surface-light`, `border-border`, `border-white/5`. Those tokens are not wired for light pages and will render invisibly. Use explicit classes like `text-zinc-900`, `text-zinc-500`, `bg-white`, `bg-zinc-50`, `border-zinc-200`.'''}

{"" if not accent_hex else f'''### Product accent color override

This product's brand accent is `{accent_name}` ({accent_hex}), already baked into the Tailwind classes you emit above. For isolated-canvas components that render outside the Tailwind pipeline, pass the hex explicitly:

- `<RemotionClip accentHex="{accent_hex}" accentHexDark="{accent_hex_dark or accent_hex}" ... />`
- `<AnimatedBeam accentColor="{accent_hex}" ... />`
- `<Particles color="{accent_hex}" ... />`
- `<ShineBorder color="{accent_hex}" ... />`

Library components (GradientText, SitemapSidebar, GlowCard, BackgroundGrid, etc.) read `--seo-accent*` CSS variables, which the consumer's `globals.css` already sets to match `{accent_hex}`. You do not need to override those components' variants or props.
'''}### No decorative icons or emoji

Functional icons are allowed: expand/collapse chevrons, check/x indicators in comparison tables, arrow indicators on CTA buttons, terminal chrome, status badges. These exist to convey meaning the text cannot.

Decorative emoji and decorative icons are NOT allowed. Never pass emoji like 🎯 📐 🚫 🔢 🔗 📋 ✨ 🚀 💡 ⚡ as the `icon` prop of BentoGrid cards, FlowDiagram steps, StepTimeline steps, or anywhere else. Never add decorative emoji to section headers, feature lists, use case lists, how-it-works steps, or bullet points. If a component's props accept an `icon` field, leave it undefined unless the icon is genuinely functional (a check, an x, a chevron, an arrow, a lock, a warning triangle). When in doubt, omit it.

### Creative inspiration

You are not building a cookie-cutter SEO shell. Draw from the best modern product marketing and editorial sites. Specifically study these patterns when picking your layout and motion:

- **Linear** (linear.app) — mouse-tracking radial glow on cards, ultra-clean typography, narrow content column, heavy use of whitespace, subtle gradients in accent borders, micro-interactions on hover.
- **Vercel** (vercel.com) — geometric pattern backgrounds, grid lines, bold hero stats, inline small code chips, sequential reveal on scroll.
- **Stripe** (stripe.com) — animated diagrams that draw themselves on scroll, floating 3D-ish cards with layered shadows, gradient meshes in hero areas, side-by-side code + diagram layouts.
- **Apple product pages** — full-bleed sections with large-format photography (or here, large-format diagrams), sticky heading while content scrolls, dramatic scale shifts.
- **Framer** (framer.com) — physics-based spring animations, staggered reveals, hover-triggered card flips and morphs, bold accent gradients.
- **Remotion** (remotion.dev) — video-style sequenced animations: elements enter on a timeline, not just on scroll. Use the `MotionSequence` component (see below) for this.
- **Magic UI / Aceternity** (magicui.design, ui.aceternity.com) — bento grids with animated content, moving gradients, animated beam connectors between elements, glowing borders.

Motion principles: prefer spring physics (`type: "spring"`, `stiffness: 200-400`, `damping: 20-30`) over linear easing. Stagger reveals by 80-120ms. Use `whileInView` with `viewport={{ once: true, margin: "-40px" }}` for scroll entrances. For hover, use subtle scale (1.02) not aggressive (1.1). For emphasis, animate numbers counting up rather than just fading in.

### Tailwind setup (do this BEFORE writing the page)

Check `src/app/globals.css` for a `@source` line pointing at the `@seo/components` package. If it is missing, add it right after the `@import "tailwindcss"` line:

```
@source "../../node_modules/@seo/components/src";
```

Without this line, Tailwind will not generate utility classes used inside the components and SVGs/icons will render at wrong sizes. This only needs to be done once per repo.

Also check that the repo has `transpilePackages: ["@seo/components"]` in its `next.config.ts` (or `.mjs`/`.js`). If missing, add it. This tells Next.js to compile the raw TypeScript source from the package.

## Step 4 — Build the page

- Location: `{repo}/{primary_path}` (or match the convention you found in Step 3 if the repo uses a different path).
- **Structure is yours to invent.** Let the angle from Step 2 dictate everything: section count, section order, which visual components appear where, how the story unfolds. Do not follow a fixed outline. Do not replicate the skeleton of any existing page.
- Length: however long the angle deserves. Shorter and specific beats longer and generic. Do not pad.
- Style: no em dashes, no en dashes, anywhere. Plain direct prose. First person fine where natural.
- At least one section must surface the anchor_fact from your concept, with enough specificity that a reader could verify it (file name, command, number, behavior description). This is the uncopyable part of the page.
- Do not invent statistics. Do not fabricate quotes. If you use numbers, they come from something you read or ran.
- **Visual rhythm:** Alternate between prose sections and visual components. Never stack more than two consecutive prose-only sections without a visual break (diagram, code block, metrics row, comparison table, checklist, or terminal output).

### Required trust signals

Every page MUST include all of the following, but their PLACEMENT is flexible (not locked to a fixed position):

1. **`Breadcrumbs`** — near the top of the page.
2. **`ArticleMeta`** — near the top, after or alongside the title.
3. **`ProofBand`** — anywhere in the top third of the page.
4. **`FaqSection`** — anywhere in the bottom third (does not have to be the last section). At least 5 concrete, specific FAQs drawn from your research. Generic FAQs are worse than no FAQs.
5. **JSON-LD structured data** — `<script type="application/ld+json">` tag. Import `articleSchema`, `breadcrumbListSchema`, and `faqPageSchema` from `@seo/components`.

{book_call_block}
## Step 5 — Typecheck, commit, deploy, and verify the Vercel build

This step is a strict gate. You may not report success until a Vercel production deploy for your commit reaches state `READY`. Local typecheck passing is not enough. A 200 on the page is not enough. You must confirm the deploy state.

**5a. Typecheck (mandatory, pre-commit).**

- Run `npx tsc --noEmit` in the repo. If it reports ANY error, fix the code you introduced and re-run until clean. Never commit on a failing typecheck.
- Common trap: `@seo/components` props are strictly typed. Pass primitives where primitives are expected (e.g., `SequenceDiagram.actors: string[]`, `SequenceDiagram.messages[].from: number`, `OrbitingCircles.items: ReactNode[]`). Confirm by reading the component source under `node_modules/@m13v/seo-components/src/components/` or `~/seo-components/src/components/` if the props shape is non-obvious.

**5b. Commit and push.**

- Stage the new page file (and any new shared components you added).
- Commit on the current branch with a clear message naming the keyword.
- Push to origin main (or whatever the repo's main branch is).
- Confirm the commit is on origin. Record the 7-char SHA.

**5c. Poll Vercel for deploy status (mandatory).**

If the repo has `.vercel/project.json`, run this polling loop from bash. Get the token once:

```
VERCEL_TOKEN=$(python3 -c "import json; print(json.load(open('/Users/matthewdi/Library/Application Support/com.vercel.cli/auth.json'))['token'])")
PROJECT_ID=$(python3 -c "import json; print(json.load(open('.vercel/project.json'))['projectId'])")
TEAM_ID=$(python3 -c "import json; print(json.load(open('.vercel/project.json'))['orgId'])")
SHA=<your 40-char commit SHA from git rev-parse HEAD>
```

Then poll (up to 30 attempts, 10s apart = ~5 min budget):

```
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v6/deployments?teamId=$TEAM_ID&projectId=$PROJECT_ID&target=production&limit=10" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(x['state'], x.get('meta',{{}}).get('githubCommitSha',''), x['uid'], x.get('inspectorUrl','')) for x in d['deployments']]"
```

Find the row matching your $SHA. Terminal states are `READY` (good), `ERROR` (failed), `CANCELED` (retry may be needed). `BUILDING`, `QUEUED`, `INITIALIZING` — keep polling.

**5d. If deploy fails, fix and retry (up to 2 times).**

If the state is `ERROR`, fetch the build log and fix the problem:

```
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" \
  "https://api.vercel.com/v3/deployments/<deploy_uid>/events?teamId=$TEAM_ID&builds=1&direction=backward&limit=100" \
  | python3 -c "import sys,json; [print(e.get('payload',{{}}).get('text','')) for e in json.load(sys.stdin) if e.get('type') in ('stdout','stderr','command','build-error')]"
```

Read the log. The last few error lines usually show the file + line that broke the prerender. Do NOT guess: locate the exact file the build complains about, read it, and fix only what's broken (typically your new page, but occasionally a shared component you added).

After fixing, re-run `npx tsc --noEmit`, commit, push, then poll again with the new SHA. Budget: at most 2 self-heal iterations. If you cannot get a READY state in 2 tries, STOP and report `success: false` with the deploy error message as the reason. Do not paper over the failure with `success: true`; a false success here corrupts the DB and blocks future generations.

**5e. Final live-URL sanity check.**

Only after the Vercel deploy is `READY`, run `curl -sI -o /dev/null -w "%{{http_code}}\\n" {page_url}` — you should see 200. If it's still 404 after 30s (Vercel alias propagation), wait 30s more and retry once. If still 404, report the problem instead of silently succeeding.

## Step 6 — Report back

Output your CONCEPT block from Step 2 in the conversation so it is captured in the log.

Then, as your FINAL message (nothing after it), output exactly one line of JSON with this shape:

{{"success": true, "page_url": "{page_url}", "slug": "{slug}", "commit_sha": "<7-char sha>", "concept_angle": "<one-line angle>"}}

If anything went wrong:

{{"success": false, "error": "<specific reason>", "slug": "{slug}"}}

Do not output any text after the final JSON line.
"""


def run_claude_stream(prompt: str, cwd: str, log_dir: Path, slug: str) -> dict:
    """
    Invoke claude -p with stream-json output. Capture every tool call to a jsonl file.
    Returns a dict: {exit_code, final_result_text, tool_summary, stream_log_path}.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    stream_log = log_dir / f"{ts}_{slug}_stream.jsonl"

    session_id = str(uuid.uuid4())
    cmd = [
        "claude", "-p", prompt,
        "--session-id", session_id,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]

    tool_calls: list[dict] = []
    final_text = ""
    start = time.time()

    # Bridge the Claude Code auto-update unlink window before spawning.
    if not wait_for_claude():
        return {"exit_code": 127, "final_result_text": "",
                "tool_summary": {}, "stream_log_path": str(stream_log),
                "session_id": session_id,
                "error": "claude CLI not on PATH after wait_for_claude timeout"}

    with open(stream_log, "w") as log_f:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            return {"exit_code": 127, "final_result_text": "",
                    "tool_summary": {}, "stream_log_path": str(stream_log),
                    "session_id": session_id,
                    "error": "claude CLI not found on PATH"}

        assert proc.stdout is not None
        while True:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            log_f.write(line)
            log_f.flush()

            if time.time() - start > CLAUDE_TIMEOUT_SECONDS:
                proc.kill()
                _log_claude_session(session_id, "seo_generate_page")
                return {"exit_code": 124, "final_result_text": final_text,
                        "tool_summary": _summarize_tools(tool_calls),
                        "stream_log_path": str(stream_log),
                        "session_id": session_id,
                        "error": f"timeout after {CLAUDE_TIMEOUT_SECONDS}s"}

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") == "assistant":
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "tool_use":
                        tool_calls.append({
                            "name": block.get("name"),
                            "input": block.get("input", {}),
                        })
            elif event.get("type") == "result":
                final_text = event.get("result", "") or ""

        proc.wait()

    _log_claude_session(session_id, "seo_generate_page")
    return {
        "exit_code": proc.returncode,
        "final_result_text": final_text,
        "tool_summary": _summarize_tools(tool_calls),
        "stream_log_path": str(stream_log),
        "session_id": session_id,
    }


def run_claude_stream_resume(session_id: str, prompt: str, cwd: str,
                             log_dir: Path, slug: str,
                             timeout: int = 600) -> dict:
    """Resume a prior Claude session with a follow-up prompt and stream its
    output. Used by the typecheck-fix retry path where we want Claude to
    keep full context of the work it just did. Shorter timeout than a fresh
    session (default 10 min) because we expect a targeted patch, not a full
    rewrite. Same return shape as run_claude_stream."""
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    stream_log = log_dir / f"{ts}_{slug}_retry_stream.jsonl"

    cmd = [
        "claude", "-p", prompt,
        "--resume", session_id,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]

    tool_calls: list[dict] = []
    final_text = ""
    start = time.time()

    if not wait_for_claude():
        return {"exit_code": 127, "final_result_text": "",
                "tool_summary": {}, "stream_log_path": str(stream_log),
                "session_id": session_id,
                "error": "claude CLI not on PATH after wait_for_claude timeout"}

    with open(stream_log, "w") as log_f:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            return {"exit_code": 127, "final_result_text": "",
                    "tool_summary": {}, "stream_log_path": str(stream_log),
                    "session_id": session_id,
                    "error": "claude CLI not found on PATH"}

        assert proc.stdout is not None
        while True:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            log_f.write(line)
            log_f.flush()

            if time.time() - start > timeout:
                proc.kill()
                _log_claude_session(session_id, "seo_generate_page_retry")
                return {"exit_code": 124, "final_result_text": final_text,
                        "tool_summary": _summarize_tools(tool_calls),
                        "stream_log_path": str(stream_log),
                        "session_id": session_id,
                        "error": f"retry timeout after {timeout}s"}

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") == "assistant":
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "tool_use":
                        tool_calls.append({
                            "name": block.get("name"),
                            "input": block.get("input", {}),
                        })
            elif event.get("type") == "result":
                final_text = event.get("result", "") or ""

        proc.wait()

    _log_claude_session(session_id, "seo_generate_page_retry")
    return {
        "exit_code": proc.returncode,
        "final_result_text": final_text,
        "tool_summary": _summarize_tools(tool_calls),
        "stream_log_path": str(stream_log),
        "session_id": session_id,
    }


def _log_claude_session(session_id: str, script_tag: str) -> None:
    """Best-effort: invoke log_claude_session.py to record cost into claude_sessions."""
    logger = ROOT_DIR / "scripts" / "log_claude_session.py"
    if not logger.exists():
        return
    try:
        subprocess.run(
            ["python3", str(logger), "--session-id", session_id, "--script", script_tag],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        pass


def _summarize_tools(calls: list[dict]) -> dict:
    """Count tool calls and flag whether product source was touched."""
    summary: dict = {"total": len(calls), "by_name": {}, "reads": [], "bash": []}
    for c in calls:
        name = c.get("name", "unknown")
        summary["by_name"][name] = summary["by_name"].get(name, 0) + 1
        inp = c.get("input", {}) or {}
        if name == "Read":
            summary["reads"].append(inp.get("file_path", ""))
        elif name == "Bash":
            summary["bash"].append(inp.get("command", "")[:200])
    return summary


def count_source_touches(tool_summary: dict, source_paths: list[str]) -> dict:
    """How many Read/Bash calls touched the product source paths."""
    touches = {p: {"reads": 0, "bash": 0} for p in source_paths}
    for read_path in tool_summary.get("reads", []):
        for sp in source_paths:
            if read_path.startswith(sp):
                touches[sp]["reads"] += 1
    for cmd in tool_summary.get("bash", []):
        for sp in source_paths:
            if sp in cmd:
                touches[sp]["bash"] += 1
    return touches


_FINAL_JSON_RE = re.compile(r"\{[^{}]*\"success\"[^{}]*\}")


def parse_final_json(text: str) -> dict | None:
    """Extract the final JSON status line from Claude's result text."""
    if not text:
        return None
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        m = _FINAL_JSON_RE.search(line)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    m = _FINAL_JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def parse_concept(text: str) -> dict:
    """Extract the CONCEPT block from Claude's output, best-effort."""
    if not text:
        return {}
    out = {}
    lines = text.splitlines()
    in_block = False
    for line in lines:
        if line.strip().startswith("CONCEPT"):
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if not stripped:
                if any(out.values()):
                    break
                continue
            m = re.match(r"^([a-z_]+):\s*(.+)$", stripped)
            if m:
                out[m.group(1)] = m.group(2).strip()
            else:
                break
    return out


def verify_commit_landed(repo_path: str, expected_file: str,
                         max_wait: float = 180.0,
                         poll_interval: float = 20.0) -> dict:
    """Check origin/main (or local HEAD if no remote) for the expected file.
    When the repo has an 'origin' remote, polls with git fetch for up to
    max_wait seconds so the background auto-commit agent (which pushes on a
    ~60s cadence) has time to land the commit before we call it a failure.
    Returns {ok, commit_sha, error}."""
    try:
        r = subprocess.run(["git", "remote"], cwd=repo_path,
                           capture_output=True, text=True, check=True, timeout=10)
        remotes = set(r.stdout.split())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": f"git remote failed: {e}"}

    has_origin = "origin" in remotes
    ref = "origin/main" if has_origin else "HEAD"
    deadline = time.time() + max_wait
    last_err = f"no commit on {ref} touching {expected_file}"
    while True:
        if has_origin:
            try:
                subprocess.run(["git", "fetch", "origin"], cwd=repo_path,
                               check=True, capture_output=True, timeout=60)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                last_err = f"git fetch failed: {e}"
        try:
            r = subprocess.run(
                ["git", "log", ref, "-1", "--format=%h", "--", expected_file],
                cwd=repo_path, capture_output=True, text=True, check=True,
            )
            sha = r.stdout.strip()
        except subprocess.CalledProcessError as e:
            last_err = f"git log {ref} failed: {e.stderr}"
            sha = ""

        if sha:
            return {"ok": True, "commit_sha": sha, "ref": ref}
        if not has_origin or time.time() >= deadline:
            return {"ok": False, "error": last_err}
        time.sleep(poll_interval)


RAW_BOOKING_HREF_RE = re.compile(
    r"""href\s*=\s*\{?\s*["'`][^"'`]*?(cal\.com|calendly\.com)""",
    re.IGNORECASE,
)


# Tailwind classes that produce light-only surfaces/text. On a dark consumer
# (fazm, mediar, assrt, cyrano), every occurrence of a bare class listed here
# must be paired with a `dark:*` variant on the SAME className attribute so
# the element reads correctly in dark mode. A `bg-white` without `dark:bg-*`
# prints a bright component block on a dark page; `text-gray-900` prints
# dark-on-dark; `text-blue-600` links are illegible; `bg-{accent}-50` pastel
# bands disappear. Fazm guide regression, 2026-04-22.
#
# Each entry maps a bare-class regex to the `dark:` prefix that must appear
# somewhere in the same className to pass. Pattern uses a `(?<![:\w])`
# lookbehind so `dark:bg-white/[0.05]` and `hover:bg-gray-50` don't count as
# bare matches — only truly unprefixed occurrences are flagged.
_THEME_LINT_RULES: list[tuple[str, str]] = [
    (r"(?<![:\w])bg-white\b", "dark:bg-"),
    (r"(?<![:\w])bg-gray-50\b", "dark:bg-"),
    (r"(?<![:\w])bg-gray-100\b", "dark:bg-"),
    (r"(?<![:\w])bg-slate-50\b", "dark:bg-"),
    (r"(?<![:\w])bg-zinc-50\b", "dark:bg-"),
    (r"(?<![:\w])bg-neutral-50\b", "dark:bg-"),
    # pastel accent bands (bg-<accent>-50 reads as a washed-out block on dark)
    (r"(?<![:\w])bg-(?:blue|purple|pink|red|orange|amber|yellow|green|"
     r"emerald|teal|cyan|sky|indigo|violet|fuchsia|rose)-50\b", "dark:bg-"),
    # body/heading text
    (r"(?<![:\w])text-gray-900\b", "dark:text-"),
    (r"(?<![:\w])text-gray-800\b", "dark:text-"),
    (r"(?<![:\w])text-gray-700\b", "dark:text-"),
    (r"(?<![:\w])text-gray-600\b", "dark:text-"),
    (r"(?<![:\w])text-slate-900\b", "dark:text-"),
    (r"(?<![:\w])text-slate-800\b", "dark:text-"),
    (r"(?<![:\w])text-slate-700\b", "dark:text-"),
    (r"(?<![:\w])text-slate-600\b", "dark:text-"),
    (r"(?<![:\w])text-zinc-900\b", "dark:text-"),
    (r"(?<![:\w])text-zinc-800\b", "dark:text-"),
    (r"(?<![:\w])text-zinc-700\b", "dark:text-"),
    (r"(?<![:\w])text-zinc-600\b", "dark:text-"),
    # link color
    (r"(?<![:\w])text-blue-600\b", "dark:text-"),
    # borders
    (r"(?<![:\w])border-gray-200\b", "dark:border-"),
    (r"(?<![:\w])border-gray-300\b", "dark:border-"),
    (r"(?<![:\w])border-slate-200\b", "dark:border-"),
    (r"(?<![:\w])border-zinc-200\b", "dark:border-"),
]

_THEME_LINT_COMPILED: list[tuple[re.Pattern, str]] = [
    (re.compile(pat), dark_prefix) for pat, dark_prefix in _THEME_LINT_RULES
]

# Matches className="...", className={`...`}, and className={"..."}.
# Captures the inner classes string in one of the three alternation groups.
_CLASSNAME_ATTR_RE = re.compile(
    r'className\s*=\s*(?:"([^"]*)"|\{`([^`]*)`\}|\{"([^"]*)"\})'
)


def validate_booking_attribution(repo_path: str,
                                 expected_file_candidates: list[str]) -> dict:
    """Fail the run if any generated page file contains a raw Cal.com or
    Calendly href. Booking CTAs must go through `BookCallCTA`, which rewrites
    the URL at click time via `withBookingAttribution` (utm_* + metadata[utm_*]).
    A raw `<a href="https://cal.com/...">` bypasses that rewrite, so the
    resulting booking lands in `cal_bookings` with empty utm columns and
    never attributes back to its source page (PieLine/Clone/mk0r 84-page
    incident, 2026-04-22).

    On failure, restores tracked files and removes untracked ones so the
    background auto-commit daemon has nothing to push, mirroring
    `typecheck_and_cleanup`'s cleanup path.
    """
    root = Path(repo_path)
    findings: list[str] = []
    for rel in expected_file_candidates:
        abs_path = root / rel
        if not abs_path.exists():
            continue
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            findings.append(f"{rel}: read failed: {e}")
            continue
        for m in RAW_BOOKING_HREF_RE.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            snippet = text[max(0, m.start() - 10):m.end() + 40].replace("\n", " ")
            findings.append(f"{rel}:L{line_no} {snippet!r}")

    if not findings:
        return {"ok": True}

    cleaned: list[str] = []
    for rel in expected_file_candidates:
        abs_path = root / rel
        if not abs_path.exists():
            continue
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel],
            cwd=repo_path, capture_output=True, text=True,
        )
        if tracked.returncode == 0:
            subprocess.run(["git", "restore", "--", rel],
                           cwd=repo_path, capture_output=True, text=True)
            cleaned.append(f"{rel} (restored)")
        else:
            try:
                abs_path.unlink()
                parent = abs_path.parent
                if parent.is_dir() and parent != root and not any(parent.iterdir()):
                    parent.rmdir()
                cleaned.append(f"{rel} (removed)")
            except OSError:
                pass

    return {
        "ok": False,
        "error": "raw booking href (must use BookCallCTA): "
                 + " | ".join(findings[:3]),
        "cleaned": cleaned,
    }


def validate_theme_classes(repo_path: str,
                           expected_file_candidates: list[str]) -> dict:
    """Fail the run if a generated page ships light-only Tailwind on a dark
    consumer. Catches the failure mode where the inner Claude session writes
    `bg-gray-50`, `text-gray-900`, `text-blue-600`, or a `bg-{accent}-50`
    band without a paired `dark:*` variant, producing a bright component
    block on fazm/mediar/assrt/cyrano's dark theme (Fazm guide pages
    regression, 2026-04-22).

    Heuristic: for each forbidden bare class found inside a `className=...`
    attribute, require that the matching `dark:` prefix (e.g. `dark:bg-`,
    `dark:text-`, `dark:border-`) appear somewhere in the SAME className
    string. This catches the overwhelmingly common failure mode (the LLM
    wrote zero dark: variants on an element); it intentionally does not
    count every pair individually, because that's enough to retry the
    generation with actionable feedback.

    Skipped on light consumers (bg-white there is correct). Cleanup path
    mirrors validate_booking_attribution / typecheck_and_cleanup: restore
    tracked files, remove untracked ones, so the ~/git-dashboard auto-commit
    cron cannot push the bad page while we retry.
    """
    if detect_consumer_theme(repo_path) != "dark":
        return {"ok": True, "skipped": "consumer theme is light"}

    root = Path(repo_path)
    findings: list[str] = []

    for rel in expected_file_candidates:
        abs_path = root / rel
        if not abs_path.exists():
            continue
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            findings.append(f"{rel}: read failed: {e}")
            continue

        for m in _CLASSNAME_ATTR_RE.finditer(text):
            classes = m.group(1) or m.group(2) or m.group(3) or ""
            if not classes.strip():
                continue
            for rx, dark_prefix in _THEME_LINT_COMPILED:
                bare = rx.search(classes)
                if not bare:
                    continue
                if dark_prefix in classes:
                    continue
                line_no = text.count("\n", 0, m.start()) + 1
                findings.append(
                    f"{rel}:L{line_no} '{bare.group(0)}' needs paired "
                    f"{dark_prefix}* on dark consumer"
                )

    if not findings:
        return {"ok": True}

    cleaned: list[str] = []
    for rel in expected_file_candidates:
        abs_path = root / rel
        if not abs_path.exists():
            continue
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel],
            cwd=repo_path, capture_output=True, text=True,
        )
        if tracked.returncode == 0:
            subprocess.run(["git", "restore", "--", rel],
                           cwd=repo_path, capture_output=True, text=True)
            cleaned.append(f"{rel} (restored)")
        else:
            try:
                abs_path.unlink()
                parent = abs_path.parent
                if parent.is_dir() and parent != root and not any(parent.iterdir()):
                    parent.rmdir()
                cleaned.append(f"{rel} (removed)")
            except OSError:
                pass

    return {
        "ok": False,
        "error": "theme lint (light-only Tailwind on dark consumer; pair "
                 "with dark:* or drop the class): "
                 + " | ".join(findings[:5]),
        "cleaned": cleaned,
    }


def typecheck_and_cleanup(repo_path: str, expected_file_candidates: list[str],
                          restore_on_fail: bool = True) -> dict:
    """Run `npx tsc --noEmit` in the repo to catch type errors the inner Claude
    session may have left behind. If typecheck fails and restore_on_fail is
    True, restore/remove any uncommitted page files under
    expected_file_candidates so the background auto-commit daemon cannot push
    broken work to main.

    restore_on_fail=False is used by the typecheck-retry path in the caller:
    we need Claude to still see the broken file during the resume session so
    it can diff/read/fix it. The caller re-invokes with the default True if
    the retry also fails.

    Returns {ok: bool, error?: str, cleaned?: list[str], skipped?: str}.
    Skipped if the repo is not a TypeScript project.
    """
    root = Path(repo_path)
    if not (root / "package.json").exists() or not (root / "tsconfig.json").exists():
        return {"ok": True, "skipped": "not a TypeScript project"}

    try:
        r = subprocess.run(
            ["npx", "--no-install", "tsc", "--noEmit"],
            cwd=repo_path, capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "tsc --noEmit timed out after 300s"}
    except FileNotFoundError:
        return {"ok": True, "skipped": "npx not on PATH"}

    if r.returncode == 0:
        return {"ok": True}

    tsc_output = (r.stdout + r.stderr).strip()
    if not restore_on_fail:
        return {"ok": False, "error": tsc_output}

    cleaned: list[str] = []
    for rel in expected_file_candidates:
        abs_path = root / rel
        if not abs_path.exists():
            continue
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel],
            cwd=repo_path, capture_output=True, text=True,
        )
        if tracked.returncode == 0:
            subprocess.run(["git", "restore", "--", rel],
                           cwd=repo_path, capture_output=True, text=True)
            cleaned.append(f"{rel} (restored)")
        else:
            try:
                abs_path.unlink()
                parent = abs_path.parent
                if parent.is_dir() and parent != root and not any(parent.iterdir()):
                    parent.rmdir()
                cleaned.append(f"{rel} (removed)")
            except OSError:
                pass

    return {"ok": False, "error": tsc_output, "cleaned": cleaned}


def probe_url_live(url: str, timeout: int = 15, retries: int = 30,
                   interval: int = 10) -> dict:
    """HEAD-then-GET probe with fixed-interval retries. Returns {ok, status, error}.
    Default budget: 30 retries x 10s = ~5 min, enough for a typical Vercel/Netlify
    deploy to finish after git push. 2xx counts as live.
    """
    import urllib.request
    import urllib.error
    last_err = ""
    for attempt in range(retries):
        for method in ("HEAD", "GET"):
            try:
                req = urllib.request.Request(url, method=method,
                                             headers={"User-Agent": "social-autoposter/seo-verify"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    status = resp.getcode()
                    if 200 <= status < 300:
                        return {"ok": True, "status": status}
                    last_err = f"{method} {url} -> {status}"
            except urllib.error.HTTPError as e:
                if 200 <= e.code < 300:
                    return {"ok": True, "status": e.code}
                last_err = f"{method} {url} -> {e.code}"
            except Exception as e:
                last_err = f"{method} {url} failed: {e}"
        if attempt < retries - 1:
            time.sleep(interval)
    return {"ok": False, "error": last_err}


def save_concept_file(concepts_dir: Path, slug: str, product: str, keyword: str,
                      concept: dict, final_json: dict | None,
                      tool_summary: dict, touches: dict) -> Path:
    concepts_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = concepts_dir / f"{ts}_{slug}.md"
    body = [
        f"# {keyword}",
        "",
        f"- product: {product}",
        f"- slug: {slug}",
        f"- generated: {ts}Z",
        "",
        "## Concept",
    ]
    if concept:
        for k, v in concept.items():
            body.append(f"- **{k}**: {v}")
    else:
        body.append("(no concept parsed)")
    body += ["", "## Final JSON", "```json",
             json.dumps(final_json or {}, indent=2), "```", "",
             "## Tool summary", "```json",
             json.dumps({"total": tool_summary.get("total", 0),
                         "by_name": tool_summary.get("by_name", {}),
                         "source_touches": touches}, indent=2),
             "```"]
    out.write_text("\n".join(body))
    return out


def update_state(trigger: str, product: str, keyword: str, status: str,
                 page_url: str | None = None, notes: str | None = None,
                 slug: str | None = None,
                 content_type: str | None = None,
                 claude_session_id: str | None = None) -> None:
    """Dispatch state updates to the right table based on trigger."""
    if trigger == "serp":
        kwargs = {}
        if page_url is not None:
            kwargs["page_url"] = page_url
        if notes is not None:
            kwargs["notes"] = notes
        if content_type is not None:
            kwargs["content_type"] = content_type
        if claude_session_id is not None:
            kwargs["claude_session_id"] = claude_session_id
        db_helpers.update_status(product, keyword, status, **kwargs)
    elif trigger == "gsc":
        conn = db_helpers.get_conn()
        cur = conn.cursor()
        sets = ["status = %s", "updated_at = NOW()"]
        vals: list = [status]
        if page_url is not None:
            sets.append("page_url = %s"); vals.append(page_url)
        if slug is not None:
            sets.append("page_slug = %s"); vals.append(slug)
        if notes is not None:
            sets.append("notes = %s"); vals.append(notes)
        if content_type is not None:
            sets.append("content_type = %s"); vals.append(content_type)
        if claude_session_id is not None:
            sets.append("claude_session_id = %s"); vals.append(claude_session_id)
        if status == "done":
            sets.append("completed_at = NOW()")
        vals.extend([product, keyword])
        cur.execute(
            f"UPDATE gsc_queries SET {', '.join(sets)} WHERE product = %s AND query = %s",
            vals,
        )
        conn.commit()
        cur.close()
        conn.close()
    elif trigger == "reddit":
        conn = db_helpers.get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO seo_keywords (product, keyword, slug, source, status) "
            "VALUES (%s, %s, %s, 'reddit', %s) "
            "ON CONFLICT (product, keyword) DO NOTHING",
            (product, keyword, slug or "", status),
        )
        sets = ["status = %s", "updated_at = NOW()"]
        vals: list = [status]
        if page_url is not None:
            sets.append("page_url = %s"); vals.append(page_url)
        if slug is not None:
            sets.append("slug = %s"); vals.append(slug)
        if notes is not None:
            sets.append("notes = %s"); vals.append(notes)
        if content_type is not None:
            sets.append("content_type = %s"); vals.append(content_type)
        if claude_session_id is not None:
            sets.append("claude_session_id = %s"); vals.append(claude_session_id)
        if status == "done":
            sets.append("completed_at = NOW()")
        vals.extend([product, keyword])
        cur.execute(
            f"UPDATE seo_keywords SET {', '.join(sets)} WHERE product = %s AND keyword = %s",
            vals,
        )
        conn.commit()
        cur.close()
        conn.close()
    elif trigger == "top_page":
        conn = db_helpers.get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO seo_keywords (product, keyword, slug, source, status) "
            "VALUES (%s, %s, %s, 'top_page', %s) "
            "ON CONFLICT (product, keyword) DO NOTHING",
            (product, keyword, slug or "", status),
        )
        sets = ["status = %s", "updated_at = NOW()"]
        vals: list = [status]
        if page_url is not None:
            sets.append("page_url = %s"); vals.append(page_url)
        if slug is not None:
            sets.append("slug = %s"); vals.append(slug)
        if notes is not None:
            sets.append("notes = %s"); vals.append(notes)
        if content_type is not None:
            sets.append("content_type = %s"); vals.append(content_type)
        if claude_session_id is not None:
            sets.append("claude_session_id = %s"); vals.append(claude_session_id)
        if status == "done":
            sets.append("completed_at = NOW()")
        vals.extend([product, keyword])
        cur.execute(
            f"UPDATE seo_keywords SET {', '.join(sets)} WHERE product = %s AND keyword = %s",
            vals,
        )
        conn.commit()
        cur.close()
        conn.close()
    elif trigger == "roundup":
        conn = db_helpers.get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO seo_keywords (product, keyword, slug, source, status) "
            "VALUES (%s, %s, %s, 'roundup', %s) "
            "ON CONFLICT (product, keyword) DO NOTHING",
            (product, keyword, slug or "", status),
        )
        sets = ["status = %s", "updated_at = NOW()"]
        vals: list = [status]
        if page_url is not None:
            sets.append("page_url = %s"); vals.append(page_url)
        if slug is not None:
            sets.append("slug = %s"); vals.append(slug)
        if notes is not None:
            sets.append("notes = %s"); vals.append(notes)
        if content_type is not None:
            sets.append("content_type = %s"); vals.append(content_type)
        if claude_session_id is not None:
            sets.append("claude_session_id = %s"); vals.append(claude_session_id)
        if status == "done":
            sets.append("completed_at = NOW()")
        vals.extend([product, keyword])
        cur.execute(
            f"UPDATE seo_keywords SET {', '.join(sets)} WHERE product = %s AND keyword = %s",
            vals,
        )
        conn.commit()
        cur.close()
        conn.close()
    elif trigger == "manual":
        pass  # caller manages state


def find_existing_target_path(repo_path: str, content_type: str,
                              slug: str) -> str | None:
    """Return first existing candidate page path for this slug, or None.

    Protects hand-edited or previously-generated pages from silent overwrite.
    The generator invokes Claude with --dangerously-skip-permissions, so there
    is no interactive guard inside the Claude session; we must gate at the
    Python layer before calling out. Any match across the candidate list means
    'a page already lives here' — abort unless the caller passes force=True.
    """
    ct = CONTENT_TYPES.get(content_type, CONTENT_TYPES["guide"])
    for tmpl in ct["path_candidates"]:
        p = os.path.join(repo_path, tmpl.format(slug=slug))
        if os.path.exists(p):
            return p
    return None


def generate(product: str, keyword: str, slug: str, trigger: str = "manual",
             content_type: str | None = None, force: bool = False,
             escalation_id: int | None = None,
             human_guidance: str | None = None) -> dict:
    """
    Full generation lifecycle. Caller already marked the row in_progress.
    Returns a structured result; also updates state on success/failure.

    content_type: override classifier. If None, classify_content_type() runs.
    force: if True, overwrite an existing target file. Default False — abort
           with overwrite_blocked status so hand-edited pages are never
           clobbered by a cron tick.
    escalation_id / human_guidance: set by --resume-escalation. When
           escalation_id is provided, on success we shell out to
           seo/escalate.py mark-resumed so the escalation row flips
           from 'replied' to 'resumed'. human_guidance is prepended at
           the very top of the prompt as binding context.
    """
    if content_type is None:
        content_type = classify_content_type(keyword)
    if content_type not in CONTENT_TYPES:
        content_type = "guide"

    product_cfg = load_product_config(product)
    # Normalize to canonical name from config so DB writes never diverge by casing
    product = product_cfg["name"]
    repo_path = os.path.expanduser(
        product_cfg.get("landing_pages", {}).get("repo", "")
    )
    if not repo_path or not os.path.isdir(repo_path):
        update_state(trigger, product, keyword, "pending",
                     notes="repo missing on disk", slug=slug,
                     content_type=content_type)
        return {"success": False, "error": f"repo not found: {repo_path}",
                "content_type": content_type}

    existing = find_existing_target_path(repo_path, content_type, slug)
    if existing and not force:
        rel = os.path.relpath(existing, repo_path)
        note = f"overwrite_blocked: target already exists at {rel}"
        update_state(trigger, product, keyword, "done",
                     notes=note[:500], slug=slug,
                     content_type=content_type)
        return {"success": False, "error": note,
                "content_type": content_type,
                "existing_path": existing}

    setup = check_consumer_setup(repo_path)
    # The 1-3 missing branch ("soft") will not return early; we inject
    # setup_missing into the prompt and let the model self-heal as part of
    # the normal page-build run. Track that here so build_prompt can grow
    # a SETUP SELF-HEAL block.
    setup_missing_for_prompt: list[str] | None = None
    # On a resume, trust the human guidance: they have presumably told the
    # model which setup-client-website phases to apply. The model will run
    # those before writing the page; we cannot enforce setup pre-flight or
    # we will re-escalate before Claude is even spawned. If the model fails
    # to fix setup, the downstream build/verification will catch it and
    # leave the escalation in 'replied' for the next tick to retry.
    if not setup["ok"] and escalation_id is not None:
        print(f"  [resume #{escalation_id}] setup gate has "
              f"{len(setup['missing'])} missing items; skipping pre-flight "
              f"because human guidance is binding context")
    elif not setup["ok"]:
        # Self-heal is the ONLY default path. Pass the missing pieces to the
        # model via build_prompt; the SETUP SELF-HEAL block tells it to
        # invoke the setup-client-website skill (Phase 2c/2d/4a/4d/4e etc.)
        # before writing the page, with a 30 tool-call budget. If the model
        # decides setup is genuinely unfixable mid-run, it can call
        # escalate.py itself with trigger=model_initiated.
        reason = "; ".join(setup["missing"])[:400]
        setup_missing_for_prompt = setup["missing"]
        print(f"  [setup-self-heal] {len(setup['missing'])} missing piece(s); "
              f"injecting into prompt: {reason}")

    sources = resolve_source_paths(product_cfg)
    source_block = format_source_block(sources)
    prompt = build_prompt(product, keyword, slug, trigger, product_cfg,
                          source_block, content_type=content_type,
                          human_guidance=human_guidance,
                          setup_missing=setup_missing_for_prompt)

    log_dir = SCRIPT_DIR / "logs" / product.lower()
    concepts_dir = SCRIPT_DIR / "concepts" / product.lower()

    stream = run_claude_stream(prompt=prompt, cwd=repo_path,
                               log_dir=log_dir, slug=slug)

    final_json = parse_final_json(stream["final_result_text"])
    concept = parse_concept(stream["final_result_text"])

    source_paths = [s["path"] for s in sources if s["exists"]]
    touches = count_source_touches(stream["tool_summary"], source_paths)

    save_concept_file(concepts_dir, slug, product, keyword,
                      concept, final_json, stream["tool_summary"], touches)

    session_id = stream.get("session_id")

    # Stream-level errors (process crash, timeout) are still hard failures.
    if stream.get("error"):
        update_state(trigger, product, keyword, "pending",
                     notes=stream["error"][:500], slug=slug,
                     content_type=content_type,
                     claude_session_id=session_id)
        return {"success": False, "error": stream["error"],
                "content_type": content_type,
                "stream_log": stream["stream_log_path"],
                "tool_summary": stream["tool_summary"]}

    # Whether the inner Claude session reported success itself. If False, we
    # attempt salvage: when the page is committed AND live, treat as success
    # regardless of the missing final marker (e.g. inner session credit-out
    # after the file was already committed/pushed).
    claimed_success = bool(final_json and final_json.get("success"))
    claimed_err = (final_json or {}).get("error", "no final success JSON from claude")

    expected_file_candidates = [
        tmpl.format(slug=slug)
        for tmpl in CONTENT_TYPES[content_type]["path_candidates"]
    ]

    # Pipeline-side typecheck gate. The prompt asks the inner session to run
    # `npx tsc --noEmit`; this enforces it. If the session skipped typecheck
    # and left broken TS in the working tree, the ~/git-dashboard auto-commit
    # cron would otherwise push it to main within ~60s and break the deploy
    # (PieLine aiphoneordering.com incident, 2026-04-21). On failure we
    # restore/remove the candidate page files so auto-commit has nothing to
    # push, then mark the row pending.
    tc = typecheck_and_cleanup(repo_path, expected_file_candidates,
                               restore_on_fail=False)
    if not tc["ok"] and session_id and not tc.get("skipped"):
        # Typecheck-fix retry. Resume the same Claude session with the tsc
        # errors appended, ask for a targeted patch, then re-run typecheck.
        # Salvages lanes where Claude wrote mostly-right TSX with a small
        # type slip (unknown prop, missing local module, wrong generic).
        # Only one retry — if it still fails, fall through to cleanup.
        tsc_err = tc.get("error", "")[-4000:]
        retry_prompt = (
            "Your previous work failed `npx tsc --noEmit`. The errors are "
            "below. Fix them in the files you already wrote. Constraints: "
            "do NOT rename or create new files, do NOT change behavior or "
            "copy, keep edits minimal. After editing, run "
            "`npx tsc --noEmit` yourself to confirm it passes, then "
            "`git add -A && git commit -m \"fix(typecheck): " + slug + "\"`.\n\n"
            "End your final message with exactly one JSON block on its own "
            "line. If fixed successfully: `{\"success\": true}`. "
            "If the errors are structural and you cannot fix in place "
            "without a rewrite: `{\"success\": false, \"error\": "
            "\"cannot fix in place\"}` and stop.\n\n"
            "Typecheck output:\n" + tsc_err
        )
        retry_log_dir = Path(stream["stream_log_path"]).parent
        retry = run_claude_stream_resume(
            session_id, retry_prompt, cwd=repo_path,
            log_dir=retry_log_dir, slug=slug,
        )
        # Re-check. On second failure this also performs the restore/remove
        # so auto-commit can't push a broken page.
        tc = typecheck_and_cleanup(repo_path, expected_file_candidates)
        if tc["ok"]:
            # Second pass passed. Prefer the retry's final result text for
            # the downstream JSON-parse since it's the most recent signal.
            new_final = retry.get("final_result_text") or ""
            if new_final:
                stream = dict(stream)
                stream["final_result_text"] = new_final
                final_json = parse_final_json(new_final)
                claimed_success = bool(final_json and final_json.get("success"))
                claimed_err = (final_json or {}).get("error",
                                                     "no final success JSON from claude")

    if not tc["ok"]:
        # Ensure the cleanup step ran — first pass used restore_on_fail=False
        # so Claude could see the broken file during the retry. If retry was
        # attempted, the second call above already did the cleanup; otherwise
        # run it now so auto-commit can't push broken TS.
        if "cleaned" not in tc:
            tc = typecheck_and_cleanup(repo_path, expected_file_candidates)
        tsc_err = tc.get("error", "")[-800:]
        cleaned = tc.get("cleaned", [])
        note = f"typecheck_failed; cleaned={cleaned}; tsc_tail={tsc_err}"[:500]
        update_state(trigger, product, keyword, "pending",
                     notes=note, slug=slug,
                     content_type=content_type,
                     claude_session_id=session_id)
        return {"success": False,
                "error": f"typecheck failed: {tsc_err}",
                "content_type": content_type,
                "cleaned": cleaned,
                "stream_log": stream["stream_log_path"],
                "tool_summary": stream["tool_summary"]}

    attr = validate_booking_attribution(repo_path, expected_file_candidates)
    if not attr["ok"]:
        attr_err = attr.get("error", "")[:800]
        cleaned = attr.get("cleaned", [])
        note = f"booking_attribution_failed; cleaned={cleaned}; {attr_err}"[:500]
        update_state(trigger, product, keyword, "pending",
                     notes=note, slug=slug,
                     content_type=content_type,
                     claude_session_id=session_id)
        return {"success": False,
                "error": attr_err,
                "content_type": content_type,
                "cleaned": cleaned,
                "stream_log": stream["stream_log_path"],
                "tool_summary": stream["tool_summary"]}

    # Theme lint. Rejects pages that ship light-only Tailwind (bg-white,
    # bg-gray-50, text-gray-900, text-blue-600, bg-{accent}-50, etc.) on a
    # dark consumer without a paired dark:* variant. Skipped on light
    # consumers. Same restore/remove cleanup as the gates above so the
    # auto-commit cron can't push the bad page while we retry.
    theme = validate_theme_classes(repo_path, expected_file_candidates)
    if not theme["ok"]:
        theme_err = theme.get("error", "")[:800]
        cleaned = theme.get("cleaned", [])
        note = f"theme_lint_failed; cleaned={cleaned}; {theme_err}"[:500]
        update_state(trigger, product, keyword, "pending",
                     notes=note, slug=slug,
                     content_type=content_type,
                     claude_session_id=session_id)
        return {"success": False,
                "error": theme_err,
                "content_type": content_type,
                "cleaned": cleaned,
                "stream_log": stream["stream_log_path"],
                "tool_summary": stream["tool_summary"]}

    verify = {"ok": False, "error": f"no candidate matched: {expected_file_candidates}"}
    last_verify_err = ""
    for candidate in expected_file_candidates:
        v = verify_commit_landed(repo_path, candidate)
        if v["ok"]:
            verify = v
            verify["file"] = candidate
            break
        last_verify_err = v.get("error", "")
    if not verify["ok"] and last_verify_err:
        verify["error"] = f"{last_verify_err} (tried {len(expected_file_candidates)} candidates)"

    if not verify["ok"]:
        # Surface the real gate error (e.g. "no commit on origin/main ...").
        # When Claude also skipped the final success JSON, append that as
        # secondary context so we can tell apart "Claude finished but push
        # races lost" from "Claude never finished at all".
        gate_err = verify.get("error", "") or "commit not on origin/main"
        err = gate_err if claimed_success else f"{gate_err}; claude={claimed_err}"
        update_state(trigger, product, keyword, "pending",
                     notes=f"commit not on origin/main: {err}"[:500],
                     slug=slug, content_type=content_type,
                     claude_session_id=session_id)
        return {"success": False, "error": err,
                "content_type": content_type,
                "stream_log": stream["stream_log_path"],
                "tool_summary": stream["tool_summary"]}

    base_url = (product_cfg.get("landing_pages", {}).get("base_url")
                or product_cfg.get("website", "")).rstrip("/")
    has_website = bool(base_url)
    constructed_url = f"{base_url}{CONTENT_TYPES[content_type]['route_prefix']}{slug}" if has_website else ""
    page_url = (final_json or {}).get("page_url") or constructed_url

    # Always probe the live URL before marking done. Vercel/Netlify deploys
    # take minutes after git push, and the inner Claude session may declare
    # success the moment the commit lands. Wait for the page to actually serve.
    # If the product has no configured website (repo-only), trust the commit.
    if has_website and page_url:
        live = probe_url_live(page_url)
        if not live["ok"]:
            # Same pattern as the commit verify gate: surface the real probe
            # error primarily. The DB note already kept both sides; mirror
            # that shape in the return so the lane log isn't misleading.
            live_err = live.get("error", "") or "url not live"
            ret_err = live_err if claimed_success else f"{live_err}; claude={claimed_err}"
            update_state(trigger, product, keyword, "pending",
                         notes=f"url not live: claude={'ok' if claimed_success else claimed_err}; live={live_err}"[:500],
                         slug=slug, content_type=content_type,
                         claude_session_id=session_id)
            return {"success": False,
                    "error": ret_err,
                    "content_type": content_type,
                    "stream_log": stream["stream_log_path"],
                    "tool_summary": stream["tool_summary"]}
    else:
        live = {"ok": True, "status": "no-website-configured"}

    salvage_note = ""
    if not claimed_success:
        salvage_note = f"salvaged after claude={claimed_err}; commit={verify['commit_sha']}; url={live.get('status')}"

    update_state(trigger, product, keyword, "done",
                 page_url=page_url, slug=slug,
                 content_type=content_type,
                 notes=salvage_note or None,
                 claude_session_id=session_id)

    # Close out an escalation if this run was a resume. Best-effort: a
    # mark-resumed failure should not flip the page success to failure.
    if escalation_id is not None:
        try:
            subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "escalate.py"), "mark-resumed",
                 "--id", str(escalation_id),
                 "--log-path", stream["stream_log_path"],
                 "--outcome", "success"],
                check=False, timeout=30,
            )
        except Exception as e:
            print(f"  WARN: mark-resumed failed for #{escalation_id}: {e}",
                  file=sys.stderr)

    return {
        "success": True,
        "page_url": page_url,
        "commit_sha": verify["commit_sha"],
        "content_type": content_type,
        "concept": concept,
        "tool_summary": stream["tool_summary"],
        "source_touches": touches,
        "stream_log": stream["stream_log_path"],
        "salvaged": bool(salvage_note),
        "salvage_note": salvage_note,
        "escalation_id": escalation_id,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    # When --resume-escalation is given, product/keyword/slug are loaded
    # from the seo_escalations row, so they are not required on the CLI.
    ap.add_argument("--product")
    ap.add_argument("--keyword")
    ap.add_argument("--slug")
    ap.add_argument("--trigger", choices=["serp", "gsc", "manual", "reddit", "top_page", "roundup"], default="manual")
    ap.add_argument("--content-type", choices=list(CONTENT_TYPES.keys()), default=None,
                    help="Override the regex classifier. Default: auto-classify from keyword.")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite an existing page at the target path. Default: abort if the target file already exists.")
    ap.add_argument("--resume-escalation", type=int, default=None,
                    help="Resume from a replied seo_escalations row. "
                         "Loads product/keyword/slug from the row, prepends "
                         "the human reply into the prompt as binding "
                         "guidance, and on success calls escalate.py "
                         "mark-resumed.")
    args = ap.parse_args()

    escalation_id = args.resume_escalation
    human_guidance = None
    trigger = args.trigger
    product = args.product
    keyword = args.keyword
    slug = args.slug

    if escalation_id is not None:
        import db_helpers as _db
        conn = _db.get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT product, keyword, slug, status, human_reply, source_table "
            "FROM seo_escalations WHERE id = %s",
            (escalation_id,),
        )
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            print(f"ERROR: escalation #{escalation_id} not found", file=sys.stderr)
            return 1
        e_product, e_keyword, e_slug, e_status, e_reply, e_source_table = row
        if e_status != "replied":
            print(f"ERROR: escalation #{escalation_id} status={e_status} (expected 'replied')",
                  file=sys.stderr)
            return 1
        if not e_reply or not e_reply.strip():
            print(f"ERROR: escalation #{escalation_id} has empty human_reply",
                  file=sys.stderr)
            return 1
        product = product or e_product
        keyword = keyword or e_keyword
        slug = slug or e_slug
        # Pick trigger from the source table if caller did not specify
        if args.trigger == "manual":
            trigger = "gsc" if e_source_table == "gsc_queries" else "serp"
        human_guidance = e_reply

    if not (product and keyword and slug):
        print("ERROR: --product, --keyword, --slug are required (or use --resume-escalation)",
              file=sys.stderr)
        return 2

    result = generate(product=product, keyword=keyword,
                      slug=slug, trigger=trigger,
                      content_type=args.content_type,
                      force=args.force,
                      escalation_id=escalation_id,
                      human_guidance=human_guidance)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
