#!/usr/bin/env python3
"""
Audit PostHog + @m13v/seo-components wiring across every project registered
in config.json.

Drives from config.json's `projects[].landing_pages.repo`, the authoritative
list of websites we ship. Two audit layers:

STATIC (always runs, cheap, offline):
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

RUNTIME (default; pass --static-only to skip):
  3. Scan `.env.production` for literal `\\n` bytes baked into PostHog values
     (the class of bug that killed Fazm's Stripe webhook for 337 events).
  4. If the repo has `.vercel/project.json` and no root Dockerfile, run
     `vercel env pull --environment production` and scan values for literal
     `\\n`. Also flag when NEXT_PUBLIC_POSTHOG_KEY is entirely missing from
     Vercel prod env while the site uses posthog-js.
  5. Fetch the live production URL and extract the `phc_*` key actually
     shipped in the HTML plus first-load JS chunks. If none ships, the site
     is silently blind.
  6. POST the shipped key to PostHog `/decide` to confirm it's alive (not
     expired / deleted / rotated).
  7. Using the PostHog personal API key from keychain
     (`PostHog-Personal-API-Key-m13v`), map the shipped key back to its
     owning project_id and compare to config.json's posthog.project_id.
     Mismatch means the dashboard queries the wrong project.

Runtime checks degrade gracefully: missing vercel CLI, missing keychain
entry, timeouts, 4xx/5xx, and JSON errors all become warnings, never
tracebacks. Static checks always complete.

Exit code: 1 if any site has errors (static violation OR runtime error);
0 otherwise.

Run from ~/social-autoposter:
  python3 scripts/check_analytics_wiring.py                  # full audit
  python3 scripts/check_analytics_wiring.py --static-only    # offline subset
  python3 scripts/check_analytics_wiring.py --only fazm      # one project
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"

POSTHOG_PERSONAL_KEY_KEYCHAIN = "PostHog-Personal-API-Key-m13v"
POSTHOG_API_BASE = "https://us.posthog.com"
POSTHOG_INGEST_BASE = "https://us.i.posthog.com"

# A PostHog client token begins with phc_ followed by base62 chars.
PHC_RE = re.compile(r"phc_[A-Za-z0-9]{30,60}")

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
# faq_toggled, checkout_success) are ignored; the funnel does not care.
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
    warnings: list[str] = field(default_factory=list)

    # Runtime-layer fields; populated only when runtime mode is on and did
    # not bail out early. `runtime_ran` stays False for sites skipped by
    # --only or if runtime mode was never entered (i.e. --static-only).
    runtime_ran: bool = False
    shipped_key: str | None = None
    shipped_project_id: int | None = None
    configured_project_id: str | None = None
    key_is_alive: bool | None = None

    @property
    def ok(self) -> bool:
        return not self.violations


# ---------------------------------------------------------------------------
# Static layer
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Runtime layer
# ---------------------------------------------------------------------------

def _get_provisioning_key() -> str | None:
    """Fetch the PostHog personal API key from macOS keychain. Returns None
    if unavailable (wrong OS, keychain locked, entry missing, timeout).
    Never raises."""
    try:
        out = subprocess.run(
            [
                "security", "find-generic-password", "-s",
                POSTHOG_PERSONAL_KEY_KEYCHAIN, "-w",
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if out.returncode != 0:
        return None
    key = (out.stdout or "").strip()
    return key or None


def fetch_token_project_map(
    provisioning_key: str,
) -> dict[str, tuple[int, str]]:
    """Return {api_token: (project_id, project_name)} by calling the PostHog
    personal-API endpoint. Empty dict on any error."""
    url = f"{POSTHOG_API_BASE}/api/projects/?limit=200"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {provisioning_key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return {}
    out: dict[str, tuple[int, str]] = {}
    for p in data.get("results", []) or []:
        tok = p.get("api_token")
        pid = p.get("id")
        if tok and pid is not None:
            try:
                out[tok] = (int(pid), p.get("name", ""))
            except (TypeError, ValueError):
                continue
    return out


def scan_env_file_for_literal_newline(path: Path) -> list[str]:
    """Return names of NEXT_PUBLIC_POSTHOG_* vars in `path` whose value
    contains a literal `\\n` / `\\r` sequence. Parses shell-style
    KEY=VALUE lines."""
    if not path.exists():
        return []
    bad: list[str] = []
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if not name.startswith("NEXT_PUBLIC_POSTHOG"):
            continue
        v = value.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if "\\n" in v or "\\r" in v or v.endswith("\\"):
            bad.append(name)
    return bad


def pull_vercel_env(repo: Path) -> dict[str, str] | None:
    """If repo is linked to Vercel, run `vercel env pull` for production and
    return the parsed env. Returns None on any error (not authed, offline,
    not linked). Also returns None when the repo ships a root Dockerfile,
    which signals a Cloud Run / container deploy; a lingering `.vercel/`
    dir from an earlier scaffold must not flip us onto the Vercel path."""
    if (repo / "Dockerfile").exists():
        return None
    if not (repo / ".vercel" / "project.json").exists():
        return None
    dest = Path(f"/tmp/check-wiring-{repo.name}.env")
    try:
        r = subprocess.run(
            [
                "vercel", "env", "pull", str(dest),
                "--environment", "production", "--yes",
            ],
            cwd=repo, capture_output=True, text=True, timeout=45,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if r.returncode != 0 or not dest.exists():
        try:
            dest.unlink()
        except (OSError, FileNotFoundError):
            pass
        return None
    env: dict[str, str] = {}
    try:
        for line in dest.read_text().splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    except (OSError, UnicodeDecodeError):
        env = {}
    try:
        dest.unlink()
    except (OSError, FileNotFoundError):
        pass
    return env


def vercel_env_newline_issues(env: dict[str, str]) -> list[str]:
    """Return names of NEXT_PUBLIC_POSTHOG_* vars in the pulled Vercel env
    whose value contains literal `\\n` / `\\r` after quote-stripping."""
    bad: list[str] = []
    for name, raw in env.items():
        if not name.startswith("NEXT_PUBLIC_POSTHOG"):
            continue
        v = raw
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if "\\n" in v or "\\r" in v or v.endswith("\\"):
            bad.append(name)
    return bad


def fetch_shipped_key(website: str, timeout: int = 10) -> str | None:
    """Fetch the production site and extract the phc_* token actually
    shipped to browsers. Checks the inline HTML plus first-load JS chunks,
    preferring chunks whose URL hints at PostHog and capping total probes
    at 25 to avoid runaway fetches. Returns None if no token ships, or on
    any network error."""
    if not website:
        return None
    base = website.rstrip("/")
    try:
        req = urllib.request.Request(
            base + "/",
            headers={"User-Agent": "check-analytics-wiring/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, UnicodeError, OSError):
        return None
    m = PHC_RE.search(html)
    if m:
        return m.group(0)
    # Check first-load JS chunks. Alphabetical sort can push the PostHog
    # bundle past a small cap on sites with many code-split chunks (bit
    # mk0r 2026-04-22: phc_ token landed at sorted-index 12, so a cap of
    # 12 intermittently missed it during redeploys). Prefer chunks whose
    # URL hints at PostHog, then probe the rest up to a higher cap.
    chunks = sorted(set(re.findall(r'/_next/static/chunks/[^"\'\s]+\.js', html)))
    hinted = [c for c in chunks if "posthog" in c.lower()]
    rest = [c for c in chunks if c not in hinted]
    ordered = hinted + rest
    for chunk in ordered[:25]:
        try:
            req = urllib.request.Request(
                base + chunk,
                headers={"User-Agent": "check-analytics-wiring/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, UnicodeError, OSError):
            continue
        m = PHC_RE.search(body)
        if m:
            return m.group(0)
    return None


def probe_key_alive(api_key: str, timeout: int = 8) -> bool | None:
    """POST to /decide with the token. True = accepted, False = rejected
    (dead or expired), None = network error or indeterminate response."""
    url = f"{POSTHOG_INGEST_BASE}/decide/?v=3"
    body = json.dumps({"api_key": api_key, "distinct_id": "wiring-check"})
    req = urllib.request.Request(
        url, data=body.encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            data = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        # /decide returns 401 for rejected tokens; treat anything in 4xx
        # that echoes an auth error as DEAD, network-level 5xx as unknown.
        if 400 <= e.code < 500:
            try:
                body_json = json.loads(e.read() or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                body_json = {}
            detail = str(body_json.get("detail", "")).lower()
            if e.code == 401 or "invalid" in detail or "expired" in detail:
                return False
        return None
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return None
    if isinstance(data, dict) and data.get("type") == "authentication_error":
        return False
    detail = str(data.get("detail", "")).lower() if isinstance(data, dict) else ""
    if "invalid" in detail or "expired" in detail:
        return False
    return True


def check_runtime(
    report: SiteReport,
    project: dict,
    token_map: dict[str, tuple[int, str]],
) -> None:
    """Populate runtime fields on `report` and append errors/warnings. Never
    raises; on any check the short-circuit path leaves `runtime_ran=True`
    but individual runtime fields as None."""
    report.runtime_ran = True

    # 3. .env.production literal-\n scan.
    env_prod = report.path / ".env.production"
    bad_vars = scan_env_file_for_literal_newline(env_prod)
    if bad_vars:
        report.violations.append(
            f".env.production values have literal \\n in: "
            f"{', '.join(bad_vars)}"
        )

    # 4. Vercel env pull + \n scan + missing-key check.
    uses_posthog = report.analytics_mount != "NONE"
    vercel_env = pull_vercel_env(report.path)
    if vercel_env is not None:
        bad_vercel = vercel_env_newline_issues(vercel_env)
        if bad_vercel:
            report.violations.append(
                f"Vercel production env values have literal \\n in: "
                f"{', '.join(bad_vercel)} (re-add with `printf '%s' ...`)"
            )
        if uses_posthog and "NEXT_PUBLIC_POSTHOG_KEY" not in vercel_env:
            report.violations.append(
                "Vercel production env is missing NEXT_PUBLIC_POSTHOG_KEY; "
                "site code uses posthog-js but no key is plumbed through."
            )

    # 5. Live-site check: what phc_ actually ships?
    website = project.get("website") or ""
    shipped = fetch_shipped_key(website) if website else None
    report.shipped_key = shipped
    if uses_posthog and website and not shipped:
        report.violations.append(
            f"Live site {website} ships no phc_* token anywhere in HTML or "
            f"first-load JS; PostHog SDK never loads."
        )

    # 6. Key-alive probe.
    if shipped:
        alive = probe_key_alive(shipped)
        report.key_is_alive = alive
        if alive is False:
            report.violations.append(
                f"Shipped key {shipped[:14]}... is rejected by PostHog /decide "
                f"(invalid or expired)."
            )

    # 7. Project-id mismatch check (requires provisioning-key success).
    ph_override = project.get("posthog") or {}
    cfg_pid_raw = ph_override.get("project_id")
    report.configured_project_id = (
        str(cfg_pid_raw) if cfg_pid_raw is not None else None
    )
    if shipped and token_map:
        mapped = token_map.get(shipped)
        if mapped is None:
            report.warnings.append(
                f"Shipped key {shipped[:14]}... does not belong to any "
                f"project visible under the provisioning key (wrong org?)."
            )
        else:
            shipped_pid, shipped_name = mapped
            report.shipped_project_id = shipped_pid
            cfg_pid = (report.configured_project_id or "").strip()
            if cfg_pid and cfg_pid != str(shipped_pid):
                report.violations.append(
                    f"Project mismatch: live key targets project {shipped_pid} "
                    f"({shipped_name}) but config.json says {cfg_pid}. "
                    f"Dashboard queries the wrong project."
                )
            elif not cfg_pid:
                report.warnings.append(
                    f"config.json has no posthog.project_id; live events are "
                    f"going to project {shipped_pid} ({shipped_name}). "
                    f"Add: \"posthog\": {{\"project_id\": \"{shipped_pid}\"}}"
                )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

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
    if report.runtime_ran:
        shipped = report.shipped_key or "(none)"
        alive = (
            "yes" if report.key_is_alive is True
            else "DEAD" if report.key_is_alive is False
            else "unknown"
        )
        lines.append(f"{bullet}runtime: shipped_key={shipped}  alive={alive}")
        if report.shipped_project_id is not None:
            lines.append(
                f"{bullet}runtime: shipped_project_id={report.shipped_project_id} "
                f"configured_project_id={report.configured_project_id or '(unset)'}"
            )
    for v in report.violations:
        lines.append(f"{bullet}violation: {v}")
    for w in report.warnings:
        lines.append(f"{bullet}warning: {w}")
    return "\n".join(lines)


def _runtime_col(report: SiteReport) -> tuple[str, str, str]:
    """Return (live, alive, pid-match) indicators for table rendering.
    `-` = not checked (runtime didn't run or not applicable),
    `ok` = pass, `fail` = detected problem, `?` = indeterminate."""
    if not report.runtime_ran:
        return ("-", "-", "-")
    live = "ok" if report.shipped_key else "fail"
    if report.key_is_alive is True:
        alive = "ok"
    elif report.key_is_alive is False:
        alive = "fail"
    else:
        alive = "-" if report.shipped_key is None else "?"
    cfg = (report.configured_project_id or "").strip()
    ship = report.shipped_project_id
    if ship is None:
        pid = "-"
    elif not cfg:
        pid = "?"
    elif cfg == str(ship):
        pid = "ok"
    else:
        pid = "fail"
    return (live, alive, pid)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit PostHog + @m13v/seo-components wiring across every "
            "project in config.json. Runs static + runtime checks by default."
        ),
    )
    parser.add_argument(
        "--static-only", action="store_true",
        help=(
            "Skip runtime checks (live fetch, vercel env pull, /decide). "
            "Use for pre-commit or offline CI."
        ),
    )
    parser.add_argument(
        "--only", metavar="NAME", default=None,
        help=(
            "Restrict runtime checks to one project by name "
            "(case-sensitive; faster for iteration)."
        ),
    )
    args = parser.parse_args()

    try:
        config = json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read config.json: {e}", file=sys.stderr)
        return 2
    projects = config.get("projects", []) or []

    reports: list[SiteReport] = []
    project_by_name: dict[str, dict] = {}
    skipped: list[str] = []

    for proj in projects:
        name = proj.get("name")
        project_by_name[name] = proj
        lp = proj.get("landing_pages") or {}
        repo = lp.get("repo") if isinstance(lp, dict) else None
        if not name or not repo:
            if name:
                skipped.append(f"{name} (no landing_pages.repo)")
            continue
        report = check_site(
            name,
            repo,
            required=required_from_config(proj),
            in_product_telemetry=bool(proj.get("in_product_telemetry")),
        )
        reports.append(report)

    # Runtime layer: default ON, opt out with --static-only.
    if not args.static_only:
        provisioning_key = _get_provisioning_key()
        token_map: dict[str, tuple[int, str]] = {}
        if provisioning_key:
            token_map = fetch_token_project_map(provisioning_key)
            if not token_map:
                print(
                    "warn: PostHog personal-API call returned no projects; "
                    "project_id mismatch check disabled this run.",
                    file=sys.stderr,
                )
        else:
            print(
                f"warn: keychain entry '{POSTHOG_PERSONAL_KEY_KEYCHAIN}' not "
                f"found or unreadable; project_id mismatch check disabled.",
                file=sys.stderr,
            )
        for report in reports:
            if not report.exists:
                continue
            if args.only and report.name != args.only:
                continue
            # Don't run runtime checks on sites that already failed the
            # mount/layout static check; we'd just produce noise.
            if report.analytics_mount == "NONE":
                continue
            project = project_by_name.get(report.name) or {}
            try:
                check_runtime(report, project, token_map)
            except Exception as e:  # noqa: BLE001 - runtime must never abort
                report.warnings.append(f"runtime check raised: {e!r}")

    # Detailed per-site blocks.
    for report in reports:
        print(format_report(report))
        print()

    # Compact summary table.
    show_runtime = not args.static_only
    header = (
        f"{'project':<22}  {'mount':<16}  {'win':<3}  "
        f"{'viol':<4}  {'warn':<4}"
    )
    if show_runtime:
        header += f"  {'live':<4}  {'alive':<5}  {'pid':<4}"
    print("=" * max(60, len(header)))
    print(header)
    print("-" * max(60, len(header)))
    failures = 0
    for report in reports:
        win = (
            "yes" if report.window_posthog_attached
            else ("no" if report.window_posthog_attached is False else "?")
        )
        row = (
            f"{report.name[:22]:<22}  "
            f"{report.analytics_mount[:16]:<16}  "
            f"{win:<3}  "
            f"{len(report.violations):<4}  "
            f"{len(report.warnings):<4}"
        )
        if show_runtime:
            live, alive, pid = _runtime_col(report)
            row += f"  {live:<4}  {alive:<5}  {pid:<4}"
        print(row)
        if not report.ok:
            failures += 1

    print("=" * max(60, len(header)))
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
