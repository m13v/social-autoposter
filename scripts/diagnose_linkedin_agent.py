#!/usr/bin/env python3
"""
Diagnose active Chrome automation profiles for LinkedIn auth state.

Examples:
  python3 scripts/diagnose_linkedin_agent.py
  python3 scripts/diagnose_linkedin_agent.py --json
  python3 scripts/diagnose_linkedin_agent.py --backup-dir /tmp/linkedin-agent-backups
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


CHROME_CMD_RE = re.compile(r"/Applications/Google Chrome\.app/Contents/MacOS/Google Chrome(?:$| )")
PORT_RE = re.compile(r"--remote-debugging-port=(\d+)")
PROFILE_RE = re.compile(r"--user-data-dir=([^ ]+)")


def run(cmd: str) -> str:
    return subprocess.check_output(["zsh", "-lc", cmd], text=True)


def active_profiles() -> list[dict]:
    out = run("ps -axww -o pid=,args=")
    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for line in out.splitlines():
        if not CHROME_CMD_RE.search(line):
            continue
        port = PORT_RE.search(line)
        profile = PROFILE_RE.search(line)
        if not (port and profile):
            continue
        key = (port.group(1), profile.group(1))
        if key in seen:
            continue
        seen.add(key)
        rows.append({"port": port.group(1), "profile": profile.group(1)})
    return rows


def linkedin_cookies(profile_dir: str) -> list[dict]:
    cookies_db = Path(profile_dir) / "Default" / "Cookies"
    if not cookies_db.exists():
        return []

    tmp = Path(tempfile.mktemp(suffix=".db"))
    try:
        shutil.copy2(cookies_db, tmp)
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT host_key, name, path, is_secure, is_httponly,
                   expires_utc, LENGTH(value) AS value_len,
                   LENGTH(encrypted_value) AS enc_len
            FROM cookies
            WHERE host_key LIKE '%linkedin.com%'
            ORDER BY host_key, name
            """
        )
        rows = [
            {
                "host_key": host_key,
                "name": name,
                "path": path,
                "is_secure": bool(is_secure),
                "is_httponly": bool(is_httponly),
                "expires_utc": expires_utc,
                "value_len": value_len,
                "enc_len": enc_len,
            }
            for host_key, name, path, is_secure, is_httponly, expires_utc, value_len, enc_len in cur.fetchall()
        ]
        conn.close()
        return rows
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def find_linkedin_targets(port: str) -> list[dict]:
    try:
        raw = run(f"curl -sf 'http://127.0.0.1:{port}/json/list'")
        pages = json.loads(raw)
    except Exception:
        return []

    results = []
    for page in pages:
        url = page.get("url", "")
        title = page.get("title", "")
        if "linkedin.com" in url or "LinkedIn" in title:
            results.append(
                {
                    "id": page.get("id"),
                    "type": page.get("type"),
                    "title": title,
                    "url": url,
                }
            )
    return results


def backup_profile(profile_dir: str, backup_dir: str) -> str:
    src = Path(profile_dir)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = Path(backup_dir) / f"linkedin-agent-{ts}"
    dst.mkdir(parents=True, exist_ok=True)

    # Keep the backup small but sufficient to restore auth state for this profile.
    paths_to_copy = [
        src / "Local State",
        src / "Default" / "Cookies",
        src / "Default" / "Preferences",
    ]
    for path in paths_to_copy:
        if not path.exists():
            continue
        rel = path.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return str(dst)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument(
        "--backup-dir",
        help="If an authenticated LinkedIn agent profile is found, copy its auth files here",
    )
    args = parser.parse_args()

    report = []
    for row in active_profiles():
        cookies = linkedin_cookies(row["profile"])
        targets = find_linkedin_targets(row["port"])
        if not cookies and not targets:
            continue

        cookie_names = [c["name"] for c in cookies]
        has_li_at = "li_at" in cookie_names
        item = {
            "port": row["port"],
            "profile": row["profile"],
            "has_li_at": has_li_at,
            "cookie_names": cookie_names,
            "linkedin_targets": targets,
        }
        if has_li_at and args.backup_dir:
            item["backup_path"] = backup_profile(row["profile"], args.backup_dir)
        report.append(item)

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
        return 0

    if not report:
        print("No active Chrome automation profiles with LinkedIn state found.")
        return 0

    for item in report:
        print(f"port: {item['port']}")
        print(f"profile: {item['profile']}")
        print(f"has_li_at: {item['has_li_at']}")
        print("cookie_names:", ", ".join(item["cookie_names"]) if item["cookie_names"] else "(none)")
        if item["linkedin_targets"]:
            print("linkedin_targets:")
            for target in item["linkedin_targets"]:
                print(f"  - [{target['type']}] {target['title']} :: {target['url']}")
        if item.get("backup_path"):
            print(f"backup_path: {item['backup_path']}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
