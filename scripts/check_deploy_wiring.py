#!/usr/bin/env python3
"""
Audit deploy wiring for every Cloud Run client site registered in config.json.

Failure mode this catches: a project with `landing_pages.deploy.target ==
"cloudrun"` and `production_trigger == "push:main"` whose repo has no
`.github/workflows/deploy-cloudrun.yml`. That state means push-to-main does
nothing and the site only ever ships when someone runs `gcloud run deploy`
by hand. Also flags the inverse: a workflow exists but the label says
`manual` (mislabeled, dashboard lies).

Static checks only, no GCP/GitHub API calls. Reads:
  - ~/social-autoposter/config.json (projects[].landing_pages.deploy)
  - <repo>/.github/workflows/deploy-cloudrun.yml

Exit code: 1 if any cloudrun site has a wiring error; 0 otherwise.

Run from ~/social-autoposter:
  python3 scripts/check_deploy_wiring.py
  python3 scripts/check_deploy_wiring.py --only studyly
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"


@dataclass
class Finding:
    project: str
    level: str  # "error" | "warn" | "ok" | "skip"
    message: str


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def audit_project(project: dict) -> list[Finding]:
    name = project.get("name", "<unknown>")
    lp = project.get("landing_pages", {})
    deploy = lp.get("deploy", {})
    target = deploy.get("target")
    trigger = deploy.get("production_trigger")
    repo_path = lp.get("repo")

    if target != "cloudrun":
        return [Finding(name, "skip", f"target={target!r}, only cloudrun is audited")]

    if not repo_path:
        return [Finding(name, "error", "landing_pages.repo missing")]

    repo = expand(repo_path)
    if not repo.is_dir():
        return [Finding(name, "error", f"repo path does not exist: {repo}")]

    workflow = repo / ".github" / "workflows" / "deploy-cloudrun.yml"
    workflow_exists = workflow.is_file()
    workflow_text = workflow.read_text(encoding="utf-8", errors="replace") if workflow_exists else ""
    on_push_main = (
        "push:" in workflow_text
        and "branches: [main]" in workflow_text
    ) if workflow_exists else False

    findings: list[Finding] = []

    if trigger == "push:main" and not workflow_exists:
        findings.append(Finding(
            name, "error",
            f"production_trigger=push:main but {workflow.relative_to(repo)} is missing in {repo}. "
            f"Push-to-main is a no-op; create the workflow per setup-client-website Phase 6f.",
        ))
    elif trigger == "push:main" and workflow_exists and not on_push_main:
        findings.append(Finding(
            name, "error",
            f"production_trigger=push:main but workflow {workflow} does not trigger on push to main.",
        ))
    elif trigger == "manual" and workflow_exists and on_push_main:
        findings.append(Finding(
            name, "warn",
            f"production_trigger=manual but workflow {workflow} actually deploys on push to main. "
            f"Update config.json deploy.production_trigger to 'push:main'.",
        ))
    elif trigger == "manual" and not workflow_exists:
        findings.append(Finding(
            name, "error",
            f"target=cloudrun + production_trigger=manual: no auto-deploy wired. "
            f"Create {workflow.relative_to(repo)} per setup-client-website Phase 6f, "
            f"then flip production_trigger to 'push:main'.",
        ))
    else:
        findings.append(Finding(name, "ok", f"trigger={trigger}, workflow={'present' if workflow_exists else 'absent'}"))

    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="audit only this project name")
    args = ap.parse_args()

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    projects = cfg.get("projects", [])
    if args.only:
        projects = [p for p in projects if p.get("name") == args.only]
        if not projects:
            print(f"no project named {args.only!r} in config.json", file=sys.stderr)
            return 2

    error_count = 0
    warn_count = 0
    print(f"{'PROJECT':24s} {'LEVEL':6s} MESSAGE")
    print("-" * 100)
    for p in projects:
        for f in audit_project(p):
            if f.level == "skip":
                continue
            print(f"{f.project:24s} {f.level.upper():6s} {f.message}")
            if f.level == "error":
                error_count += 1
            elif f.level == "warn":
                warn_count += 1

    print("-" * 100)
    print(f"{error_count} error(s), {warn_count} warning(s)")
    return 1 if error_count else 0


if __name__ == "__main__":
    sys.exit(main())
