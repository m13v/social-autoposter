#!/usr/bin/env python3
"""
Static audit: every client website in config.json fires the canonical PostHog
events the cross-project dashboard (scripts/project_stats_json.py) queries.

The dashboard filters on these event names scoped by properties.$host:
  - $pageview                                           (automatic)
  - cta_click                                           (generic marketing CTA)
  - get_started_click / download_click /
    cta_get_started_clicked                             (primary self-serve CTA)
  - schedule_click                                      (book-a-call CTA)
  - newsletter_subscribed                               (newsletter form success)

Sites that fire anything else (e.g. `download_email_sent`, `cta_clicked`,
`waitlist_signup`, `contact_submitted`, `landing_cta_clicked`) are invisible
in the dashboard. This script flags that drift so it can be fixed before
it silently drops a conversion funnel.

Two layers of coverage per site:
  1. window.posthog wiring
     - preferred: <FullSiteAnalytics> from @m13v/seo-components in layout.
     - accepted:  hand-rolled provider that assigns window.posthog = posthog.
     - fail:      no provider, or provider that never attaches to window.
  2. event-name audit
     - every posthog.capture(...) call uses a canonical name or documented alias.
     - every primary CTA has a route to a canonical event, either direct
       (raw capture) or via a library helper (NewsletterSignup / BookCallCTA /
       GetStartedCTA / trackScheduleClick / trackGetStartedClick / InlineCta
       with trackAs="get_started"|"schedule").

Exit code: 0 when all sites pass, 1 when at least one site has violations.
Run from ~/social-autoposter: `python3 scripts/check_analytics_wiring.py`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"

CANONICAL_EVENTS = {
    "$pageview",
    "$pageleave",
    "$autocapture",
    "cta_click",
    "get_started_click",
    "schedule_click",
    "newsletter_subscribed",
}

LEGACY_ALIASES = {
    "download_click",
    "cta_get_started_clicked",
}

ALL_ALLOWED_EVENTS = CANONICAL_EVENTS | LEGACY_ALIASES

# Regex matching event names that look like marketing-funnel CTAs but are
# not on the canonical allowlist. Product / interaction events (video_play,
# faq_toggled, checkout_success) are ignored — the funnel does not care.
# A site is allowed to fire whatever it wants for product telemetry. It is
# NOT allowed to invent a parallel CTA event that the dashboard ignores.
CTA_SMELL_RE = re.compile(
    r"^(?:.*_)?(?:"
    r"cta|click|clicked|"
    r"signup|sign_up|subscribe|subscribed|newsletter|waitlist|"
    r"download|install|"
    r"book|schedule|demo|"
    r"get_started|start_building|try_|trial|contact_submit"
    r")(?:_.*)?$",
    re.IGNORECASE,
)

LIBRARY_HELPER_MARKERS = {
    "newsletter_subscribed": [
        "NewsletterSignup",
        'trackAs="newsletter"',
    ],
    "schedule_click": [
        "BookCallCTA",
        "BookCallLink",
        "BookCallTracker",
        "trackScheduleClick",
        'trackAs="schedule"',
    ],
    "get_started_click": [
        "GetStartedCTA",
        "GetStartedLink",
        "GetStartedTracker",
        "trackGetStartedClick",
        'trackAs="get_started"',
    ],
}

# Source markers that tell us a site actually uses a newsletter capture
# (either via the library helper or via a custom email form wired to
# /api/newsletter). Keyword-based smell tests are intentionally avoided here
# because words like "newsletter" or "subscribe" appear in footer copy,
# privacy policies, and SEO text on sites that have no actual signup form.
NEWSLETTER_SOURCE_MARKERS = [
    "NewsletterSignup",
    "/api/newsletter",
]

SOURCE_EXTENSIONS = (".tsx", ".jsx", ".ts", ".js")
SKIP_DIR_NAMES = {"node_modules", ".next", "dist", "build", ".turbo", ".git"}

CAPTURE_CALL_RE = re.compile(
    r"""\b(?:posthog|ph|client|analytics)\??\.capture\s*\(\s*['"]([^'"]+)['"]""",
)
WINDOW_POSTHOG_ASSIGN_RE = re.compile(
    r"""window\s*\.\s*posthog\s*=|
        \(\s*window\s+as[^)]*\)\s*\.\s*posthog\s*=|
        window\s*as\s+unknown\s+as[^;]*?posthog\s*:\s*typeof\s+posthog""",
    re.VERBOSE,
)
FULL_SITE_ANALYTICS_RE = re.compile(r"<\s*FullSiteAnalytics\b")
POSTHOG_PROVIDER_RE = re.compile(r"<\s*PostHogProvider\b|<\s*PHProvider\b")


@dataclass
class SiteReport:
    name: str
    path: Path
    required_ctas: set[str] = field(default_factory=set)
    exists: bool = True
    layout_file: Path | None = None
    analytics_mount: str = "NONE"
    window_posthog_attached: bool | None = None
    events_captured: set[str] = field(default_factory=set)
    library_helpers_used: set[str] = field(default_factory=set)
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def expand(path: str) -> Path:
    return Path(os.path.expanduser(path))


def iter_source_files(root: Path) -> Iterable[Path]:
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            for entry in d.iterdir():
                if entry.name in SKIP_DIR_NAMES:
                    continue
                if entry.is_dir():
                    stack.append(entry)
                elif entry.suffix in SOURCE_EXTENSIONS:
                    yield entry
        except (PermissionError, FileNotFoundError):
            continue


def find_layout(repo: Path) -> Path | None:
    for candidate in (
        "src/app/layout.tsx",
        "src/app/layout.jsx",
        "app/layout.tsx",
        "app/layout.jsx",
    ):
        p = repo / candidate
        if p.exists():
            return p
    return None


def detect_window_posthog(repo: Path) -> bool | None:
    """True if any source file assigns window.posthog = posthog. None if the
    site relies solely on <FullSiteAnalytics>, which handles the assignment
    inside the library (treated as a pass elsewhere)."""
    for candidate in (
        "src/components/posthog-provider.tsx",
        "src/components/posthog-provider.jsx",
        "src/components/PostHogProvider.tsx",
    ):
        p = repo / candidate
        if p.exists() and WINDOW_POSTHOG_ASSIGN_RE.search(p.read_text(errors="ignore")):
            return True
    for file in iter_source_files(repo / "src") if (repo / "src").exists() else []:
        if WINDOW_POSTHOG_ASSIGN_RE.search(file.read_text(errors="ignore")):
            return True
    return False


def scan_repo(site: SiteReport) -> None:
    src_root = site.path / "src" if (site.path / "src").exists() else site.path
    has_newsletter_form = False
    for file in iter_source_files(src_root):
        text = file.read_text(errors="ignore")

        for match in CAPTURE_CALL_RE.finditer(text):
            site.events_captured.add(match.group(1))

        for event_name, markers in LIBRARY_HELPER_MARKERS.items():
            if any(marker in text for marker in markers):
                site.library_helpers_used.add(event_name)

        if any(marker in text for marker in NEWSLETTER_SOURCE_MARKERS):
            has_newsletter_form = True

    if has_newsletter_form:
        site.required_ctas.add("newsletter_subscribed")


def required_from_config(project: dict) -> set[str]:
    """Derive which canonical CTA events a project is on the hook for, from
    the explicit scope declarations in config.json. No keyword smell tests;
    not every site has every CTA, and that is by design."""
    required: set[str] = set()
    if project.get("get_started_link"):
        required.add("get_started_click")
    if project.get("booking_link"):
        required.add("schedule_click")
    return required


def check_site(
    name: str,
    repo_path: str,
    required: set[str] | None = None,
    in_product_telemetry: bool = False,
) -> SiteReport:
    repo = expand(repo_path)
    report = SiteReport(name=name, path=repo, required_ctas=set(required or set()))

    if not repo.exists():
        report.exists = False
        report.violations.append(f"repo missing: {repo}")
        return report

    layout = find_layout(repo)
    report.layout_file = layout
    if layout is None:
        report.violations.append("no app/layout.tsx found")
        return report

    layout_text = layout.read_text(errors="ignore")
    if FULL_SITE_ANALYTICS_RE.search(layout_text):
        report.analytics_mount = "FullSiteAnalytics"
        report.window_posthog_attached = True
    elif POSTHOG_PROVIDER_RE.search(layout_text):
        report.analytics_mount = "hand-rolled"
        report.window_posthog_attached = detect_window_posthog(repo)
        if report.window_posthog_attached is False:
            report.violations.append(
                "hand-rolled PostHogProvider does not assign window.posthog "
                "(library helpers will silently no-op)"
            )
    else:
        report.analytics_mount = "NONE"
        report.window_posthog_attached = False
        report.violations.append("no analytics mount in layout")
        return report

    scan_repo(report)

    # Sites flagged `in_product_telemetry` emit many events shaped like CTAs
    # (sign_in_clicked, publish_deploy_clicked, browser_reconnect_clicked,
    # voice_recording_started, etc.) that are in-product instrumentation, not
    # marketing funnel CTAs. The smell check produces only false positives on
    # those sites. Required canonical events are still enforced below.
    if not in_product_telemetry:
        suspicious = sorted(
            ev for ev in (report.events_captured - ALL_ALLOWED_EVENTS)
            if CTA_SMELL_RE.match(ev)
        )
        if suspicious:
            named = ", ".join(suspicious)
            report.violations.append(
                f"CTA-shaped event name(s) outside the canonical set: {named}. "
                "Dashboard will not count these. Replace with "
                "cta_click / get_started_click / schedule_click / newsletter_subscribed."
            )

    effective_events = (
        report.events_captured & ALL_ALLOWED_EVENTS
    ) | report.library_helpers_used

    for cta in sorted(report.required_ctas):
        if cta not in effective_events:
            report.violations.append(
                f"CTA '{cta}' is in scope for this site but no canonical event "
                "is wired (neither direct capture nor library helper)."
            )

    return report


def format_report(report: SiteReport) -> str:
    bullet = "  "
    lines: list[str] = []
    head = "OK   " if report.ok else "FAIL "
    lines.append(f"{head}{report.name}  ({report.path})")
    lines.append(f"{bullet}mount: {report.analytics_mount}")
    lines.append(
        f"{bullet}window.posthog attached: "
        f"{'yes' if report.window_posthog_attached else 'no/unknown'}"
    )
    if report.events_captured:
        lines.append(
            f"{bullet}raw capture events: {sorted(report.events_captured)}"
        )
    if report.library_helpers_used:
        lines.append(
            f"{bullet}canonical via library: {sorted(report.library_helpers_used)}"
        )
    if report.required_ctas:
        lines.append(f"{bullet}required (scope): {sorted(report.required_ctas)}")
    for v in report.violations:
        lines.append(f"{bullet}violation: {v}")
    return "\n".join(lines)


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text())
    projects = config.get("projects", [])

    failures = 0
    reports: list[SiteReport] = []

    for proj in projects:
        name = proj.get("name")
        lp = proj.get("landing_pages") or {}
        repo = lp.get("repo") if isinstance(lp, dict) else None
        if not repo:
            continue
        report = check_site(
            name,
            repo,
            required=required_from_config(proj),
            in_product_telemetry=bool(proj.get("in_product_telemetry")),
        )
        reports.append(report)
        if not report.ok:
            failures += 1

    for report in reports:
        print(format_report(report))
        print()

    print("=" * 60)
    print(f"audited: {len(reports)}   failing: {failures}   passing: {len(reports) - failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
