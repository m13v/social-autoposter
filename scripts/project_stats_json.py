#!/usr/bin/env python3
"""JSON wrapper around project_stats.py for the dashboard /api/funnel/stats endpoint.

Emits a single JSON object on stdout: { generated_at, days, projects: [ ... ], overall }.
Keeps project_stats.py untouched (it is chflags uchg-locked).
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_stats as ps


_PAGE_FILENAMES = ("page.tsx", "page.ts", "page.jsx", "page.js", "page.mdx", "page.md")


def _scan_repo_pages(repo_path):
    """Walk a Next.js app-router repo and return URL paths we ship as static files.

    Skips dynamic segments ([slug], [...rest]), route groups ((group)), private
    folders (_foo), and parallel-route slots (@slot) per Next.js conventions.
    Route groups collapse to nothing; dynamic segments exclude the whole branch.
    """
    out = set()
    if not repo_path:
        return out
    repo = os.path.expanduser(repo_path)
    app_roots = [
        os.path.join(repo, "src", "app"),
        os.path.join(repo, "app"),
    ]
    for root in app_roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            segs = [] if rel == "." else rel.split(os.sep)
            if any(s.startswith(("[", "_", "@")) for s in segs):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if not d.startswith(("[", "_", "@", "."))
                           and d not in ("node_modules",)]
            has_page = any(f in _PAGE_FILENAMES for f in filenames)
            if has_page:
                url_segs = [s for s in segs if not (s.startswith("(") and s.endswith(")"))]
                path = "/" + "/".join(url_segs) if url_segs else "/"
                out.add(path)
    return out


def _db_created_pages(conn, product_name, days=None):
    """Return {domain: set(paths)} for pages this project published via the SEO
    pipelines (seo_keywords) or GSC-driven page generation (gsc_queries).

    When `days` is set, restrict to pages whose `completed_at` falls inside the
    window. The seo_keywords / gsc_queries rows get `completed_at` stamped when
    the page is actually generated, so this matches "pages created in the last
    N days" as used by the dashboard's period selector.
    """
    out = {}
    window_sql = ""
    if days is not None:
        window_sql = f" AND completed_at >= NOW() - INTERVAL '{int(days)} days'"
    for sql in (
        "SELECT page_url FROM seo_keywords WHERE product = %s AND page_url IS NOT NULL" + window_sql,
        "SELECT page_url FROM gsc_queries WHERE product = %s AND page_url IS NOT NULL" + window_sql,
    ):
        try:
            cur = conn.execute(sql, (product_name,))
            for row in cur.fetchall():
                url = row[0]
                if not url:
                    continue
                try:
                    parsed = urllib.parse.urlparse(url)
                except Exception:
                    continue
                host = (parsed.netloc or "").lower()
                path = parsed.path or "/"
                while len(path) > 1 and path.endswith("/"):
                    path = path[:-1]
                if not host:
                    continue
                out.setdefault(host, set()).add(path)
        except Exception as e:
            print(f"  _db_created_pages query error: {e}", file=sys.stderr)
    return out


def _created_paths_for_project(conn, proj, days=None):
    """Return {domain: set(paths)} of pages we created for this project.

    Source-of-truth union: filesystem scan of the project's landing-pages repo
    (applies to every domain the project owns) plus any URLs logged in
    seo_keywords / gsc_queries (keyed by their own host).

    When `days` is set, the filesystem scan is skipped entirely — static page
    files on disk carry no creation timestamp we can trust, so a window-scoped
    "pages created in the last N days" answer has to come from the DB alone.
    """
    by_domain = {}
    domains = ps.get_project_domains(proj) or []
    if days is None:
        lp = proj.get("landing_pages") or {}
        repo_path = lp.get("repo") if isinstance(lp, dict) else None
        fs_paths = _scan_repo_pages(repo_path) if repo_path else set()
        for d in domains:
            by_domain.setdefault(d.lower(), set()).update(fs_paths)
    for host, paths in _db_created_pages(conn, proj.get("name") or "", days=days).items():
        by_domain.setdefault(host, set()).update(paths)
    return by_domain


def _norm_path(p):
    """Match the frontend `normPath` in bin/server.js so PostHog pathnames
    (`properties.$pathname`) and DB-derived created paths compare cleanly.
    """
    s = str(p or "/")
    if not s.startswith("/"):
        s = "/" + s
    while len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    return s


# HogQL-based PostHog query layer.
#
# project_stats.py uses the events LIST endpoint with limit=1000 and no
# pagination, so any (domain, event) that exceeds 1000 occurrences in the
# window silently caps at 1000 and misreports the funnel. We swap that out
# for HogQL aggregate queries (COUNT/GROUP BY), which return the true
# totals in a single call per query.
_SAFE_DOMAIN_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _hogql(api_key, project_id, query, timeout=60):
    """Run a HogQL query against /api/projects/{pid}/query/.
    Returns the `results` list (list of row lists), or [] on error.
    """
    url = f"https://us.posthog.com/api/projects/{project_id}/query/"
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": query}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data.get("results", []) or []
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            detail = ""
        print(f"  HogQL HTTPError {e.code}: {detail} | query={query[:120]}", file=sys.stderr)
        return []
    except urllib.error.URLError as e:
        print(f"  HogQL URLError: {e} | query={query[:120]}", file=sys.stderr)
        return []


def _ph_domain_counts(api_key, project_id, domain, after_iso):
    """Return aggregated stats for one domain using HogQL (no 1000-row cap).

    Output shape matches what _ph_combine used to produce per domain:
      { "pageviews": int,
        "cta_clicks": int,
        "pageview_details": { domain: { "total": int, "top_pages": {path: n}, "top_pages_signups": {path: n}, "top_pages_schedule": {path: n} } },
        "cta_details": [ {text, section, time}, ... up to 10 ] }
    """
    empty = {
        "pageviews": 0,
        "cta_clicks": 0,
        "email_signups": 0,
        "schedule_clicks": 0,
        "pageview_details": {domain: {"total": 0, "top_pages": {}, "top_pages_signups": {}, "top_pages_schedule": {}}},
        "cta_details": [],
    }
    if not _SAFE_DOMAIN_RE.match(domain or ""):
        print(f"  skip unsafe domain: {domain!r}", file=sys.stderr)
        return empty

    # after_iso looks like "2026-04-17T22:07:30"; HogQL's toDateTime wants a
    # 'YYYY-MM-DD HH:MM:SS' literal.
    after_str = (after_iso or "").replace("T", " ")
    if not after_str:
        return empty

    pv_total_q = (
        "SELECT count() FROM events "
        f"WHERE event = '$pageview' "
        f"AND properties.$host = '{domain}' "
        f"AND timestamp >= toDateTime('{after_str}')"
    )
    top_pages_q = (
        "SELECT properties.$pathname AS path, count() AS c FROM events "
        f"WHERE event = '$pageview' "
        f"AND properties.$host = '{domain}' "
        f"AND timestamp >= toDateTime('{after_str}') "
        "GROUP BY path ORDER BY c DESC LIMIT 500"
    )
    cta_total_q = (
        "SELECT count() FROM events "
        f"WHERE event = 'cta_click' "
        f"AND properties.$host = '{domain}' "
        f"AND timestamp >= toDateTime('{after_str}')"
    )
    cta_detail_q = (
        "SELECT properties.$el_text, properties.text, properties.section, timestamp "
        "FROM events "
        f"WHERE event = 'cta_click' "
        f"AND properties.$host = '{domain}' "
        f"AND timestamp >= toDateTime('{after_str}') "
        "ORDER BY timestamp DESC LIMIT 10"
    )
    email_signup_q = (
        "SELECT count() FROM events "
        f"WHERE event = 'newsletter_subscribed' "
        f"AND properties.$host = '{domain}' "
        f"AND timestamp >= toDateTime('{after_str}')"
    )
    schedule_click_q = (
        "SELECT count() FROM events "
        f"WHERE event = 'schedule_click' "
        f"AND properties.$host = '{domain}' "
        f"AND timestamp >= toDateTime('{after_str}')"
    )
    signup_by_page_q = (
        "SELECT properties.$pathname AS path, count() AS c FROM events "
        f"WHERE event = 'newsletter_subscribed' "
        f"AND properties.$host = '{domain}' "
        f"AND timestamp >= toDateTime('{after_str}') "
        "GROUP BY path ORDER BY c DESC LIMIT 50"
    )
    schedule_by_page_q = (
        "SELECT properties.$pathname AS path, count() AS c FROM events "
        f"WHERE event = 'schedule_click' "
        f"AND properties.$host = '{domain}' "
        f"AND timestamp >= toDateTime('{after_str}') "
        "GROUP BY path ORDER BY c DESC LIMIT 50"
    )

    pv_rows = _hogql(api_key, project_id, pv_total_q)
    pv_total = int(pv_rows[0][0]) if pv_rows and pv_rows[0] else 0

    top_rows = _hogql(api_key, project_id, top_pages_q)
    top_pages = {}
    for r in (top_rows or []):
        path = r[0] if r and r[0] else "/"
        top_pages[path] = int(r[1])

    cta_rows = _hogql(api_key, project_id, cta_total_q)
    cta_total = int(cta_rows[0][0]) if cta_rows and cta_rows[0] else 0
    cta_details = []

    email_rows = _hogql(api_key, project_id, email_signup_q)
    email_signups = int(email_rows[0][0]) if email_rows and email_rows[0] else 0

    sched_rows = _hogql(api_key, project_id, schedule_click_q)
    schedule_clicks = int(sched_rows[0][0]) if sched_rows and sched_rows[0] else 0

    signup_page_rows = _hogql(api_key, project_id, signup_by_page_q)
    top_pages_signups = {}
    for r in (signup_page_rows or []):
        path = r[0] if r and r[0] else "/"
        top_pages_signups[path] = int(r[1])

    sched_page_rows = _hogql(api_key, project_id, schedule_by_page_q)
    top_pages_schedule = {}
    for r in (sched_page_rows or []):
        path = r[0] if r and r[0] else "/"
        top_pages_schedule[path] = int(r[1])

    if cta_total > 0:
        detail_rows = _hogql(api_key, project_id, cta_detail_q)
        for r in (detail_rows or []):
            el_text = r[0] if len(r) > 0 else None
            text = r[1] if len(r) > 1 else None
            section = r[2] if len(r) > 2 else None
            ts = r[3] if len(r) > 3 else None
            cta_details.append({
                "text": el_text or text or "?",
                "section": section or "?",
                "time": (str(ts)[:16] if ts else "?"),
            })
    else:
        # Fallback: $autocapture clicks whose el_text contains "book"
        ac_total_q = (
            "SELECT count() FROM events "
            f"WHERE event = '$autocapture' "
            f"AND properties.$host = '{domain}' "
            f"AND timestamp >= toDateTime('{after_str}') "
            "AND lower(properties.$el_text) LIKE '%book%'"
        )
        ac_rows = _hogql(api_key, project_id, ac_total_q)
        cta_total = int(ac_rows[0][0]) if ac_rows and ac_rows[0] else 0
        if cta_total > 0:
            ac_detail_q = (
                "SELECT properties.$el_text, properties.text, properties.section, timestamp "
                "FROM events "
                f"WHERE event = '$autocapture' "
                f"AND properties.$host = '{domain}' "
                f"AND timestamp >= toDateTime('{after_str}') "
                "AND lower(properties.$el_text) LIKE '%book%' "
                "ORDER BY timestamp DESC LIMIT 10"
            )
            rows = _hogql(api_key, project_id, ac_detail_q)
            for r in (rows or []):
                el_text = r[0] if len(r) > 0 else None
                text = r[1] if len(r) > 1 else None
                section = r[2] if len(r) > 2 else None
                ts = r[3] if len(r) > 3 else None
                cta_details.append({
                    "text": el_text or text or "?",
                    "section": section or "?",
                    "time": (str(ts)[:16] if ts else "?"),
                })

    return {
        "pageviews": pv_total,
        "cta_clicks": cta_total,
        "email_signups": email_signups,
        "schedule_clicks": schedule_clicks,
        "pageview_details": {domain: {
            "total": pv_total,
            "top_pages": top_pages,
            "top_pages_signups": top_pages_signups,
            "top_pages_schedule": top_pages_schedule,
        }},
        "cta_details": cta_details,
    }


def _ph_combine(per_domain):
    out = {
        "pageviews": 0,
        "cta_clicks": 0,
        "email_signups": 0,
        "schedule_clicks": 0,
        "pageview_details": {},
        "cta_details": [],
    }
    for s in per_domain:
        out["pageviews"] += s.get("pageviews", 0)
        out["cta_clicks"] += s.get("cta_clicks", 0)
        out["email_signups"] += s.get("email_signups", 0)
        out["schedule_clicks"] += s.get("schedule_clicks", 0)
        out["pageview_details"].update(s.get("pageview_details", {}))
        out["cta_details"].extend(s.get("cta_details", []))
    return out


def _bookings_shared(bookings_conn, client_slug, days):
    """Same output shape as ps.get_booking_stats, but reuses a shared psycopg2
    connection instead of opening a fresh one per project."""
    if not bookings_conn or not client_slug:
        return None
    try:
        cur = bookings_conn.cursor()
        cur.execute(
            "SELECT COUNT(*), "
            "COUNT(*) FILTER (WHERE status = 'created'), "
            "COUNT(*) FILTER (WHERE status = 'cancelled'), "
            "COUNT(*) FILTER (WHERE status = 'rescheduled'), "
            "COUNT(*) FILTER (WHERE attendee_email NOT LIKE '%%test%%' "
            "AND attendee_email NOT LIKE '%%example%%' "
            "AND attendee_name NOT LIKE '%%TEST%%' "
            "AND attendee_name NOT LIKE '%%John Doe%%') "
            "FROM cal_bookings WHERE client_slug = %s "
            "AND created_at >= NOW() - INTERVAL '" + str(days) + " days'",
            (client_slug,),
        )
        row = cur.fetchone()
        cols = ["total", "booked", "cancelled", "rescheduled", "real_bookings"]
        result = dict(zip(cols, row)) if row else {}

        cur.execute(
            "SELECT attendee_name, attendee_email, status, start_time, created_at "
            "FROM cal_bookings WHERE client_slug = %s "
            "AND created_at >= NOW() - INTERVAL '" + str(days) + " days' "
            "ORDER BY created_at DESC LIMIT 5",
            (client_slug,),
        )
        result["recent"] = [
            {"name": r[0], "email": r[1], "status": r[2],
             "start": str(r[3])[:16] if r[3] else "?",
             "created": str(r[4])[:16] if r[4] else "?"}
            for r in cur.fetchall()
        ]
        cur.close()
        return result
    except Exception as e:
        print(f"  Bookings DB error for {client_slug}: {e}", file=sys.stderr)
        return None


def _windowed_post_engagement(conn, name, days):
    """Sum engagement only for posts *created within the window*.

    project_stats.get_post_stats aggregates engagement over ALL time for the
    project, which is misleading when the window is a day or a week. Here we
    filter by posted_at so upvotes/comments/views match the same 24h slice as
    the 'recent' post count.
    """
    cur = conn.execute(
        "SELECT COALESCE(SUM(upvotes), 0), "
        "COALESCE(SUM(comments_count), 0), "
        "COALESCE(SUM(views) FILTER (WHERE LOWER(platform) NOT IN ('moltbook', 'github', 'github_issues')), 0), "
        "COUNT(*) FILTER (WHERE LOWER(platform) NOT IN ('moltbook', 'github', 'github_issues')) "
        "FROM posts WHERE project_name = %s AND posted_at >= NOW() - INTERVAL '" + str(days) + " days'",
        (name,),
    )
    row = cur.fetchone() or (0, 0, 0, 0)
    return {
        "upvotes": int(row[0] or 0),
        "comments": int(row[1] or 0),
        "views": int(row[2] or 0),
        "views_posts": int(row[3] or 0),
    }


def _seo_pages_count(conn, name, days):
    """Count SEO pages published in window. seo_keywords.product matches project_name."""
    cur = conn.execute(
        "SELECT "
        "(SELECT COUNT(*) FROM seo_keywords WHERE product = %s "
        "   AND completed_at >= NOW() - INTERVAL '" + str(days) + " days' "
        "   AND page_url IS NOT NULL) + "
        "(SELECT COUNT(*) FROM gsc_queries WHERE product = %s "
        "   AND completed_at >= NOW() - INTERVAL '" + str(days) + " days' "
        "   AND page_url IS NOT NULL)",
        (name, name),
    )
    row = cur.fetchone()
    return int((row and row[0]) or 0)


def build_project_entry(conn, proj, days, api_key, ph_pid, bookings_conn, env, ph_results):
    name = proj["name"]
    post_stats = ps.get_post_stats(conn, name, days)
    platforms = ps.get_platform_breakdown(conn, name, days)
    eng_recent = _windowed_post_engagement(conn, name, days)
    seo_pages_recent = _seo_pages_count(conn, name, days)

    domains = ps.get_project_domains(proj)
    ph_override = proj.get("posthog", {}) or {}
    ph_key = env.get(ph_override.get("api_key_env", ""), api_key)
    ph_pid_proj = ph_override.get("project_id", ph_pid)
    if domains:
        per_domain = []
        for d in domains:
            stats = ph_results.get((ph_key, ph_pid_proj, d))
            if stats is None:
                stats = {
                    "pageviews": 0,
                    "cta_clicks": 0,
                    "email_signups": 0,
                    "schedule_clicks": 0,
                    "pageview_details": {d: {"total": 0, "top_pages": {}, "top_pages_signups": {}, "top_pages_schedule": {}}},
                    "cta_details": [],
                }
            per_domain.append(stats)
        posthog = _ph_combine(per_domain)
    else:
        posthog = None

    # Window-scoped: `created_paths` is now restricted to pages whose
    # seo_keywords/gsc_queries `completed_at` falls inside `days`. Top tab →
    # Pages sub-tab already filters rows on this set, so it becomes "pages
    # created in the selected period" automatically.
    created_by_domain = _created_paths_for_project(conn, proj, days=days)
    if posthog is not None:
        for d, detail in (posthog.get("pageview_details") or {}).items():
            paths = created_by_domain.get((d or "").lower(), set())
            detail["created_paths"] = sorted(paths)

    # Preserve the pre-rewrite, domain-wide totals for the analytics-broken
    # canary below — it's meant to answer "is window.posthog wired up on this
    # site at all?", which requires domain-level signal, not per-new-page.
    domain_wide_pv = int(posthog["pageviews"]) if posthog else 0
    domain_wide_signups = int(posthog["email_signups"]) if posthog else 0
    domain_wide_sched = int(posthog["schedule_clicks"]) if posthog else 0

    # Recompute funnel totals against the window-scoped created set so the
    # Status tab → project funnel columns reflect "pageviews / signups /
    # schedule clicks ONLY on pages we generated in this window" instead of
    # domain-wide traffic. cta_clicks and real_bookings are not tracked
    # per-page so they stay domain/project-wide.
    if posthog is not None:
        scoped_pv = 0
        scoped_signups = 0
        scoped_sched = 0
        for d, detail in (posthog.get("pageview_details") or {}).items():
            created = {_norm_path(p) for p in created_by_domain.get((d or "").lower(), set())}
            if not created:
                continue
            for path, cnt in (detail.get("top_pages") or {}).items():
                if _norm_path(path) in created:
                    scoped_pv += int(cnt or 0)
            for path, cnt in (detail.get("top_pages_signups") or {}).items():
                if _norm_path(path) in created:
                    scoped_signups += int(cnt or 0)
            for path, cnt in (detail.get("top_pages_schedule") or {}).items():
                if _norm_path(path) in created:
                    scoped_sched += int(cnt or 0)
        posthog["pageviews"] = scoped_pv
        posthog["email_signups"] = scoped_signups
        posthog["schedule_clicks"] = scoped_sched

    client_slug = ps.get_client_slug(name)
    bookings = _bookings_shared(bookings_conn, client_slug, days) if client_slug else None

    pvs = posthog["pageviews"] if posthog else 0
    ctas = posthog["cta_clicks"] if posthog else 0
    real = bookings.get("real_bookings", 0) if bookings else 0
    ctr = (ctas / pvs * 100) if pvs else None
    conv = (real / ctas * 100) if ctas else None

    email_signups = (posthog["email_signups"] if posthog else 0)
    schedule_clicks = (posthog["schedule_clicks"] if posthog else 0)
    # Canary: real traffic but zero tracked conversion events almost always
    # means window.posthog was never wired up on the site (e.g. Fazm
    # newsletter bug where signups worked but nothing fired to PostHog).
    # Use domain-wide totals so the signal doesn't get diluted by the
    # window-scoped funnel numbers above.
    analytics_suspected_broken = (domain_wide_pv >= 500) and ((domain_wide_signups + domain_wide_sched) == 0)

    return {
        "name": name,
        "posts": {
            "total": post_stats.get("total", 0),
            "recent": post_stats.get("recent", 0),
            "active": post_stats.get("active", 0),
            "removed": post_stats.get("removed", 0),
            # Lifetime engagement across ALL posts for this project (kept for context).
            "upvotes": post_stats.get("total_upvotes", 0),
            "comments": post_stats.get("total_comments", 0),
            "views": post_stats.get("total_views", 0),
            # Window-scoped engagement: only posts created in the last `days`.
            "upvotes_recent": eng_recent["upvotes"],
            "comments_recent": eng_recent["comments"],
            "views_recent": eng_recent["views"] if eng_recent["views_posts"] > 0 else None,
        },
        "seo": {"pages_recent": seo_pages_recent},
        "platforms": platforms,
        "posthog": posthog,
        "bookings": bookings,
        "funnel": {
            "pageviews": pvs,
            "cta_clicks": ctas,
            "email_signups": email_signups,
            "schedule_clicks": schedule_clicks,
            "real_bookings": real,
            "ctr_pct": ctr,
            "conv_pct": conv,
        },
        "analytics_suspected_broken": analytics_suspected_broken,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--project", help="Filter to a single project name")
    args = parser.parse_args()

    ps.load_env()
    env = os.environ
    config = ps.load_config()

    api_key = env.get("POSTHOG_PERSONAL_API_KEY")
    project_id = env.get("POSTHOG_PROJECT_ID", "330744")
    bookings_db_url = env.get("BOOKINGS_DATABASE_URL")

    if not api_key:
        print(json.dumps({"error": "POSTHOG_PERSONAL_API_KEY not set"}), file=sys.stdout)
        sys.exit(1)

    conn = ps.dbmod.get_conn()

    bookings_conn = None
    if bookings_db_url:
        try:
            import psycopg2
            bookings_conn = psycopg2.connect(bookings_db_url)
        except Exception as e:
            print(f"  Bookings DB connect error: {e}", file=sys.stderr)
            bookings_conn = None

    selected_projects = []
    for proj in config.get("projects", []):
        name = proj["name"]
        if args.project and args.project.lower() != name.lower():
            continue
        selected_projects.append(proj)

    # Collect unique (api_key, project_id, domain) tuples, dedup across
    # projects that share the same PostHog instance and domain so we only
    # pay for each fetch once.
    after = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%S")
    ph_tasks = set()
    for proj in selected_projects:
        domains = ps.get_project_domains(proj)
        if not domains:
            continue
        ph_over = proj.get("posthog", {}) or {}
        ph_key = env.get(ph_over.get("api_key_env", ""), api_key)
        ph_pid_proj = ph_over.get("project_id", project_id)
        for d in domains:
            ph_tasks.add((ph_key, ph_pid_proj, d))

    # Fan out one HogQL batch per unique (key, pid, domain). Each call issues
    # 3-5 small aggregate queries; we run them concurrently across domains.
    ph_results = {}
    if ph_tasks:
        pool_size = max(4, min(16, len(ph_tasks)))
        with ThreadPoolExecutor(max_workers=pool_size) as ex:
            futs = {
                ex.submit(_ph_domain_counts, k, pid, d, after): (k, pid, d)
                for (k, pid, d) in ph_tasks
            }
            for fut, key in futs.items():
                try:
                    ph_results[key] = fut.result()
                except Exception as e:
                    print(f"  PostHog HogQL error for {key[2]}: {e}", file=sys.stderr)
                    ph_results[key] = {
                        "pageviews": 0,
                        "cta_clicks": 0,
                        "email_signups": 0,
                        "schedule_clicks": 0,
                        "pageview_details": {key[2]: {"total": 0, "top_pages": {}, "top_pages_signups": {}, "top_pages_schedule": {}}},
                        "cta_details": [],
                    }

    out_projects = []
    for proj in selected_projects:
        name = proj["name"]
        try:
            out_projects.append(build_project_entry(
                conn, proj, args.days, api_key, project_id, bookings_conn, env, ph_results
            ))
        except Exception as e:
            out_projects.append({"name": name, "error": str(e)})

    cur = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE posted_at >= NOW() - INTERVAL '" + str(args.days) + " days'"
    )
    total_recent = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM posts")
    total_all = cur.fetchone()[0]
    conn.close()
    if bookings_conn:
        try: bookings_conn.close()
        except Exception: pass

    print(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "projects": out_projects,
        "overall": {"total": total_all, "recent": total_recent},
    }))


if __name__ == "__main__":
    main()
