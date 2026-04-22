#!/usr/bin/env python3
"""
Scrape latest production deploy state for every project in config.json that
has a public website, and write the result to skill/cache/deploy_status.json.

Invoked every 5 min by launchd (com.m13v.social-deploy-status). Served by
bin/server.js at /api/deploy/status. Also safe to run ad-hoc:

    python3 scripts/project_deploy_status.py
"""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

REPO = Path(__file__).resolve().parent.parent

_RUN_START = time.time()


def _emit_run_log() -> None:
    elapsed = max(0, int(time.time() - _RUN_START))
    subprocess.run(
        [
            "python3", str(REPO / "scripts" / "log_run.py"),
            "--script", "deploy_status",
            "--posted", "0", "--skipped", "0", "--failed", "0",
            "--cost", "0", "--elapsed", str(elapsed),
        ],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


atexit.register(_emit_run_log)
CONFIG_PATH = REPO / "config.json"
CACHE_DIR = REPO / "skill" / "cache"
OUTPUT = CACHE_DIR / "deploy_status.json"
AUTH_JSON = Path.home() / "Library" / "Application Support" / "com.vercel.cli" / "auth.json"
API = "https://api.vercel.com"
UA = "social-autoposter-deploy-status/1.0"


def load_token() -> str:
    env = os.environ.get("VERCEL_TOKEN")
    if env:
        return env.strip()
    with AUTH_JSON.open() as f:
        return json.load(f)["token"]


def api_get(path: str, token: str) -> dict:
    req = Request(f"{API}{path}", headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": UA,
    })
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_teams(token: str) -> list[dict]:
    data = api_get("/v2/teams?limit=50", token)
    return data.get("teams", [])


def list_projects_for_team(token: str, team_id: str) -> list[dict]:
    out: list[dict] = []
    cursor: Optional[str] = None
    for _ in range(20):  # up to 2000 projects
        path = f"/v9/projects?teamId={team_id}&limit=100"
        if cursor:
            path += f"&until={cursor}"
        data = api_get(path, token)
        out.extend(data.get("projects", []))
        pag = data.get("pagination") or {}
        cursor = pag.get("next")
        if not cursor:
            break
    return out


def build_domain_map(token: str) -> dict[str, dict]:
    """host -> {team_id, team_slug, project_id, project_name}"""
    mapping: dict[str, dict] = {}
    for team in list_teams(token):
        tid, tslug = team["id"], team["slug"]
        try:
            projects = list_projects_for_team(token, tid)
        except Exception as e:
            print(f"[warn] listing projects for {tslug} failed: {e}", file=sys.stderr)
            continue
        for p in projects:
            entry = {
                "team_id": tid,
                "team_slug": tslug,
                "project_id": p["id"],
                "project_name": p["name"],
            }
            # Harvest alias domains from all deploy targets.
            targets = p.get("targets") or {}
            for env_name, env_data in targets.items():
                for alias in (env_data or {}).get("alias", []) or []:
                    if alias:
                        mapping.setdefault(alias.lower(), entry)
            # Also register the canonical "<name>-<team>.vercel.app" so we can
            # look up by project name if config.json uses that.
            mapping.setdefault(f"__project__:{p['name']}", entry)
    return mapping


def latest_production_deploy(token: str, entry: dict) -> Optional[dict]:
    path = (f"/v6/deployments?teamId={entry['team_id']}"
            f"&projectId={entry['project_id']}"
            f"&target=production&limit=1")
    data = api_get(path, token)
    deploys = data.get("deployments") or []
    return deploys[0] if deploys else None


def host_from_website(website: str) -> Optional[str]:
    if not website:
        return None
    parsed = urlparse(website if "://" in website else "https://" + website)
    return (parsed.netloc or parsed.path or "").lower().strip("/") or None


def summarize(deploy: dict, entry: dict) -> dict:
    created = deploy.get("created") or 0
    sha = (deploy.get("meta") or {}).get("githubCommitSha") or ""
    msg = (deploy.get("meta") or {}).get("githubCommitMessage") or ""
    return {
        "state": deploy.get("state") or deploy.get("readyState") or "UNKNOWN",
        "ready_substate": deploy.get("readySubstate"),
        "deploy_id": deploy.get("uid"),
        "created_ms": created,
        "age_sec": int(max(0, (time.time() * 1000 - created) / 1000)) if created else None,
        "commit_sha": sha[:7] if sha else None,
        "commit_message": (msg.splitlines()[0][:140] if msg else None),
        "deploy_url": f"https://{deploy['url']}" if deploy.get("url") else None,
        "inspector_url": deploy.get("inspectorUrl"),
        "project_id": entry["project_id"],
        "project_name": entry["project_name"],
        "team_slug": entry["team_slug"],
    }


def resolve_entry(host: Optional[str], project_name_hint: Optional[str], domain_map: dict[str, dict]) -> Optional[dict]:
    if host and host in domain_map:
        return domain_map[host]
    if host and host.startswith("www.") and host[4:] in domain_map:
        return domain_map[host[4:]]
    if project_name_hint:
        key = f"__project__:{project_name_hint}"
        if key in domain_map:
            return domain_map[key]
    return None


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    projects = cfg.get("projects") or []

    try:
        token = load_token()
    except Exception as e:
        print(f"[error] cannot load Vercel token: {e}", file=sys.stderr)
        return 2

    try:
        domain_map = build_domain_map(token)
    except Exception as e:
        print(f"[error] Vercel project listing failed: {e}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    for proj in projects:
        name = proj.get("name") or ""
        website = proj.get("website") or ""
        host = host_from_website(website)
        lp = proj.get("landing_pages") or {}
        repo = lp.get("repo") or ""
        repo_base = Path(repo).name if repo else None

        # Try by website host first, then by repo basename (e.g., fazm-website),
        # then by explicit vercel_project override if someone sets it.
        override = proj.get("vercel_project")
        entry = None
        for hint in (None, repo_base, override):
            entry = resolve_entry(host, hint, domain_map)
            if entry:
                break

        row: dict = {
            "name": name,
            "website": website,
            "host": host,
            "repo_base": repo_base,
        }

        if not entry:
            if not host and not repo_base:
                continue  # internal / no-landing-page project
            row["state"] = "UNMATCHED"
            row["error"] = "no vercel project mapped to this website or repo"
            rows.append(row)
            continue

        try:
            deploy = latest_production_deploy(token, entry)
        except HTTPError as e:
            row["state"] = "API_ERROR"
            row["error"] = f"HTTP {e.code}"
            row["project_name"] = entry["project_name"]
            rows.append(row)
            continue
        except (URLError, Exception) as e:
            row["state"] = "API_ERROR"
            row["error"] = str(e)[:200]
            rows.append(row)
            continue

        if deploy is None:
            row["state"] = "NO_DEPLOY"
            row["project_name"] = entry["project_name"]
            rows.append(row)
            continue

        row.update(summarize(deploy, entry))
        rows.append(row)

    # Sort errored first so the dashboard surface is meaningful.
    priority = {"ERROR": 0, "CANCELED": 1, "API_ERROR": 2, "UNMATCHED": 3,
                "BUILDING": 4, "QUEUED": 5, "INITIALIZING": 6, "READY": 7}
    rows.sort(key=lambda r: (priority.get(r.get("state") or "READY", 8), r.get("name") or ""))

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_ms": int(time.time() * 1000),
        "projects": rows,
        "counts": {
            "total": len(rows),
            "error": sum(1 for r in rows if r.get("state") == "ERROR"),
            "ready": sum(1 for r in rows if r.get("state") == "READY"),
            "building": sum(1 for r in rows if r.get("state") in ("BUILDING", "QUEUED", "INITIALIZING")),
            "unmatched": sum(1 for r in rows if r.get("state") == "UNMATCHED"),
        },
    }

    tmp = OUTPUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(OUTPUT)

    if os.isatty(sys.stdout.fileno()) or os.environ.get("DEPLOY_STATUS_VERBOSE"):
        for r in rows:
            state = r.get("state", "?")
            marker = "X" if state == "ERROR" else "." if state == "READY" else "~"
            print(f"  {marker} {state:<10} {r.get('name',''):<20} {r.get('commit_sha') or '':<7} {r.get('host') or ''}")
        print(f"\nwrote {OUTPUT} ({payload['counts']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
