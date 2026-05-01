#!/usr/bin/env python3
"""One-shot migration: dm_links child table + dms.target_projects[].

Idempotent. Safe to re-run. Backfills existing dms.short_link_code rows into
dm_links so the resolver works continuously through the cutover. The legacy
dms.short_link_* columns stay in place for one release; a follow-up PR drops
them after dashboard click counts match.

Run from repo root:
    python3 scripts/migrate_dm_links.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import load_env, get_conn


def main():
    load_env()
    conn = get_conn()

    statements = [
        # New child table: one row per minted short link. Multi-link, multi-turn.
        """
        CREATE TABLE IF NOT EXISTS dm_links (
            code TEXT PRIMARY KEY,
            dm_id INTEGER NOT NULL REFERENCES dms(id) ON DELETE CASCADE,
            target_url TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'web',
            project_at_mint TEXT,
            message_id INTEGER REFERENCES dm_messages(id) ON DELETE SET NULL,
            minted_at TIMESTAMP NOT NULL DEFAULT NOW(),
            clicks INTEGER NOT NULL DEFAULT 0,
            first_click_at TIMESTAMP,
            last_click_at TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_dm_links_dm_id ON dm_links(dm_id)",
        # Idempotent re-mint: same DM + same target URL returns existing code.
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dm_links_dm_target ON dm_links(dm_id, target_url)",

        # Multi-project DM threads. target_project remains the canonical/primary
        # (last-set wins). target_projects is the union; the wrap-tool checks
        # against this when deciding whether a URL is allowed.
        "ALTER TABLE dms ADD COLUMN IF NOT EXISTS target_projects TEXT[] NOT NULL DEFAULT '{}'",
    ]

    for s in statements:
        conn.execute(s)
        first_line = ' '.join(s.split())[:90]
        print("OK:", first_line)
    conn.commit()

    # Backfill target_projects from target_project for existing rows. Idempotent
    # via the empty-array guard.
    conn.execute("""
        UPDATE dms
        SET target_projects = ARRAY[target_project]
        WHERE target_project IS NOT NULL
          AND target_project <> ''
          AND target_projects = '{}'
    """)
    conn.commit()
    backfilled_tp = conn.execute(
        "SELECT COUNT(*) AS n FROM dms WHERE array_length(target_projects, 1) > 0"
    ).fetchone()
    print(f"target_projects backfill: {backfilled_tp['n']} dms rows now have at least one project")

    # Backfill existing short_link_code rows into dm_links so the resolver keeps
    # working during the cutover. ON CONFLICT (code) makes this idempotent.
    conn.execute("""
        INSERT INTO dm_links (code, dm_id, target_url, kind, project_at_mint,
                              minted_at, clicks, first_click_at, last_click_at)
        SELECT
            short_link_code,
            id,
            short_link_target_url,
            'booking',
            target_project,
            COALESCE(booking_link_sent_at, NOW()),
            COALESCE(short_link_clicks, 0),
            short_link_first_click_at,
            short_link_last_click_at
        FROM dms
        WHERE short_link_code IS NOT NULL
          AND short_link_target_url IS NOT NULL
        ON CONFLICT (code) DO NOTHING
    """)
    conn.commit()

    counts = conn.execute("""
        SELECT
            (SELECT COUNT(*) FROM dm_links) AS dm_links_total,
            (SELECT COUNT(*) FROM dms WHERE short_link_code IS NOT NULL) AS legacy_short_link_total
    """).fetchone()
    print(f"dm_links total: {counts['dm_links_total']}")
    print(f"legacy short_link_code rows: {counts['legacy_short_link_total']} (expected to match)")

    # Schema sanity print.
    cols = conn.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'dm_links'
        ORDER BY ordinal_position
    """).fetchall()
    print()
    print("dm_links columns:")
    for c in cols:
        print(f"  {c['column_name']:<20} {c['data_type']}")

    tp = conn.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name='dms' AND column_name='target_projects'
    """).fetchone()
    print()
    if tp:
        print(f"dms.target_projects: {tp['data_type']}")
    else:
        print("WARN: dms.target_projects missing")

    print()
    print("migration applied")


if __name__ == "__main__":
    main()
