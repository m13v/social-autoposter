#!/usr/bin/env python3
"""Per-DM short link minting + resolution for booking attribution.

Mint:
  python3 scripts/dm_short_links.py mint --dm-id 1136
    -> https://aiphoneordering.com/r/k7m2pq9x

Resolve (used by the public /api/short-links/<code> endpoint and the client
website /r/[code] route):
  python3 scripts/dm_short_links.py resolve --code k7m2pq9x
    -> {"dm_id": 1136, "target_url": "https://cal.com/...?utm_content=dm_1136..."}

Behavior:
  - mint generates a fresh code, stores it on dms.short_link_code, idempotent
    (returns the existing code if already set for that DM).
  - resolve increments dms.short_link_clicks and stamps first/last click
    timestamps. Builds the Cal.com / Calendly URL on the fly from the matched
    project's config (project derived from target_project, fallback project_name).
  - Both query the central Neon DB (the same one that hosts dms).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_DIR, 'scripts'))

import db as dbmod  # noqa: E402

CONFIG_PATH = os.path.join(REPO_DIR, 'config.json')
CODE_ALPHABET = 'abcdefghijkmnpqrstuvwxyz23456789'
CODE_LEN = 8


def _load_projects():
    with open(CONFIG_PATH, 'r') as f:
        return {p['name']: p for p in json.load(f).get('projects', []) if p.get('name')}


def _gen_code(n=CODE_LEN):
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(n))


def _build_target_url(booking_link: str, *, dm_id: int, project: str, platform: str) -> str:
    """Return Cal.com / Calendly URL with both flat utm_* and metadata[utm_*].

    Cal.com strips most query params before its own UI; only metadata[*] survives
    to the booking webhook. Calendly accepts both forms; the bracketed pairs are
    harmless there.
    """
    parts = urlsplit(booking_link)
    existing = dict(parse_qsl(parts.query, keep_blank_values=True))

    # Slug per project — match the existing trackBookingClick convention so
    # cal_bookings rows from DM clicks share schema with on-site clicks.
    utm = {
        'utm_source': platform,           # reddit | twitter | linkedin
        'utm_medium': 'dm',
        'utm_campaign': project.lower(),  # e.g. "pieline"
        'utm_content': f'dm_{dm_id}',
    }
    for k, v in utm.items():
        existing.setdefault(k, v)
        existing[f'metadata[{k}]'] = v

    new_query = urlencode(existing, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def _project_for_dm(conn, dm_id: int):
    cur = conn.execute(
        "SELECT id, platform, target_project, project_name "
        "FROM dms WHERE id = %s",
        (dm_id,),
    )
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"DM #{dm_id} not found")
    return dict(row)


def cmd_mint(args):
    projects = _load_projects()
    conn = dbmod.get_conn()
    try:
        dm = _project_for_dm(conn, args.dm_id)
        platform = (dm.get('platform') or 'reddit').lower()
        if platform == 'x':
            platform = 'twitter'
        project_name = args.project or dm.get('target_project') or dm.get('project_name')
        if not project_name:
            raise SystemExit(
                f"DM #{args.dm_id} has no target_project/project_name set; "
                f"pass --project explicitly"
            )
        proj = projects.get(project_name)
        if not proj:
            raise SystemExit(f"Project {project_name!r} not in config.json")
        booking = proj.get('booking_link')
        if not booking:
            raise SystemExit(f"Project {project_name!r} has no booking_link in config.json")
        website = (proj.get('website') or '').rstrip('/')
        if not website:
            raise SystemExit(f"Project {project_name!r} has no website in config.json")

        cur = conn.execute(
            "SELECT short_link_code FROM dms WHERE id = %s",
            (args.dm_id,),
        )
        row = cur.fetchone()
        existing = (row or {}).get('short_link_code') if isinstance(row, dict) else (row[0] if row else None)
        if existing and not args.force:
            code = existing
        else:
            for _ in range(8):
                code = _gen_code()
                cur = conn.execute(
                    "UPDATE dms SET short_link_code = %s WHERE id = %s "
                    "AND (short_link_code IS NULL OR %s)",
                    (code, args.dm_id, args.force),
                )
                if cur.rowcount:
                    break
            else:
                raise SystemExit("Could not allocate a unique short_link_code after 8 tries")
            conn.commit()

        target = _build_target_url(
            booking,
            dm_id=args.dm_id,
            project=project_name,
            platform=platform,
        )
        short_url = f"{website}/r/{code}"
        if args.json:
            print(json.dumps({
                'code': code,
                'short_url': short_url,
                'target_url': target,
                'dm_id': args.dm_id,
                'project': project_name,
                'platform': platform,
            }))
        else:
            print(short_url)
    finally:
        conn.close()


def cmd_resolve(args):
    projects = _load_projects()
    conn = dbmod.get_conn()
    try:
        cur = conn.execute(
            "SELECT id, platform, target_project, project_name "
            "FROM dms WHERE short_link_code = %s",
            (args.code,),
        )
        dm = cur.fetchone()
        if not dm:
            print(json.dumps({'error': 'not_found', 'code': args.code}))
            sys.exit(1)
        dm = dict(dm)
        platform = (dm.get('platform') or 'reddit').lower()
        if platform == 'x':
            platform = 'twitter'
        project_name = dm.get('target_project') or dm.get('project_name')
        proj = projects.get(project_name) if project_name else None
        if not proj or not proj.get('booking_link'):
            print(json.dumps({'error': 'no_project', 'dm_id': dm['id']}))
            sys.exit(1)
        target = _build_target_url(
            proj['booking_link'],
            dm_id=dm['id'],
            project=project_name,
            platform=platform,
        )

        if not args.no_count:
            conn.execute(
                "UPDATE dms SET "
                "  short_link_clicks = short_link_clicks + 1, "
                "  short_link_first_click_at = COALESCE(short_link_first_click_at, NOW()), "
                "  short_link_last_click_at = NOW() "
                "WHERE id = %s",
                (dm['id'],),
            )
            conn.commit()

        print(json.dumps({
            'dm_id': dm['id'],
            'platform': platform,
            'project': project_name,
            'target_url': target,
        }))
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p_mint = sub.add_parser('mint', help='Generate (or fetch existing) short link for a DM')
    p_mint.add_argument('--dm-id', type=int, required=True)
    p_mint.add_argument('--project', help='Project name override (default: dms.target_project)')
    p_mint.add_argument('--force', action='store_true', help='Regenerate even if a code already exists')
    p_mint.add_argument('--json', action='store_true', help='Print full JSON envelope')

    p_res = sub.add_parser('resolve', help='Look up code, increment clicks, return target URL')
    p_res.add_argument('--code', required=True)
    p_res.add_argument('--no-count', action='store_true', help='Skip click counter update (debugging)')

    args = ap.parse_args()
    if args.cmd == 'mint':
        cmd_mint(args)
    elif args.cmd == 'resolve':
        cmd_resolve(args)


if __name__ == '__main__':
    main()
