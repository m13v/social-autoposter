#!/usr/bin/env python3
"""
Database helpers for the SEO pipeline.
All state reads/writes go through Postgres.
"""

import json
import os
import sys
from datetime import datetime, timezone

# Load .env
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(ENV_PATH):
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

import psycopg2


def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def pick_next_keyword(product):
    """Pick next keyword: pending (ready to build) first, then unscored."""
    conn = get_conn()
    cur = conn.cursor()

    # First: pending keywords ready to build (score >= 1.5)
    cur.execute("""
        SELECT keyword, slug, status, score FROM seo_keywords
        WHERE product = %s AND status = 'pending' AND score >= 1.5
        ORDER BY score DESC LIMIT 1
    """, (product,))
    row = cur.fetchone()
    if row:
        cur.close()
        conn.close()
        return {"keyword": row[0], "slug": row[1], "status": row[2], "score": row[3]}

    # Then: unscored keywords — prioritize long-tail (3+ words, moderate volume)
    # over broad head terms that waste scoring budget
    cur.execute("""
        SELECT keyword, slug, status FROM seo_keywords
        WHERE product = %s AND status = 'unscored'
        ORDER BY
            CASE WHEN array_length(string_to_array(keyword, ' '), 1) >= 3
                 AND (volume IS NULL OR volume BETWEEN 20 AND 5000)
                 THEN 0 ELSE 1 END,
            volume DESC NULLS LAST
        LIMIT 1
    """, (product,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {"keyword": row[0], "slug": row[1], "status": row[2], "score": None}
    return None


def update_status(product, keyword, status, **kwargs):
    """Update keyword status and optional fields."""
    conn = get_conn()
    cur = conn.cursor()
    sets = ["status = %s", "updated_at = NOW()"]
    vals = [status]

    for field in ("score", "signal1", "signal2", "signal3", "notes", "page_url"):
        if field in kwargs:
            sets.append(f"{field} = %s")
            vals.append(kwargs[field])

    if status == "scoring":
        pass
    elif status in ("pending", "skip"):
        sets.append("scored_at = NOW()")
    elif status == "done":
        sets.append("completed_at = NOW()")

    vals.extend([product, keyword])
    cur.execute(f"""
        UPDATE seo_keywords SET {', '.join(sets)}
        WHERE product = %s AND keyword = %s
    """, vals)
    conn.commit()
    cur.close()
    conn.close()


def check_slug_exists(product, slug):
    """Check if a page with this slug already exists (status=done)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT count(*) FROM seo_keywords
        WHERE product = %s AND slug = %s AND status = 'done'
    """, (product, slug))
    exists = cur.fetchone()[0] > 0
    cur.close()
    conn.close()
    return exists


def has_work(product):
    """Check if there's any work to do for a product."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            sum(case when status = 'unscored' then 1 else 0 end) as unscored,
            sum(case when status = 'pending' and score >= 1.5 then 1 else 0 end) as pending
        FROM seo_keywords WHERE product = %s
    """, (product,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return (row[0] or 0) > 0 or (row[1] or 0) > 0


def report(product):
    """Print status summary for a product."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT status, count(*) FROM seo_keywords WHERE product = %s GROUP BY status ORDER BY status
    """, (product,))
    rows = cur.fetchall()
    total = sum(r[1] for r in rows)
    print(f"  Total keywords: {total}")
    for status, count in rows:
        print(f"  {status}: {count}")

    cur.execute("""
        SELECT score, keyword FROM seo_keywords
        WHERE product = %s AND status = 'pending' AND score >= 1.5
        ORDER BY score DESC LIMIT 5
    """, (product,))
    pending = cur.fetchall()
    if pending:
        print(f"  Top pending:")
        for score, kw in pending:
            print(f"    {score:.1f} | {kw}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    # CLI: python3 db_helpers.py <command> <product> [args]
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    product = sys.argv[2] if len(sys.argv) > 2 else None

    if cmd == "pick":
        result = pick_next_keyword(product)
        print(json.dumps(result) if result else "NONE")
    elif cmd == "update":
        keyword = sys.argv[3]
        status = sys.argv[4]
        update_status(product, keyword, status)
    elif cmd == "has_work":
        print("yes" if has_work(product) else "no")
    elif cmd == "report":
        report(product)
    elif cmd == "check_slug":
        slug = sys.argv[3]
        print("exists" if check_slug_exists(product, slug) else "new")
    else:
        print("Usage: db_helpers.py <pick|update|has_work|report|check_slug> <product> [args]")
