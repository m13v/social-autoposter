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
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project_stats as ps


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
        "pageview_details": { domain: { "total": int, "top_pages": {path: n} } },
        "cta_details": [ {text, section, time}, ... up to 10 ] }
    """
    empty = {
        "pageviews": 0,
        "cta_clicks": 0,
        "email_signups": 0,
        "schedule_clicks": 0,
        "pageview_details": {domain: {"total": 0, "top_pages": {}}},
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
        "GROUP BY path ORDER BY c DESC LIMIT 10"
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
        "pageview_details": {domain: {"total": pv_total, "top_pages": top_pages}},
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
                    "pageview_details": {d: {"total": 0, "top_pages": {}}},
                    "cta_details": [],
                }
            per_domain.append(stats)
        posthog = _ph_combine(per_domain)
    else:
        posthog = None

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
    analytics_suspected_broken = (pvs >= 500) and ((email_signups + schedule_clicks) == 0)

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

    projects_with_stats = {
        "fazm", "Cyrano", "PieLine", "Terminator", "S4L",
        "macOS MCP", "Vipassana", "WhatsApp MCP", "AI Browser Profile", "macOS Session Replay",
    }

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
        if name not in projects_with_stats and not args.project:
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
                        "pageview_details": {key[2]: {"total": 0, "top_pages": {}}},
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
