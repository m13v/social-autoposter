#!/usr/bin/env python3
"""Per-DM short link minting + resolution for outbound link tracking.

All outbound URLs in the DM-replies pipeline get wrapped through this tool so
clicks attribute to the originating DM. Booking links, GitHub repos, our own
website pages, third-party references — every URL we send goes through /r/<code>.

Subcommands:

  mint --dm-id N --target-url URL
      Idempotent on (dm_id, target_url). Returns a wrapped URL like
      https://<target_project_website>/r/<code>. Refuses if URL points at a
      project not in dms.target_projects[]; the caller must call
      `dm_conversation.py set-target-project --append --project NAME` first.
      Auto-stamps dms.booking_link_sent_at for kind='booking'.

  resolve --code CODE
      Used by the public /api/short-links/<code> endpoint. Bumps clicks,
      stamps first/last click timestamps, inserts a synthetic [CLICK_SIGNAL]
      row in dm_messages so the engage pipeline picks the thread up. Returns
      target_url + dm_id + project + platform.

  wrap-text --dm-id N --text "..."
      Find every URL in the text, mint each via the same path, substring-replace
      the original URLs with the wrapped versions. Prints the wrapped text on
      stdout. Used by reddit_browser.py / twitter_browser.py (via direct import
      of `wrap_text()`) and by the LinkedIn shell flow (subprocess).

The classifier maps a URL to (kind, matched_project_name) using config.json:
  - booking : URL starts with project.booking_link
  - github  : URL starts with project.github or matches project.landing_pages.github_repo
  - website : URL host == project.website host
  - other   : no project match (no project guard, kind='other')

Wrapped hostname is always the DM's primary `target_project.website` (consistent
per thread regardless of which project a given link points at).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_DIR, 'scripts'))

import db as dbmod  # noqa: E402

CONFIG_PATH = os.path.join(REPO_DIR, 'config.json')
CODE_ALPHABET = 'abcdefghijkmnpqrstuvwxyz23456789'
CODE_LEN = 8

# Match http(s) URLs in arbitrary text. Greedy on the path; the trailing
# punctuation strip below handles "...github.com/foo/bar.")
_URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
_TRAILING_PUNCT = '.,;:!?)]}>\'"'


def _load_projects():
    with open(CONFIG_PATH, 'r') as f:
        return [p for p in json.load(f).get('projects', []) if p.get('name')]


def _gen_code(n=CODE_LEN):
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(n))


def _norm_host(url: str) -> str:
    try:
        return (urlsplit(url).netloc or '').lower().lstrip('www.')
    except Exception:
        return ''


def _classify_url(url: str, projects: list) -> tuple[str, str | None]:
    """Return (kind, project_name|None). Longest-prefix-wins across projects.

    Priority: booking > github > website > other. Ties within a kind go to the
    longest matching prefix so e.g. cal.com/team/mediar/fazm beats a hypothetical
    cal.com/team/mediar/ root.
    """
    u = url.strip()
    best_booking = ('', None)
    best_github = ('', None)
    best_website = ('', None)

    for p in projects:
        name = p.get('name')
        if not name:
            continue

        booking = (p.get('booking_link') or '').strip()
        if booking and u.startswith(booking.rstrip('?').rstrip('/')):
            if len(booking) > len(best_booking[0]):
                best_booking = (booking, name)

        gh = (p.get('github') or '').strip()
        if gh and u.startswith(gh.rstrip('/')):
            if len(gh) > len(best_github[0]):
                best_github = (gh, name)

        gh_repo = (p.get('landing_pages', {}) or {}).get('github_repo')
        if gh_repo:
            gh_url = f'https://github.com/{gh_repo.strip("/")}'
            if u.startswith(gh_url):
                if len(gh_url) > len(best_github[0]):
                    best_github = (gh_url, name)

        website = (p.get('website') or '').strip()
        if website:
            site_host = _norm_host(website)
            url_host = _norm_host(u)
            if site_host and url_host and (url_host == site_host or url_host.endswith('.' + site_host)):
                if len(site_host) > len(best_website[0]):
                    best_website = (site_host, name)

    if best_booking[1]:
        return ('booking', best_booking[1])
    if best_github[1]:
        return ('github', best_github[1])
    if best_website[1]:
        return ('website', best_website[1])
    return ('other', None)


def _build_target_url(target_url: str, kind: str, *, dm_id: int, project: str | None, platform: str) -> str:
    """Add UTM params for kinds where we control the analytics consumer.

    Booking: Cal.com metadata[utm_*] survives to the booking webhook (the flat
    utm_* gets stripped by Cal's UI), Calendly accepts both — keep both.
    Website: our own domains run PostHog; flat utm_* is enough.
    Github / other: leave the URL untouched (no downstream UTM consumer).
    """
    if kind not in ('booking', 'website'):
        return target_url

    parts = urlsplit(target_url)
    existing = dict(parse_qsl(parts.query, keep_blank_values=True))

    utm = {
        'utm_source': platform,           # reddit | twitter | linkedin
        'utm_medium': 'dm',
        'utm_campaign': (project or 'unknown').lower(),
        'utm_content': f'dm_{dm_id}',
    }
    for k, v in utm.items():
        existing.setdefault(k, v)
        if kind == 'booking':
            existing[f'metadata[{k}]'] = v

    new_query = urlencode(existing, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def _project_website(projects: list, name: str) -> str | None:
    for p in projects:
        if p.get('name') == name:
            site = (p.get('website') or '').strip().rstrip('/')
            return site or None
    return None


def _dm_row(conn, dm_id: int):
    cur = conn.execute(
        "SELECT id, platform, target_project, target_projects, project_name, "
        "       booking_link_sent_at "
        "FROM dms WHERE id = %s",
        (dm_id,),
    )
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"DM #{dm_id} not found")
    return dict(row)


def _existing_link(conn, dm_id: int, target_url: str):
    cur = conn.execute(
        "SELECT code, target_url, kind FROM dm_links "
        "WHERE dm_id = %s AND target_url = %s",
        (dm_id, target_url),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _mint_one(conn, *, dm_id: int, target_url: str, projects: list, projects_by_name: dict,
              dm: dict) -> dict:
    """Core mint logic, shared by `mint` CLI and `wrap_text` library call.

    Returns one of:
      {ok: True, code, short_url, target_url, kind, project, reused: bool}
      {ok: False, error: "target_project_required", needed_project, url}
      {ok: False, error: "no_primary_website", dm_id}
    """
    target_url = (target_url or '').strip()
    if not target_url:
        return {'ok': False, 'error': 'empty_url'}

    platform = (dm.get('platform') or 'reddit').lower()
    if platform == 'x':
        platform = 'twitter'

    kind, matched_project = _classify_url(target_url, projects)

    # Target-project guard: if the URL maps to one of our projects, that project
    # must already be in the DM's target_projects[]. The caller is expected to
    # call set-target-project --append before retry. kind='other' bypasses.
    target_projects = dm.get('target_projects') or []
    if matched_project and matched_project not in target_projects:
        return {
            'ok': False,
            'error': 'target_project_required',
            'needed_project': matched_project,
            'url': target_url,
            'kind': kind,
        }

    # Wrapped hostname: use the DM's primary target_project website. Falls back
    # to the matched_project's website if target_project is unset (rare, only on
    # very fresh rows where set-project hasn't fired yet).
    primary = dm.get('target_project') or (matched_project if matched_project else None)
    website = _project_website(projects, primary) if primary else None
    if not website:
        return {
            'ok': False,
            'error': 'no_primary_website',
            'dm_id': dm_id,
            'detail': f"no website for project={primary!r}; set target_project first",
        }

    final_target = _build_target_url(
        target_url,
        kind,
        dm_id=dm_id,
        project=matched_project,
        platform=platform,
    )

    # Idempotent: lookup against the FINAL target_url (post-UTM) since that's
    # what the unique index (dm_id, target_url) is on. Looking up the bare URL
    # would miss when a prior mint stored the UTM-stamped form.
    existing = _existing_link(conn, dm_id, final_target)
    if not existing and final_target != target_url:
        # Also check the bare URL form, so a re-wrap that was minted before
        # we started UTM-stamping a given kind still resolves to the same row.
        existing = _existing_link(conn, dm_id, target_url)

    if existing:
        code = existing['code']
        # Refresh target_url in case UTM/booking_link updated since first mint.
        conn.execute(
            "UPDATE dm_links SET target_url = %s WHERE code = %s",
            (final_target, code),
        )
        conn.commit()
        return {
            'ok': True,
            'code': code,
            'short_url': f"{website}/r/{code}",
            'target_url': final_target,
            'kind': existing.get('kind') or kind,
            'project': matched_project,
            'reused': True,
        }

    for _ in range(8):
        code = _gen_code()
        try:
            conn.execute(
                "INSERT INTO dm_links (code, dm_id, target_url, kind, project_at_mint) "
                "VALUES (%s, %s, %s, %s, %s)",
                (code, dm_id, final_target, kind, matched_project),
            )
            conn.commit()
            break
        except Exception as e:
            # Code collision (PK) → retry with a new code. Other errors → bail.
            if 'duplicate key' in str(e).lower() and 'dm_links_pkey' in str(e).lower():
                conn.execute("ROLLBACK")
                continue
            # Unique (dm_id, target_url) collision: another mint raced us. Re-read.
            if 'uq_dm_links_dm_target' in str(e).lower():
                conn.execute("ROLLBACK")
                existing2 = _existing_link(conn, dm_id, target_url)
                if existing2:
                    return {
                        'ok': True,
                        'code': existing2['code'],
                        'short_url': f"{website}/r/{existing2['code']}",
                        'target_url': existing2['target_url'],
                        'kind': existing2.get('kind') or kind,
                        'project': matched_project,
                        'reused': True,
                    }
            raise
    else:
        return {'ok': False, 'error': 'code_collision_after_8_tries'}

    # Auto-stamp booking_link_sent_at on first booking-kind wrap. The legacy
    # mark-booking-sent CLI is still supported but becomes a no-op when this
    # path already stamped the timestamp.
    if kind == 'booking' and not dm.get('booking_link_sent_at'):
        conn.execute(
            "UPDATE dms SET booking_link_sent_at = NOW() WHERE id = %s "
            "AND booking_link_sent_at IS NULL",
            (dm_id,),
        )
        conn.commit()

    return {
        'ok': True,
        'code': code,
        'short_url': f"{website}/r/{code}",
        'target_url': final_target,
        'kind': kind,
        'project': matched_project,
        'reused': False,
    }


# ---- Library entry point used by reddit_browser.py / twitter_browser.py ----

def wrap_text(*, dm_id: int, text: str) -> dict:
    """Find every URL in `text`, mint each, substring-replace.

    Returns:
      {ok: True, text: "<wrapped>", minted_codes: [...], skipped: [...]}
      {ok: False, error: "...", url: "...", needed_project: "..." }

    On a target_project_required error, the caller should set-target-project
    --append the needed_project and retry. We DO NOT silently fall through —
    refusing here is the whole point of the multi-project guard.
    """
    if not text:
        return {'ok': True, 'text': text, 'minted_codes': [], 'skipped': []}

    projects = _load_projects()
    projects_by_name = {p['name']: p for p in projects}
    conn = dbmod.get_conn()
    try:
        dm = _dm_row(conn, dm_id)
        seen = {}  # original_url -> wrapped_url (dedup so identical URLs map once)
        minted_codes = []
        skipped = []

        # Iterate matches in order, replace each. Trailing punctuation common in
        # prose ("...github.com/foo.") is stripped from the URL before classify.
        for m in list(_URL_RE.finditer(text)):
            raw = m.group(0)
            stripped = raw.rstrip(_TRAILING_PUNCT)
            trailing = raw[len(stripped):]
            if stripped in seen:
                continue

            # If the URL is already a wrapped /r/<code> on one of our domains,
            # leave it alone. Recognized by path shape /r/<8 chars from alphabet>.
            if re.search(r'/r/[a-z0-9]{4,32}(?:[/?#]|$)', stripped, re.IGNORECASE):
                seen[stripped] = stripped
                skipped.append({'url': stripped, 'reason': 'already_wrapped'})
                continue

            res = _mint_one(
                conn,
                dm_id=dm_id,
                target_url=stripped,
                projects=projects,
                projects_by_name=projects_by_name,
                dm=dm,
            )
            if not res.get('ok'):
                return {**res, 'ok': False}
            seen[stripped] = res['short_url']
            if not res.get('reused'):
                minted_codes.append(res['code'])
            elif res.get('code'):
                # Reused codes still surfaced so callers can backfill message_id.
                minted_codes.append(res['code'])

        if not seen:
            return {'ok': True, 'text': text, 'minted_codes': [], 'skipped': skipped}

        # Re-walk the text and substitute. Use the regex again to preserve
        # trailing punctuation outside the URL (we stripped it before classify).
        def _sub(m):
            raw = m.group(0)
            stripped = raw.rstrip(_TRAILING_PUNCT)
            trailing = raw[len(stripped):]
            wrapped = seen.get(stripped, stripped)
            return wrapped + trailing

        new_text = _URL_RE.sub(_sub, text)
        return {
            'ok': True,
            'text': new_text,
            'minted_codes': minted_codes,
            'skipped': skipped,
        }
    finally:
        conn.close()


# ---- CLI subcommands ----

def cmd_mint(args):
    projects = _load_projects()
    projects_by_name = {p['name']: p for p in projects}
    conn = dbmod.get_conn()
    try:
        dm = _dm_row(conn, args.dm_id)
        res = _mint_one(
            conn,
            dm_id=args.dm_id,
            target_url=args.target_url,
            projects=projects,
            projects_by_name=projects_by_name,
            dm=dm,
        )
        if not res.get('ok'):
            sys.stderr.write(json.dumps(res) + '\n')
            sys.exit(2)
        if args.json:
            print(json.dumps(res))
        else:
            print(res['short_url'])
    finally:
        conn.close()


def cmd_resolve(args):
    conn = dbmod.get_conn()
    try:
        cur = conn.execute(
            "SELECT l.code, l.dm_id, l.target_url, l.kind, "
            "       d.platform, d.target_project, d.project_name "
            "FROM dm_links l JOIN dms d ON d.id = l.dm_id "
            "WHERE l.code = %s",
            (args.code,),
        )
        row = cur.fetchone()
        if not row:
            print(json.dumps({'error': 'not_found', 'code': args.code}))
            return
        row = dict(row)
        platform = (row.get('platform') or 'reddit').lower()
        if platform == 'x':
            platform = 'twitter'

        if not args.no_count:
            conn.execute(
                "UPDATE dm_links SET "
                "  clicks = clicks + 1, "
                "  first_click_at = COALESCE(first_click_at, NOW()), "
                "  last_click_at = NOW() "
                "WHERE code = %s",
                (args.code,),
            )
            try:
                conn.execute(
                    "INSERT INTO dm_messages (dm_id, direction, author, content, message_at, logged_at) "
                    "VALUES (%s, 'inbound', '__click_signal__', "
                    "        '[CLICK_SIGNAL] short link clicked', NOW(), NOW())",
                    (row['dm_id'],),
                )
            except Exception as e:
                sys.stderr.write(f"[dm_short_links] click_signal insert failed (non-fatal): {e}\n")
            conn.commit()

        print(json.dumps({
            'dm_id': row['dm_id'],
            'platform': platform,
            'project': row.get('target_project') or row.get('project_name'),
            'kind': row.get('kind'),
            'target_url': row['target_url'],
        }))
    finally:
        conn.close()


def cmd_wrap_text(args):
    res = wrap_text(dm_id=args.dm_id, text=args.text)
    if not res.get('ok'):
        sys.stderr.write(json.dumps(res) + '\n')
        sys.exit(2)
    if args.json:
        print(json.dumps(res))
    else:
        # Stdout is the wrapped text only — ready to pipe into a `send` command
        # or a shell variable. Diagnostics go to stderr.
        if res.get('minted_codes') or res.get('skipped'):
            sys.stderr.write(json.dumps({
                'minted_codes': res['minted_codes'],
                'skipped': res['skipped'],
            }) + '\n')
        sys.stdout.write(res['text'])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p_mint = sub.add_parser('mint', help='Mint (or reuse) a wrapped /r/<code> short link for one URL')
    p_mint.add_argument('--dm-id', type=int, required=True)
    p_mint.add_argument('--target-url', required=True)
    p_mint.add_argument('--json', action='store_true', help='Print full JSON envelope')

    p_res = sub.add_parser('resolve', help='Look up code, increment clicks, return target URL')
    p_res.add_argument('--code', required=True)
    p_res.add_argument('--no-count', action='store_true', help='Skip click counter update (debugging)')

    p_wrap = sub.add_parser('wrap-text', help='Wrap every URL in TEXT through the mint pipeline')
    p_wrap.add_argument('--dm-id', type=int, required=True)
    p_wrap.add_argument('--text', required=True)
    p_wrap.add_argument('--json', action='store_true', help='Print full JSON envelope to stdout')

    args = ap.parse_args()
    if args.cmd == 'mint':
        cmd_mint(args)
    elif args.cmd == 'resolve':
        cmd_resolve(args)
    elif args.cmd == 'wrap-text':
        cmd_wrap_text(args)


if __name__ == '__main__':
    main()
