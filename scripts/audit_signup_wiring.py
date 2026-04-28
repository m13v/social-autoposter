#!/usr/bin/env python3
"""One-shot audit of email-signup wiring across every client website.

For each project in config.json with a landing_pages.repo, reports:
  - signup_routes:    POST routes under src/app/api/{newsletter,signup,subscribe}
  - audience_upsert:  source files that POST to api.resend.com/audiences/.../contacts
  - createNewsletterHandler:  whether the route uses the @seo/components factory
  - addToAudience: source files that import addToAudience helper
  - audience_env: whether RESEND_AUDIENCE_ID is in .env.local
  - newsletter_subscribed_capture: any source file that fires the canonical event
  - newsletter_signup_form: whether <NewsletterSignup> or /api/newsletter is used
  - audience_id (live): from .env.local; used to query Resend for actual contact count

Read-only. No edits. Prints a Markdown table.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((REPO_ROOT / "config.json").read_text())
SRC_EXT = (".tsx", ".jsx", ".ts", ".js")
SKIP_DIRS = {"node_modules", ".next", "dist", "build", ".turbo", ".git"}

CAPTURE_RE = re.compile(r"""\b(?:posthog|ph|client|analytics)\??\.capture\??\s*\(\s*['"]([^'"]+)['"]""")
LIBRARY_NEWSLETTER_MARKERS = ("NewsletterSignup", "/api/newsletter")
AUDIENCE_UPSERT_RE = re.compile(r"audiences/[^\"'`\s]+contacts|api\.resend\.com/audiences", re.I)
CREATE_NEWSLETTER_HANDLER_RE = re.compile(r"createNewsletterHandler\b")
ADD_TO_AUDIENCE_IMPORT_RE = re.compile(r"\baddToAudience\b")
RESEND_AUDIENCE_ENV_RE = re.compile(r"^RESEND_AUDIENCE_ID\s*=\s*(\S+)", re.M)


def iter_src(repo: Path):
    src = repo / "src"
    root = src if src.exists() else repo
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            for entry in d.iterdir():
                if entry.name in SKIP_DIRS:
                    continue
                if entry.is_dir():
                    stack.append(entry)
                elif entry.suffix in SRC_EXT:
                    yield entry
        except (PermissionError, FileNotFoundError):
            continue


def find_signup_routes(repo: Path) -> list[str]:
    candidates = []
    for sub in ("newsletter", "signup", "subscribe"):
        for ext in (".ts", ".js"):
            for variant in (f"src/app/api/{sub}/route{ext}", f"app/api/{sub}/route{ext}"):
                p = repo / variant
                if p.exists():
                    candidates.append(str(p.relative_to(repo)))
    return candidates


def env_audience_id(repo: Path) -> str | None:
    for fn in (".env.local", ".env.production", ".env"):
        p = repo / fn
        if not p.exists():
            continue
        m = RESEND_AUDIENCE_ENV_RE.search(p.read_text(errors="ignore"))
        if m:
            return m.group(1).strip().strip('"\'')
    return None


def keychain_secret(name: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["security", "find-generic-password", "-s", name, "-w"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or None
    except subprocess.CalledProcessError:
        return None


def resend_contact_count(api_key: str, audience_id: str) -> int | None:
    if not api_key or not audience_id:
        return None
    req = urllib.request.Request(
        f"https://api.resend.com/audiences/{audience_id}/contacts",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return len(data.get("data") or [])
    except Exception:
        return None


def audit_repo(name: str, repo: Path) -> dict:
    info = {
        "name": name,
        "repo_exists": repo.exists(),
        "signup_routes": [],
        "audience_upsert_files": [],
        "createNewsletterHandler_files": [],
        "addToAudience_files": [],
        "newsletter_form_files": [],
        "newsletter_subscribed_capture_files": [],
        "events_captured": set(),
        "env_audience_id": None,
        "live_contacts": None,
        "audience_name": None,
    }
    if not repo.exists():
        return info
    info["signup_routes"] = find_signup_routes(repo)
    info["env_audience_id"] = env_audience_id(repo)

    for f in iter_src(repo):
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        rel = str(f.relative_to(repo))
        for m in CAPTURE_RE.finditer(text):
            info["events_captured"].add(m.group(1))
        if AUDIENCE_UPSERT_RE.search(text):
            info["audience_upsert_files"].append(rel)
        if CREATE_NEWSLETTER_HANDLER_RE.search(text):
            info["createNewsletterHandler_files"].append(rel)
        if ADD_TO_AUDIENCE_IMPORT_RE.search(text):
            info["addToAudience_files"].append(rel)
        if any(marker in text for marker in LIBRARY_NEWSLETTER_MARKERS):
            info["newsletter_form_files"].append(rel)
        if "newsletter_subscribed" in text:
            info["newsletter_subscribed_capture_files"].append(rel)

    return info


def main():
    api_key = keychain_secret("resend-mk0r-users")
    rows = []
    for proj in CONFIG.get("projects", []):
        lp = proj.get("landing_pages") or {}
        repo_str = lp.get("repo")
        if not repo_str:
            continue
        repo = Path(os.path.expanduser(repo_str))
        info = audit_repo(proj.get("name", "?"), repo)
        if info["env_audience_id"] and api_key:
            info["live_contacts"] = resend_contact_count(api_key, info["env_audience_id"])
            try:
                req = urllib.request.Request(
                    f"https://api.resend.com/audiences/{info['env_audience_id']}",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                info["audience_name"] = data.get("name")
            except Exception:
                pass
        rows.append(info)

    # Markdown table
    headers = [
        "name",
        "signup_routes",
        "newsletter_handler",
        "audience_upsert",
        "newsletter_subscribed_event",
        "newsletter_form",
        "RESEND_AUDIENCE_ID",
        "audience_name",
        "live_contacts",
    ]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    for r in rows:
        if not r["repo_exists"]:
            print(f"| {r['name']} | _repo missing_ |  |  |  |  |  |  |  |")
            continue
        sr = ", ".join(r["signup_routes"]) or "-"
        nh = "yes" if r["createNewsletterHandler_files"] else "no"
        au = (
            "yes"
            if (r["audience_upsert_files"] or r["addToAudience_files"] or r["createNewsletterHandler_files"])
            else "NO"
        )
        ns = "yes" if r["newsletter_subscribed_capture_files"] else "NO"
        nf = "yes" if r["newsletter_form_files"] else "no"
        ai = r["env_audience_id"] or "-"
        an = r["audience_name"] or "-"
        lc = "?" if r["live_contacts"] is None else str(r["live_contacts"])
        print(
            f"| {r['name']} | {sr} | {nh} | {au} | {ns} | {nf} | {ai[:8]+'...' if ai != '-' else '-'} | {an} | {lc} |"
        )


if __name__ == "__main__":
    main()
