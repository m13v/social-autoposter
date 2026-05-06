#!/usr/bin/env python3
"""One-shot migration: post_links child table.

Mirrors dm_links but for PUBLIC posts/comments (Reddit, Twitter/X, LinkedIn,
GitHub). Every outbound URL we paste into a public thread gets minted into
post_links so we can attribute clicks back to the originating post or reply.

post_id and reply_id are BOTH nullable because we mint codes BEFORE the
platform call returns a permalink; log_post (or reply_db) inserts the row,
returns its id, and a follow-up backfill UPDATE stamps post_links with that
id. Orphan rows (post_id IS NULL AND reply_id IS NULL) are still resolvable
(the redirect uses target_url frozen at mint time) and just have no
attribution; that is acceptable since the post failed to land anyway.

minted_session is a UUID grouping every code minted by one wrap_text_for_post
call so the caller can backfill all of them in one UPDATE after log_post
returns the row id.

Idempotent. Safe to re-run.

Run from repo root:
    python3 scripts/migrate_post_links.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import load_env, get_conn


def main():
    load_env()
    conn = get_conn()

    statements = [
        # Child table: one row per minted short link for a public post or reply.
        # post_id and reply_id are both nullable; exactly one is expected to be
        # populated after backfill (or both NULL if the platform call failed).
        """
        CREATE TABLE IF NOT EXISTS post_links (
            code TEXT PRIMARY KEY,
            post_id INTEGER REFERENCES posts(id) ON DELETE SET NULL,
            reply_id INTEGER REFERENCES replies(id) ON DELETE SET NULL,
            platform TEXT NOT NULL,
            project_name TEXT,
            target_url TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'web',
            project_at_mint TEXT,
            minted_session TEXT,
            minted_at TIMESTAMP NOT NULL DEFAULT NOW(),
            clicks INTEGER NOT NULL DEFAULT 0,
            first_click_at TIMESTAMP,
            last_click_at TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_post_links_post_id ON post_links(post_id)",
        "CREATE INDEX IF NOT EXISTS idx_post_links_reply_id ON post_links(reply_id)",
        "CREATE INDEX IF NOT EXISTS idx_post_links_minted_session ON post_links(minted_session)",
        "CREATE INDEX IF NOT EXISTS idx_post_links_platform_project ON post_links(platform, project_name)",
    ]

    for s in statements:
        conn.execute(s)
        first_line = ' '.join(s.split())[:90]
        print("OK:", first_line)
    conn.commit()

    # Schema sanity print.
    cols = conn.execute("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'post_links'
        ORDER BY ordinal_position
    """).fetchall()
    print()
    print("post_links columns:")
    for c in cols:
        print(f"  {c['column_name']:<20} {c['data_type']:<25} nullable={c['is_nullable']}")

    counts = conn.execute("SELECT COUNT(*) AS n FROM post_links").fetchone()
    print()
    print(f"post_links rows: {counts['n']}")
    print()
    print("migration applied")


if __name__ == "__main__":
    main()
