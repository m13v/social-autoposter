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

    from psycopg2.extras import execute_values

    values = [
        (
            product,
            row["keys"][0],
            int(row["impressions"]),
            int(row["clicks"]),
            float(row.get("ctr", 0)),
            float(row.get("position", 0)),
            classify(row["keys"][0], brand_terms),
        )
        for row in rows
    ]

    if not values:
        return 0, 0

    conn = get_conn()
    cur = conn.cursor()

    # Single batched upsert. xmax=0 identifies freshly inserted rows;
    # anything else is an existing row that got its metrics refreshed.
    sql = """
        INSERT INTO gsc_queries
          (product, query, impressions, clicks, ctr, position, status)
        VALUES %s
        ON CONFLICT (product, query) DO UPDATE SET
          impressions = EXCLUDED.impressions,
          clicks      = EXCLUDED.clicks,
          ctr         = EXCLUDED.ctr,
          position    = EXCLUDED.position,
          last_seen   = NOW(),
          updated_at  = NOW()
        RETURNING (xmax = 0) AS inserted
    """
    results = execute_values(cur, sql, values, page_size=1000, fetch=True)
    added = sum(1 for r in results if r[0])
    updated = len(results) - added

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

    # Normalize to canonical name from config so DB writes never diverge by casing
    product = product_cfg["name"]

    gsc_property = product_cfg.get("landing_pages", {}).get("gsc_property")
    if not gsc_property:
        print(f"[fetch_gsc_queries] ERROR: no gsc_property configured for {product}")
        sys.exit(1)

    brand_terms = product_cfg.get("landing_pages", {}).get("brand_terms", [])

    print(f"[fetch_gsc_queries] Fetching last {PERIOD_DAYS} days of queries for {product} ({gsc_property})")
    rows = fetch_gsc_rows(gsc_property)
    print(f"[fetch_gsc_queries] API returned {len(rows)} queries")

    added, updated = upsert(product, rows, brand_terms, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"[fetch_gsc_queries] added={added} updated={updated} total={len(rows)}")

    print_summary(product, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
