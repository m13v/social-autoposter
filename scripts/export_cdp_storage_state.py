#!/usr/bin/env python3
"""
Export cookies from a live Chrome DevTools session into a Playwright storageState file.

Typical use for browser agents:
  python3 scripts/export_cdp_storage_state.py \
    --port 53716 \
    --out /Users/matthewdi/.claude/browser-sessions.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path
from urllib.request import urlopen

import websockets


def browser_ws_url(port: int) -> str:
    with urlopen(f"http://127.0.0.1:{port}/json/version") as resp:
        data = json.load(resp)
    url = data.get("webSocketDebuggerUrl")
    if not url:
        raise RuntimeError(f"No webSocketDebuggerUrl found on port {port}")
    return url


async def get_all_cookies(ws_url: str) -> list[dict]:
    async with websockets.connect(ws_url, max_size=50_000_000) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") != 1:
                continue
            result = msg.get("result", {})
            return result.get("cookies", [])


def cdp_cookie_to_playwright(cookie: dict) -> dict:
    out = {
        "name": cookie["name"],
        "value": cookie["value"],
        "domain": cookie["domain"],
        "path": cookie.get("path", "/"),
        "expires": cookie.get("expires", -1),
        "httpOnly": bool(cookie.get("httpOnly", False)),
        "secure": bool(cookie.get("secure", False)),
    }
    same_site = cookie.get("sameSite")
    if same_site in {"Strict", "Lax", "None"}:
        out["sameSite"] = same_site
    return out


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True, help="Chrome remote debugging port")
    parser.add_argument("--out", required=True, help="Output Playwright storageState JSON file")
    parser.add_argument("--backup", action="store_true", help="Backup existing output file first")
    parser.add_argument(
        "--require-cookie",
        action="append",
        default=[],
        help="Fail unless the exported cookies include this cookie name. Repeatable.",
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.backup and out_path.exists():
        backup = out_path.with_suffix(out_path.suffix + ".bak")
        shutil.copy2(out_path, backup)

    ws_url = browser_ws_url(args.port)
    cookies = await get_all_cookies(ws_url)
    names = {c.get("name") for c in cookies}
    missing = [name for name in args.require_cookie if name not in names]
    if missing:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "missing_required_cookies",
                    "missing": missing,
                    "cookie_count": len(cookies),
                },
                indent=2,
            )
        )
        return 1

    storage_state = {
        "cookies": [cdp_cookie_to_playwright(c) for c in cookies],
        "origins": [],
    }
    out_path.write_text(json.dumps(storage_state, indent=2))

    print(
        json.dumps(
            {
                "ok": True,
                "port": args.port,
                "out": str(out_path),
                "cookie_count": len(cookies),
                "cookie_names_sample": sorted(list(names))[:40],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
