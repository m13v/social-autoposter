#!/usr/bin/env python3
"""
Fetch GSC search queries for a product and upsert into the gsc_queries Postgres table.

State schema (gsc_queries table):
  product, query, impressions, clicks, ctr, position,
  status (pending | in_progress | done | skip | duplicate),
  page_slug, page_url, notes,
  first_seen, last_seen, completed_at, created_at, updated_at

Usage:
  python3 fetch_gsc_queries.py --product Fazm
  python3 fetch_gsc_queries.py --product Fazm --dry-run
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_FILE = os.path.join(ROOT_DIR, "config.json")
SA_PATH = os.path.join(SCRIPT_DIR, "credentials", "seo-autopilot-sa.json")

PERIOD_DAYS = 90
ROW_LIMIT = 25000
IMPRESSIONS_THRESHOLD = 5


def load_env():
    env_path = os.path.join(ROOT_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def get_product_config(product_name):
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    for p in config.get("projects", []):
        if p["name"].lower() == product_name.lower():
            return p
    return None


def get_gsc_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        SA_PATH,
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
    )
    return build("searchconsole", "v1", credentials=creds)


def fetch_gsc_rows(gsc_property):
    svc = get_gsc_service()
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=PERIOD_DAYS)).strftime("%Y-%m-%d")
    result = svc.searchanalytics().query(
        siteUrl=gsc_property,
        body={
            "startDate": start,
            "endDate": end,
            "dimensions": ["query"],
            "rowLimit": ROW_LIMIT,
        },
    ).execute()
    return result.get("rows", [])


def get_conn():
    import psycopg2
    load_env()
    return psycopg2.connect(os.environ["DATABASE_URL"])


def classify(query_text, brand_terms):
    q = query_text.lower().strip()
    if q in {t.lower() for t in brand_terms}:
        return "skip"
    return "pending"


def upsert(product, rows, brand_terms, dry_run=False):
    if dry_run:
        added = sum(1 for r in rows if int(r["impressions"]) >= IMPRESSIONS_THRESHOLD)
        print(f"[fetch_gsc_queries] DRY RUN: would upsert {len(rows)} queries ({added} above threshold)")
        return len(rows), 0

    conn = get_conn()
    cur = conn.cursor()

    added = updated = 0
    for row in rows:
        query_text = row["keys"][0]
        impressions = int(row["impressions"])
        clicks = int(row["clicks"])
        ctr = float(row.get("ctr", 0))
        position = float(row.get("position", 0))
        status = classify(query_text, brand_terms)

        # Upsert: on conflict only update metrics, preserve status/page linkage
        cur.execute("""
            INSERT INTO gsc_queries
              (product, query, impressions, clicks, ctr, position, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (product, query) DO UPDATE SET
              impressions = EXCLUDED.impressions,
              clicks      = EXCLUDED.clicks,
              ctr         = EXCLUDED.ctr,
              position    = EXCLUDED.position,
              last_seen   = NOW(),
              updated_at  = NOW()
        """, (product, query_text, impressions, clicks, ctr, position, status))

        if cur.rowcount == 1 and cur.statusmessage == "INSERT 0 1":
            added += 1
        else:
            updated += 1

    conn.commit()
    cur.close()
    conn.close()
    return added, updated


def print_summary(product, dry_run=False):
    if dry_run:
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT status, COUNT(*) FROM gsc_queries WHERE product = %s GROUP BY status
    """, (product,))
    counts = dict(cur.fetchall())
    pending = counts.get("pending", 0)
    done = counts.get("done", 0)
    skip = counts.get("skip", 0)
    in_progress = counts.get("in_progress", 0)
    print(f"[fetch_gsc_queries] pending={pending} done={done} skip={skip} in_progress={in_progress}")

    cur.execute("""
        SELECT query, impressions, clicks FROM gsc_queries
        WHERE product = %s AND status = 'pending' AND impressions >= %s
        ORDER BY impressions DESC LIMIT 10
    """, (product, IMPRESSIONS_THRESHOLD))
    rows = cur.fetchall()
    if rows:
        print(f"\n[fetch_gsc_queries] Top 10 pending queries (>={IMPRESSIONS_THRESHOLD} impr):")
        for query, impr, clk in rows:
            print(f"  {impr:>5} impr  {clk:>4} clk  {query}")
    cur.close()
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", required=True, help="Product name (e.g. Fazm)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    product_cfg = get_product_config(args.product)
    if not product_cfg:
        print(f"[fetch_gsc_queries] ERROR: product '{args.product}' not found in config.json")
        sys.exit(1)

    gsc_property = product_cfg.get("landing_pages", {}).get("gsc_property")
    if not gsc_property:
        print(f"[fetch_gsc_queries] ERROR: no gsc_property configured for {args.product}")
        sys.exit(1)

    brand_terms = product_cfg.get("landing_pages", {}).get("brand_terms", [])

    print(f"[fetch_gsc_queries] Fetching last {PERIOD_DAYS} days of queries for {args.product} ({gsc_property})")
    rows = fetch_gsc_rows(gsc_property)
    print(f"[fetch_gsc_queries] API returned {len(rows)} queries")

    added, updated = upsert(args.product, rows, brand_terms, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"[fetch_gsc_queries] added={added} updated={updated} total={len(rows)}")

    print_summary(args.product, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
