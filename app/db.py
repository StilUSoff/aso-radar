"""SQLite storage."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "data/aso.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
    id          INTEGER PRIMARY KEY,          -- App Store (Apple) ID
    name        TEXT NOT NULL,
    bundle_id   TEXT,
    icon        TEXT,
    seller      TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keywords (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id      INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    country     TEXT NOT NULL,
    term        TEXT NOT NULL,
    locale      TEXT,
    source      TEXT NOT NULL DEFAULT 'manual',
    created_at  TEXT NOT NULL,
    UNIQUE (app_id, country, term)
);

CREATE TABLE IF NOT EXISTS checks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword_id    INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
    run_id        INTEGER,
    checked_at    TEXT NOT NULL,
    rank          INTEGER,                    -- NULL = not in top results
    total_results INTEGER NOT NULL,
    top_apps      TEXT                        -- JSON: [{id, name, icon}] top 3
);
CREATE INDEX IF NOT EXISTS idx_checks_kw ON checks(keyword_id, checked_at DESC);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id      INTEGER,                      -- NULL = all apps
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,                -- running | done | failed
    total       INTEGER NOT NULL DEFAULT 0,
    done        INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with tx() as conn:
        conn.executescript(SCHEMA)
        # A crash mid-run leaves it "running" forever; close it on boot.
        conn.execute(
            "UPDATE runs SET status='failed', error='interrupted', finished_at=? WHERE status='running'",
            (now(),),
        )


# Latest and previous check per keyword, in one query.
KEYWORDS_WITH_RANKS = """
WITH ranked AS (
    SELECT c.*, ROW_NUMBER() OVER (PARTITION BY keyword_id ORDER BY c.checked_at DESC, c.id DESC) AS rn
    FROM checks c
    JOIN keywords k ON k.id = c.keyword_id
    WHERE k.app_id = :app_id
)
SELECT k.id, k.country, k.term, k.locale, k.source,
       cur.rank AS rank, cur.checked_at AS checked_at, cur.total_results AS total_results,
       cur.top_apps AS top_apps, prev.rank AS prev_rank, prev.checked_at AS prev_checked_at
FROM keywords k
LEFT JOIN ranked cur  ON cur.keyword_id  = k.id AND cur.rn = 1
LEFT JOIN ranked prev ON prev.keyword_id = k.id AND prev.rn = 2
WHERE k.app_id = :app_id
ORDER BY k.country, COALESCE(cur.rank, 100000), k.term
"""
