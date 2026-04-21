#!/usr/bin/env python3
"""Pick the top-trafficked page for a project over the last 24h and build
a brief the improvement pipeline can hand to Claude.

The brief bundles:
  - full product config from config.json (so Claude has voice, positioning,
    qualification, proof points, pricing, etc. without re-deriving)
  - the winning page path + absolute URL
  - per-metric counts for three windows: 24h total, 7d average/day, 30d average/day
  - bookings count from cal_bookings keyed on client_slug
  - history of prior improvements on the same page

Emits the brief as a single JSON object to stdout. Exits non-zero with a
short reason on stderr when there is nothing to improve (no pageviews in
the last 24h, or product not configured). The orchestrator uses exit code
2 as "skip, no work".

Usage:
    python3 seo/pick_top_page.py --product PieLine
    python3 seo/pick_top_page.py --product Cyrano --out /tmp/brief.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
CONFIG_PATH = ROOT_DIR / "config.json"

ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import psycopg2  # noqa: E402


METRIC_EVENTS = {
    # PostHog event name(s) per funnel column. get_started has two legacy names
    # alongside the canonical one, mirroring project_stats_json.py.
    "pageviews":          ["$pageview"],
    "email_signups":      ["newsletter_subscribed"],
    "schedule_clicks":    ["schedule_click"],
    "get_started_clicks": ["get_started_click", "download_click", "cta_get_started_clicked"],
}

WINDOWS = (
    ("24h",  1),
    ("7d",   7),
    ("30d", 30),
)


def _load_config():
    return json.loads(CONFIG_PATH.read_text())


def _find_project(cfg, product):
    for p in cfg.get("projects", []):
        if (p.get("name") or "").lower() == (product or "").lower():
            return p
    return None


def _domain_from_url(url):
    if not url:
        return ""
    return url.replace("https://", "").replace("http://", "").rstrip("/")


def _posthog_api_key():
    # Prefer an explicit env var so CI / launchd can override. Fall back to the
    # personal key in keychain for interactive use.
    v = os.environ.get("POSTHOG_PERSONAL_API_KEY")
    if v:
        return v.strip()
    try:
        out = subprocess.check_output(
            ["security", "find-generic-password", "-s", "PostHog-Personal-API-Key-m13v", "-w"],
            stderr=subprocess.DEVNULL, timeout=10,
        )
        return out.decode().strip()
    except Exception:
        return ""


def _hogql(api_key, project_id, query, timeout=60):
    """Run a HogQL query. Simple retry; raise on permanent failure."""
    url = f"https://us.posthog.com/api/projects/{project_id}/query/"
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": query}}).encode("utf-8")
    last_err = None
    for attempt, wait in enumerate([0.0, 2.0, 5.0, 12.0]):
        if wait:
            time.sleep(wait)
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
                return data.get("results") or []
        except urllib.error.HTTPError as e:
            try:
                body_txt = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                body_txt = ""
            last_err = f"HTTP {e.code}: {body_txt}"
            if e.code not in (429,) and not (500 <= e.code < 600):
                break
        except urllib.error.URLError as e:
            last_err = f"URLError: {e}"
    raise RuntimeError(f"HogQL failed: {last_err}")


def _pick_top_path(api_key, project_id, domain):
    q = (
        "SELECT properties.$pathname AS path, count() AS n "
        "FROM events "
        "WHERE event = '$pageview' "
        f"AND properties.$host = '{domain}' "
        "AND timestamp >= now() - interval 24 hour "
        "AND properties.$pathname IS NOT NULL "
        "GROUP BY path ORDER BY n DESC LIMIT 1"
    )
    rows = _hogql(api_key, project_id, q)
    if not rows:
        return None, 0
    path = rows[0][0] or "/"
    views = int(rows[0][1] or 0)
    return path, views


def _metric_counts_for_path(api_key, project_id, domain, path, days):
    """Return {metric_name: total_count} over the last `days` days."""
    out = {}
    safe_domain = domain.replace("'", "")
    safe_path = path.replace("'", "")
    for metric, events in METRIC_EVENTS.items():
        if len(events) == 1:
            event_clause = f"event = '{events[0]}'"
        else:
            quoted = ",".join(f"'{e}'" for e in events)
            event_clause = f"event IN ({quoted})"
        q = (
            "SELECT count() FROM events "
            f"WHERE {event_clause} "
            f"AND properties.$host = '{safe_domain}' "
            f"AND properties.$pathname = '{safe_path}' "
            f"AND timestamp >= now() - interval {days} day"
        )
        rows = _hogql(api_key, project_id, q)
        out[metric] = int(rows[0][0]) if rows and rows[0] else 0
    return out


def _client_slug(product_name):
    return {
        "Cyrano": "cyrano",
        "PieLine": "pieline",
        "fazm": "fazm",
        "S4L": "s4l",
        "fde10x": "fde10x",
        "Assrt": "assrt",
    }.get(product_name)


def _bookings_counts(client_slug, days):
    """Count real (non-test) bookings in the window. Return None on any failure
    so the brief records 'unknown' rather than a misleading 0."""
    url = os.environ.get("BOOKINGS_DATABASE_URL")
    if not url or not client_slug:
        return None
    try:
        conn = psycopg2.connect(url)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FILTER (WHERE attendee_email NOT LIKE '%%test%%' "
            "AND attendee_email NOT LIKE '%%example%%' "
            "AND attendee_name NOT LIKE '%%TEST%%' "
            "AND attendee_name NOT LIKE '%%John Doe%%') "
            "FROM cal_bookings WHERE client_slug = %s "
            "AND created_at >= NOW() - INTERVAL '" + str(int(days)) + " days'",
            (client_slug,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return int(row[0] or 0)
    except Exception as e:
        print(f"  bookings query failed: {e}", file=sys.stderr)
        return None


def _history_for_page(product, page_path, limit=5):
    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        cur = conn.cursor()
        cur.execute(
            "SELECT created_at, status, diff_summary, rationale, commit_sha "
            "FROM seo_page_improvements "
            "WHERE product = %s AND page_path = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (product, page_path, limit),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            {
                "at": r[0].isoformat() if r[0] else None,
                "status": r[1],
                "diff_summary": r[2],
                "rationale": r[3],
                "commit_sha": r[4],
            }
            for r in rows
        ]
    except Exception as e:
        print(f"  history lookup failed: {e}", file=sys.stderr)
        return []


def _build_metrics(api_key, project_id, domain, path, client_slug):
    # 24h = totals, 7d/30d are expressed as per-day averages so the magnitudes
    # on the dashboard are comparable across windows.
    metrics = {}
    for label, days in WINDOWS:
        raw = _metric_counts_for_path(api_key, project_id, domain, path, days)
        bookings = _bookings_counts(client_slug, days)
        if label == "24h":
            metrics["24h"] = {**raw, "bookings": bookings, "window_days": 1}
        else:
            per_day = {k: round(v / days, 3) for k, v in raw.items()}
            per_day["bookings"] = round(bookings / days, 3) if bookings is not None else None
            metrics[f"{label}_avg_per_day"] = {
                **per_day,
                "window_days": days,
                "totals": {**raw, "bookings": bookings},
            }
    return metrics


def build_brief(product):
    cfg = _load_config()
    proj = _find_project(cfg, product)
    if not proj:
        raise SystemExit(f"ERROR: product '{product}' not found in config.json")

    website = proj.get("website") or ""
    domain = _domain_from_url(website)
    if not domain:
        raise SystemExit(f"ERROR: product '{product}' has no website domain")

    lp = proj.get("landing_pages") or {}
    repo_raw = lp.get("repo") or ""
    repo_abs = os.path.expanduser(repo_raw)
    if not repo_abs or not os.path.isdir(repo_abs):
        raise SystemExit(f"ERROR: repo path missing for '{product}': {repo_raw!r}")

    ph = (proj.get("posthog") or {})
    project_id = ph.get("project_id")
    if not project_id:
        raise SystemExit(f"ERROR: posthog.project_id not set for '{product}'")

    api_key = _posthog_api_key()
    if not api_key:
        raise SystemExit("ERROR: PostHog API key not available (set POSTHOG_PERSONAL_API_KEY or keychain)")

    path, views_24h = _pick_top_path(api_key, project_id, domain)
    if not path or views_24h <= 0:
        print(f"SKIP: no pageviews in last 24h for {domain}", file=sys.stderr)
        sys.exit(2)

    client_slug = _client_slug(product)
    metrics = _build_metrics(api_key, project_id, domain, path, client_slug)

    base_url = (lp.get("base_url") or website).rstrip("/")
    page_url = base_url + (path if path.startswith("/") else "/" + path)

    history = _history_for_page(product, path)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "product": proj.get("name"),
        "domain": domain,
        "repo_path": repo_abs,
        "page_path": path,
        "page_url": page_url,
        "posthog_project_id": project_id,
        "client_slug": client_slug,
        "metrics": metrics,
        "history": history,
        "project_config": proj,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--out", help="Write brief to this path instead of stdout")
    args = ap.parse_args()

    brief = build_brief(args.product)
    blob = json.dumps(brief, indent=2, ensure_ascii=False, default=str)
    if args.out:
        Path(args.out).write_text(blob)
        print(args.out)
    else:
        sys.stdout.write(blob)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
