#!/usr/bin/env python3
"""Shared Neon Postgres connection for social-autoposter.

Provides a thin psycopg2 wrapper with a sqlite3-compatible API so all
scripts can use the same SQL without changes to query logic.

DATABASE_URL is read from ~/social-autoposter/.env (pre-filled on install).
"""

import os
import re
import sys

ENV_PATH = os.path.expanduser("~/social-autoposter/.env")


def load_env():
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())


def _translate_sql(sql):
    """Translate SQLite-specific SQL syntax to PostgreSQL."""
    # ? placeholders -> %s
    sql = sql.replace('?', '%s')
    # datetime('now', '-N hours') -> NOW() - INTERVAL 'N hours'
    sql = re.sub(r"datetime\('now',\s*'-(\d+) hours'\)", r"NOW() - INTERVAL '\1 hours'", sql)
    # datetime('now', '-N days') -> NOW() - INTERVAL 'N days'
    sql = re.sub(r"datetime\('now',\s*'-(\d+) days'\)", r"NOW() - INTERVAL '\1 days'", sql)
    # datetime('now') -> NOW()
    sql = re.sub(r"datetime\('now'\)", 'NOW()', sql)
    # status_checked_at=datetime('now') already handled above
    return sql


class PGConn:
    """Thin psycopg2 wrapper with a sqlite3-compatible execute/commit/close API."""

    def __init__(self, conn):
        import psycopg2.extras
        self._conn = conn
        self._cursor_factory = psycopg2.extras.DictCursor

    def execute(self, sql, params=None):
        cur = self._conn.cursor(cursor_factory=self._cursor_factory)
        sql = _translate_sql(sql)
        if params is not None:
            cur.execute(sql, list(params))
        else:
            cur.execute(sql)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    # No-op to absorb sqlite3.Row assignments
    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, val):
        pass


def get_conn():
    """Return a PGConn connected to the central Neon database."""
    load_env()
    url = os.environ.get('DATABASE_URL')
    if not url:
        print("ERROR: DATABASE_URL not set in ~/social-autoposter/.env", file=sys.stderr)
        print("  Re-run: npx social-autoposter init", file=sys.stderr)
        sys.exit(1)
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2-binary not installed.", file=sys.stderr)
        print("  Run: pip3 install psycopg2-binary", file=sys.stderr)
        sys.exit(1)
    conn = psycopg2.connect(url)
    return PGConn(conn)
