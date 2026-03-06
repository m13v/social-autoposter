"""Database helpers for social_autoposter."""

import json
import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = os.path.expanduser("~/social-autoposter/social_posts.db")
CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")


def get_db_path() -> str:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return os.path.expanduser(cfg.get("database", DEFAULT_DB_PATH))
    return DEFAULT_DB_PATH


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]
