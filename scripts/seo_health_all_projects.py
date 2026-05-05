#!/usr/bin/env python3
"""Cross-project SEO health snapshot.

For every project in config.json with a gsc_property, pulls 30 days of
GSC page-level data and reports:
  - pages with any impressions
  - total clicks / impressions / site CTR
  - zero-click pages (any age) — domain quality risk
  - top performer page (>=1 click) and its clicks

Read-only. Shares the same SA credentials as fetch_gsc_queries.py.
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
CONFIG_PATH = ROOT_DIR / "config.json"
SA_PATH = ROOT_DIR / "seo" / "credentials" / "seo-autopilot-sa.json"

PERIOD_DAYS = 30
ROW_LIMIT = 25000

from google.oauth2 import service_account
from googleapiclient.discovery import build

creds = service_account.Credentials.from_service_account_file(
    str(SA_PATH),
    scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
)
svc = build("searchconsole", "v1", credentials=creds)

cfg = json.loads(CONFIG_PATH.read_text())
end_date = date.today() - timedelta(days=2)
start_date = end_date - timedelta(days=PERIOD_DAYS)

print(f"\n{'Project':<22} {'Pages':>6} {'Clicks':>7} {'Imp':>9} {'CTR':>6} {'0-click':>8} {'%dead':>6} {'TopPg':>6}")
print("-" * 80)

rows_out = []
for p in cfg.get("projects", []):
    name = p["name"]
    lp = p.get("landing_pages") or {}
    gsc = lp.get("gsc_property")
    if not gsc:
        continue
    try:
        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["page"],
            "rowLimit": ROW_LIMIT,
        }
        resp = svc.searchanalytics().query(siteUrl=gsc, body=body).execute()
        rows = resp.get("rows", [])
    except Exception as e:
        print(f"{name:<22} ERROR {e}")
        continue

    if not rows:
        print(f"{name:<22} {0:>6} {0:>7} {0:>9} {0:>6.2f} {0:>8} {0:>6.0f} {0:>6}")
        continue

    total_clicks = sum(r.get("clicks", 0) for r in rows)
    total_imp = sum(r.get("impressions", 0) for r in rows)
    site_ctr = (total_clicks / total_imp * 100) if total_imp else 0
    zero_click = [r for r in rows if r.get("clicks", 0) == 0]
    top_pg = max((r.get("clicks", 0) for r in rows), default=0)
    pct_dead = (len(zero_click) / len(rows) * 100) if rows else 0

    rows_out.append({
        "name": name, "pages": len(rows), "clicks": total_clicks,
        "imp": total_imp, "ctr": site_ctr, "zero": len(zero_click),
        "pct_dead": pct_dead, "top": top_pg,
    })

# Sort by dead-weight count
rows_out.sort(key=lambda r: -r["zero"])
for r in rows_out:
    print(
        f"{r['name']:<22} {r['pages']:>6} {r['clicks']:>7} {r['imp']:>9,} "
        f"{r['ctr']:>5.2f}% {r['zero']:>8} {r['pct_dead']:>5.0f}% {r['top']:>6}"
    )

# Aggregate
total_pages = sum(r["pages"] for r in rows_out)
total_clicks = sum(r["clicks"] for r in rows_out)
total_imp = sum(r["imp"] for r in rows_out)
total_zero = sum(r["zero"] for r in rows_out)
agg_ctr = (total_clicks / total_imp * 100) if total_imp else 0
print("-" * 80)
print(f"{'TOTAL ('+str(len(rows_out))+' projects)':<22} {total_pages:>6} {total_clicks:>7} {total_imp:>9,} {agg_ctr:>5.2f}% {total_zero:>8}")
