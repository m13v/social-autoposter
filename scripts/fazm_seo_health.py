#!/usr/bin/env python3
"""
Analyze fazm.ai SEO health: identify dead-weight pages that may drag domain quality.
Pulls 90-day GSC data, then segments pages into:
  - Top performers (clicks > 0)
  - Indexed-but-zero-traffic (impressions > 50, clicks = 0) -> quality risk
  - High-impression / low-CTR (CTR < 0.5%, impressions > 100) -> bad SERP fit
  - Total counts per bucket
"""
import os
import sys
import json
from datetime import date, timedelta
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SA_PATH = os.path.join(ROOT_DIR, "seo", "credentials", "seo-autopilot-sa.json")

from google.oauth2 import service_account
from googleapiclient.discovery import build

PROPERTY = "sc-domain:fazm.ai"
PERIOD_DAYS = 90
ROW_LIMIT = 25000

end = date.today()
start = end - timedelta(days=PERIOD_DAYS)

creds = service_account.Credentials.from_service_account_file(
    SA_PATH,
    scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
)
svc = build("searchconsole", "v1", credentials=creds)

# Pull pages
req = {
    "startDate": start.isoformat(),
    "endDate": end.isoformat(),
    "dimensions": ["page"],
    "rowLimit": ROW_LIMIT,
}
resp = svc.searchanalytics().query(siteUrl=PROPERTY, body=req).execute()
rows = resp.get("rows", [])

print(f"=== fazm.ai SEO Health Report ===")
print(f"Period: {start} -> {end} ({PERIOD_DAYS} days)")
print(f"Total pages with any GSC impressions: {len(rows)}")
print()

# Bucket pages
top_performers = []
zero_click_high_imp = []  # quality risk
low_ctr_high_imp = []     # bad SERP fit
zero_imp_pages = 0        # not in this dataset (GSC excludes those)
total_clicks = 0
total_imp = 0

for r in rows:
    page = r["keys"][0]
    clicks = r.get("clicks", 0)
    imp = r.get("impressions", 0)
    ctr = r.get("ctr", 0)
    pos = r.get("position", 0)
    total_clicks += clicks
    total_imp += imp

    entry = {"page": page, "clicks": clicks, "imp": imp, "ctr": ctr, "pos": pos}
    if clicks >= 1:
        top_performers.append(entry)
    if clicks == 0 and imp >= 50:
        zero_click_high_imp.append(entry)
    if imp >= 100 and ctr < 0.005:
        low_ctr_high_imp.append(entry)

print(f"Total clicks: {total_clicks}")
print(f"Total impressions: {total_imp}")
print(f"Site CTR: {(total_clicks/total_imp*100) if total_imp else 0:.2f}%")
print()

print(f"=== Top performers (>=1 click) — {len(top_performers)} pages ===")
top_performers.sort(key=lambda x: -x["clicks"])
for e in top_performers[:20]:
    print(f"  {e['clicks']:>4} clicks | {e['imp']:>6} imp | CTR {e['ctr']*100:>5.2f}% | pos {e['pos']:>5.1f} | {e['page']}")
print()

print(f"=== Zero-click despite >=50 impressions — {len(zero_click_high_imp)} pages (DEAD-WEIGHT) ===")
zero_click_high_imp.sort(key=lambda x: -x["imp"])
for e in zero_click_high_imp[:30]:
    print(f"  {e['imp']:>6} imp | pos {e['pos']:>5.1f} | {e['page']}")
print()

print(f"=== High-impression / low-CTR (<0.5%, imp>=100) — {len(low_ctr_high_imp)} pages (BAD FIT) ===")
low_ctr_high_imp.sort(key=lambda x: -x["imp"])
for e in low_ctr_high_imp[:30]:
    print(f"  {e['imp']:>6} imp | {e['clicks']:>3} cl | CTR {e['ctr']*100:>5.2f}% | pos {e['pos']:>5.1f} | {e['page']}")
print()

# Save raw to json for follow-up
out_path = "/tmp/fazm_gsc_pages.json"
with open(out_path, "w") as f:
    json.dump({
        "period_days": PERIOD_DAYS,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total_pages": len(rows),
        "rows": rows,
    }, f, indent=2)
print(f"Raw data saved to {out_path}")
