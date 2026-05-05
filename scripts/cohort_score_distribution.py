#!/usr/bin/env python3
"""Score-cohort distribution for posts in the last N days.

Score formula (matches scripts/top_performers.py SCORE_SQL):
    score = comments_count * 3 + upvotes_adj
    upvotes_adj = max(0, upvotes - 1) on reddit/moltbook (kills OP self-upvote),
                  else upvotes

4 cohorts:
    Dead   : score = 0
    Low    : 1-4
    Mid    : 5-14
    High   : 15+
"""
import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db as dbmod

SCORE_SQL = (
    "(COALESCE(comments_count,0) * 3 + "
    "CASE WHEN LOWER(platform) IN ('reddit','moltbook') "
    "THEN GREATEST(0, COALESCE(upvotes,0) - 1) "
    "ELSE COALESCE(upvotes,0) END)"
)

COHORT_SQL = f"""
CASE
  WHEN {SCORE_SQL} = 0 THEN '1_dead_0'
  WHEN {SCORE_SQL} BETWEEN 1 AND 4 THEN '2_low_1_4'
  WHEN {SCORE_SQL} BETWEEN 5 AND 14 THEN '3_mid_5_14'
  ELSE '4_high_15_plus'
END
"""

DAYS = 7

def fmt_int(x):
    if x is None: return '-'
    return f"{int(x):,}"

def fmt_float(x):
    if x is None: return '-'
    return f"{float(x):.1f}"

def query(conn, where_extra=""):
    sql = f"""
    SELECT
      {COHORT_SQL} AS cohort,
      COUNT(*)                                  AS posts,
      MIN({SCORE_SQL})                          AS min_score,
      MAX({SCORE_SQL})                          AS max_score,
      AVG({SCORE_SQL})::numeric(10,1)           AS avg_score,
      AVG(COALESCE(upvotes,0))::numeric(10,1)   AS avg_up,
      MAX(COALESCE(upvotes,0))                  AS max_up,
      AVG(COALESCE(comments_count,0))::numeric(10,1) AS avg_cm,
      MAX(COALESCE(comments_count,0))           AS max_cm,
      AVG(COALESCE(views,0))::numeric(10,0)     AS avg_views,
      MAX(COALESCE(views,0))                    AS max_views
    FROM posts
    WHERE posted_at >= NOW() - INTERVAL '{DAYS} days'
      AND status = 'active'
      AND upvotes IS NOT NULL
      {where_extra}
    GROUP BY 1
    ORDER BY 1
    """
    cur = conn.execute(sql)
    return cur.fetchall()

def print_table(title, rows, total):
    print(f"\n## {title}")
    if not rows:
        print("(no posts)")
        return
    hdr = f"{'cohort':<18} {'posts':>6} {'%':>5} | {'score':>14} | {'upvotes avg / max':>18} | {'comments avg/max':>17} | {'views avg/max':>16}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        cohort, posts, mn, mx, avg_s, avg_up, max_up, avg_cm, max_cm, avg_v, max_v = r
        pct = (posts / total * 100) if total else 0
        score_range = f"{fmt_int(mn)}-{fmt_int(mx)} (avg {fmt_float(avg_s)})"
        up_str = f"{fmt_float(avg_up)} / {fmt_int(max_up)}"
        cm_str = f"{fmt_float(avg_cm)} / {fmt_int(max_cm)}"
        v_str  = f"{fmt_int(avg_v)} / {fmt_int(max_v)}"
        print(f"{cohort:<18} {posts:>6} {pct:>4.1f}% | {score_range:>14} | {up_str:>18} | {cm_str:>17} | {v_str:>16}")

def main():
    conn = dbmod.get_conn()

    # All posts
    rows = query(conn)
    total = sum(r[1] for r in rows)
    print(f"# Score-cohort distribution, last {DAYS} days (status=active)")
    print(f"# Score = comments*3 + upvotes (Reddit/Moltbook: -1 to strip OP self-upvote)")
    print_table(f"ALL platforms — {total} posts", rows, total)

    # Per platform
    cur = conn.execute(f"""
        SELECT DISTINCT LOWER(platform)
        FROM posts
        WHERE posted_at >= NOW() - INTERVAL '{DAYS} days'
          AND status='active' AND upvotes IS NOT NULL
        ORDER BY 1
    """)
    platforms = [r[0] for r in cur.fetchall()]
    for p in platforms:
        rows = query(conn, where_extra=f"AND LOWER(platform) = '{p}'")
        ptot = sum(r[1] for r in rows)
        print_table(f"{p} — {ptot} posts", rows, ptot)

if __name__ == "__main__":
    main()
