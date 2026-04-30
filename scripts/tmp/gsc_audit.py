#!/usr/bin/env python3
"""Cross-project GSC audit. Pulls performance + index coverage stats."""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

ROOT = os.path.expanduser("~/social-autoposter")
SA_PATH = os.path.join(ROOT, "seo/credentials/seo-autopilot-sa.json")
CONFIG_FILE = os.path.join(ROOT, "config.json")

from google.oauth2 import service_account
from googleapiclient.discovery import build

def get_service():
    creds = service_account.Credentials.from_service_account_file(
        SA_PATH, scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
    )
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)

def date_range(days_back, end_offset=2):
    end = datetime.utcnow().date() - timedelta(days=end_offset)
    start = end - timedelta(days=days_back - 1)
    return start.isoformat(), end.isoformat()

def query_perf(svc, prop, start, end, dimensions=None, row_limit=25000):
    body = {
        "startDate": start,
        "endDate": end,
        "rowLimit": row_limit,
        "dataState": "all",
    }
    if dimensions:
        body["dimensions"] = dimensions
    resp = svc.searchanalytics().query(siteUrl=prop, body=body).execute()
    return resp.get("rows", [])

def get_all_sites(svc):
    return svc.sites().list().execute().get("siteEntry", [])

def audit_property(name, prop, all_props):
    out = {"name": name, "property": prop}
    if prop not in all_props:
        out["status"] = "NOT_VERIFIED_OR_NO_ACCESS"
        out["permission"] = None
        return out
    out["permission"] = all_props[prop]
    try:
        svc = get_service()
        s30, e30 = date_range(28)
        s7, e7 = date_range(7)
        s_prev, e_prev = date_range(7, end_offset=9)
        s90, e90 = date_range(90)
        # Aggregate totals
        agg30 = query_perf(svc, prop, s30, e30, dimensions=None, row_limit=1)
        agg7 = query_perf(svc, prop, s7, e7, dimensions=None, row_limit=1)
        agg_prev = query_perf(svc, prop, s_prev, e_prev, dimensions=None, row_limit=1)
        # Pages with impressions (proxy for indexed+ranking)
        pages30 = query_perf(svc, prop, s30, e30, dimensions=["page"], row_limit=25000)
        pages90 = query_perf(svc, prop, s90, e90, dimensions=["page"], row_limit=25000)
        # Queries
        queries30 = query_perf(svc, prop, s30, e30, dimensions=["query"], row_limit=25000)

        def totals(rows):
            if not rows:
                return {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0}
            r = rows[0]
            return {
                "clicks": r.get("clicks", 0),
                "impressions": r.get("impressions", 0),
                "ctr": r.get("ctr", 0),
                "position": r.get("position", 0),
            }

        out["status"] = "OK"
        out["last28d"] = totals(agg30)
        out["last7d"] = totals(agg7)
        out["prev7d"] = totals(agg_prev)
        out["pages_with_impressions_28d"] = len(pages30)
        out["pages_with_impressions_90d"] = len(pages90)
        out["unique_queries_28d"] = len(queries30)
        # Top pages by clicks
        top_pages = sorted(pages30, key=lambda r: r.get("clicks", 0), reverse=True)[:5]
        out["top_pages"] = [
            {
                "url": p["keys"][0],
                "clicks": p.get("clicks", 0),
                "impressions": p.get("impressions", 0),
                "position": round(p.get("position", 0), 1),
            }
            for p in top_pages
        ]
        # Top queries
        top_queries = sorted(queries30, key=lambda r: r.get("clicks", 0), reverse=True)[:5]
        out["top_queries"] = [
            {
                "query": q["keys"][0],
                "clicks": q.get("clicks", 0),
                "impressions": q.get("impressions", 0),
                "position": round(q.get("position", 0), 1),
            }
            for q in top_queries
        ]
    except Exception as e:
        out["status"] = "ERROR"
        out["error"] = str(e)[:200]
    return out

def main():
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    projects = []
    for p in config.get("projects", []):
        gsc = p.get("landing_pages", {}).get("gsc_property")
        if gsc:
            projects.append((p["name"], gsc))

    svc = get_service()
    sites = get_all_sites(svc)
    all_props = {s["siteUrl"]: s.get("permissionLevel") for s in sites}
    print(f"# SA has access to {len(all_props)} GSC properties")
    print(f"# Auditing {len(projects)} configured projects")
    print()

    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(audit_property, n, p, all_props): n for n, p in projects}
        for fut in as_completed(futures):
            results.append(fut.result())

    out_path = os.path.join(ROOT, "scripts/tmp/gsc_audit_results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Wrote {out_path}")
    # also list properties SA can see that aren't in config
    cfg_props = {p for _, p in projects}
    extra = [(s, all_props[s]) for s in all_props if s not in cfg_props]
    if extra:
        print("\n# Additional GSC properties accessible to SA but NOT in config.json:")
        for s, perm in extra:
            print(f"  {s}  ({perm})")

if __name__ == "__main__":
    main()
