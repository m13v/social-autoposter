#!/usr/bin/env python3
"""One-shot migration: add per-reply engagement stat columns to `replies`.

Idempotent. Safe to re-run. See scripts/migrate_replies_stats.sql for the
canonical SQL definition.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import load_env, get_conn


def main():
    load_env()
    conn = get_conn()
    statements = [
        "ALTER TABLE replies ADD COLUMN IF NOT EXISTS upvotes INTEGER DEFAULT 0",
        "ALTER TABLE replies ADD COLUMN IF NOT EXISTS comments_count INTEGER DEFAULT 0",
        "ALTER TABLE replies ADD COLUMN IF NOT EXISTS views INTEGER DEFAULT 0",
        "ALTER TABLE replies ADD COLUMN IF NOT EXISTS engagement_updated_at TIMESTAMP",
        "CREATE INDEX IF NOT EXISTS idx_replies_engagement_updated_at ON replies(engagement_updated_at)",
    ]
    for s in statements:
        conn.execute(s)
        print("OK:", s[:80])
    conn.commit()

    rows = conn.execute(
        "SELECT column_name, data_type, column_default "
        "FROM information_schema.columns "
        "WHERE table_name='replies' "
        "AND column_name IN ('upvotes','comments_count','views','engagement_updated_at') "
        "ORDER BY column_name"
    ).fetchall()
    print()
    print("replies columns after migration:")
    for r in rows:
        print(f"  {r[0]:<24} {r[1]:<12} default={r[2]}")
    print()
    print("migration applied")


if __name__ == "__main__":
    main()
