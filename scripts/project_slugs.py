#!/usr/bin/env python3
"""Single source of truth for project -> client_slug + booking_table.

Derives both from ~/social-autoposter/config.json so no list of projects is
maintained in parallel across project_stats.py, pick_top_page.py,
pick_top_pages.py, or the cal webhook routing.

client_slug rule:
    project `name` lowercased with dashes/spaces stripped, unless the
    project explicitly defines a `client_slug` field. Matches every entry
    hard-coded historically (Cyrano->cyrano, PieLine->pieline, paperback-expert
    ->paperbackexpert, fde10x->fde10x, etc.).

booking_table rule:
    cal.com/*       -> cal_bookings
    calendly.com/*  -> calendly_bookings
    anything else / unset -> None (project does not attribute bookings)
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def _derive_slug(name: str) -> str:
    return name.lower().replace("-", "").replace(" ", "")


@lru_cache(maxsize=1)
def _projects() -> list[dict]:
    try:
        return json.loads(CONFIG_PATH.read_text()).get("projects", [])
    except (OSError, ValueError):
        return []


def _find(project_name: str) -> Optional[dict]:
    for p in _projects():
        if p.get("name") == project_name:
            return p
    return None


def get_client_slug(project_name: str) -> Optional[str]:
    """Return the client_slug used in cal_bookings / calendly_bookings for
    this project. Returns None if the project is not in config.json."""
    p = _find(project_name)
    if p is None:
        return None
    return p.get("client_slug") or _derive_slug(project_name)


def get_booking_table(project_name: str) -> Optional[str]:
    """Return 'cal_bookings', 'calendly_bookings', or None if the project has
    no booking link configured."""
    p = _find(project_name)
    if p is None:
        return None
    link = (p.get("booking_link") or "").lower()
    if "calendly.com" in link:
        return "calendly_bookings"
    if "cal.com" in link:
        return "cal_bookings"
    return None


if __name__ == "__main__":
    # Smoke-test: print the derivation for every project in config.json.
    for p in _projects():
        name = p.get("name", "")
        print(f"{name!r:<28} slug={get_client_slug(name)!r:<22} "
              f"table={get_booking_table(name)!r}")
