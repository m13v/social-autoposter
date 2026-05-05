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


def load_forbidden_keywords(product):
    """Return the list of forbidden-keyword patterns configured for this product.

    Reads from config.json projects[].landing_pages.forbidden_keywords. Matching
    is case-insensitive substring; the patterns are meant to be surface-form
    search fragments (e.g. 'body scan'), not regex.
    """
    cfg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config.json",
    )
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    for p in cfg.get("projects", []):
        if p.get("name", "").lower() == (product or "").lower():
            lp = p.get("landing_pages") or {}
            return [str(x).lower() for x in (lp.get("forbidden_keywords") or [])]
    return []


def match_forbidden(product, keyword):
    """Return the first forbidden pattern matching this keyword, or ''.

    Case-insensitive substring match. '' means not forbidden.
    """
    kw = (keyword or "").lower()
    for pattern in load_forbidden_keywords(product):
        if pattern and pattern in kw:
            return pattern
    return ""


def pick_next_keyword(product):
    """Pick next keyword: pending (ready to build) first, then unscored."""
    conn = get_conn()
    cur = conn.cursor()

    # First: pending keywords ready to build (floor raised 2026-05-05 from 1.0 to 1.5 after fazm.ai dead-weight audit)
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

    for field in ("score", "signal1", "signal2", "signal3", "notes", "page_url", "content_type", "claude_session_id"):
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


def list_done_pages(product, limit=400):
    """Return the inventory of completed pages for this product.

    Used by generate_page.py's build_prompt to give the model the choice
    between writing a new page or consolidating into an existing one.
    Returns a list of dicts ordered by completion recency (newest first):
        [{"slug": str, "keyword": str, "page_url": str|None,
          "content_type": str|None, "completed_at": str|None}, ...]

    Cap at `limit` so the prompt does not blow past context on a site with
    1000+ pages. The caller can further filter by token overlap with the
    new keyword to keep the inventory relevant.
    """
    conn = get_conn()
    cur = conn.cursor()
    # Case-insensitive product match. Historical writes for some projects
    # (notably fazm) used lowercase product names while config.json now has
    # them title-cased. Comparing on LOWER(product) keeps the inventory
    # surfaced regardless of which casing the caller passes in.
    cur.execute(
        """
        SELECT slug, keyword, page_url, content_type, completed_at
        FROM seo_keywords
        WHERE LOWER(product) = LOWER(%s) AND status = 'done' AND slug IS NOT NULL
        ORDER BY completed_at DESC NULLS LAST, updated_at DESC NULLS LAST
        LIMIT %s
        """,
        (product, int(limit)),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "slug": r[0],
            "keyword": r[1],
            "page_url": r[2],
            "content_type": r[3],
            "completed_at": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]


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
    elif cmd == "check_forbidden":
        keyword = sys.argv[3]
        match = match_forbidden(product, keyword)
        print(match if match else "ok")
    else:
        print("Usage: db_helpers.py <pick|update|has_work|report|check_slug|check_forbidden> <product> [args]")
