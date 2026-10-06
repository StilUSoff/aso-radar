"""SQLite storage."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "data/aso.db")

SCHEMA_VERSION = 3

SCHEMA = """
-- One scan = one pass over all (or some) markets.
CREATE TABLE IF NOT EXISTS scans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,               -- running | done | failed
    partial      INTEGER NOT NULL DEFAULT 0,  -- 1 = only some markets
    total        INTEGER NOT NULL DEFAULT 0,  -- prefixes planned (estimate)
    done         INTEGER NOT NULL DEFAULT 0,  -- prefixes probed
    current      TEXT,                        -- market being scanned
    error        TEXT
);

-- Result of probing one scan unit (storefront + alphabets).
CREATE TABLE IF NOT EXISTS market_scans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id      INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    scan_key     TEXT NOT NULL,
    scanned_at   TEXT NOT NULL,
    prefixes     INTEGER NOT NULL,
    unique_terms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_scans_key ON market_scans(scan_key, id DESC);

-- Top suggestions of a market scan, ranked by popularity score.
CREATE TABLE IF NOT EXISTS keywords (
    market_scan_id INTEGER NOT NULL REFERENCES market_scans(id) ON DELETE CASCADE,
    term           TEXT NOT NULL,
    rank           INTEGER NOT NULL,
    score          REAL NOT NULL,             -- 0..100, top term of the market = 100
    hits           INTEGER NOT NULL,          -- how many prefixes suggested it
    best_prefix    TEXT NOT NULL,             -- shortest prefix that surfaced it
    best_pos       INTEGER NOT NULL,          -- its position there (1-based)
    PRIMARY KEY (market_scan_id, term)
);
CREATE INDEX IF NOT EXISTS idx_keywords_term ON keywords(term);
"""

LEGACY_TABLES = ["checks", "market_meta", "keywords", "apps", "runs"]


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
        if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            # Versions < 3 tracked individual apps; that data model is gone.
            conn.execute("PRAGMA foreign_keys = OFF")
            for t in LEGACY_TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {t}")
            conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        # A crash mid-scan leaves it "running" forever; close it on boot.
        conn.execute(
            "UPDATE scans SET status='failed', error='interrupted', finished_at=? WHERE status='running'",
            (now(),),
        )
