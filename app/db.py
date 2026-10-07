"""SQLite storage."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "data/aso.db")

SCHEMA_VERSION = 4

SCHEMA = """
-- One scan = one pass over all (or some) markets.
CREATE TABLE IF NOT EXISTS scans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,               -- running | done | stopped | failed
    partial      INTEGER NOT NULL DEFAULT 0,  -- 1 = only some markets
    total        INTEGER NOT NULL DEFAULT 0,  -- prefixes planned (estimate)
    done         INTEGER NOT NULL DEFAULT 0,  -- prefixes probed
    current      TEXT,                        -- market being scanned
    paused_until TEXT,                        -- waiting out Apple's rate limit
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

-- ---- App tracking: where a given app ranks for chosen queries per market ----

CREATE TABLE IF NOT EXISTS apps (
    id          INTEGER PRIMARY KEY,          -- App Store (Apple) ID
    name        TEXT NOT NULL,
    bundle_id   TEXT,
    icon        TEXT,
    seller      TEXT,
    markets     TEXT,                         -- JSON list of tracked market locales
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_keywords (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id      INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    locale      TEXT NOT NULL,                -- market, see markets.MARKETS
    country     TEXT NOT NULL,                -- storefront it is searched in
    term        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (app_id, locale, term)
);

CREATE TABLE IF NOT EXISTS app_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id      INTEGER,                      -- NULL = all apps
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,                -- running | done | stopped | failed
    total       INTEGER NOT NULL DEFAULT 0,
    done        INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS app_checks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword_id    INTEGER NOT NULL REFERENCES app_keywords(id) ON DELETE CASCADE,
    run_id        INTEGER,
    checked_at    TEXT NOT NULL,
    rank          INTEGER,                    -- NULL = not in the top 200
    total_results INTEGER NOT NULL,
    top_apps      TEXT                        -- JSON: top 3 [{id, name, icon}]
);
CREATE INDEX IF NOT EXISTS idx_app_checks_kw ON app_checks(keyword_id, checked_at DESC);

-- Store listing per market. title/subtitle/keywords: from App Store Connect or
-- typed in by hand; store_*: what the public App Store page shows.
CREATE TABLE IF NOT EXISTS app_meta (
    app_id         INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    locale         TEXT NOT NULL,
    title          TEXT,
    subtitle       TEXT,
    keywords       TEXT,
    store_title    TEXT,
    store_subtitle TEXT,
    iap_names      TEXT,                      -- JSON list (App Store Connect)
    version        TEXT,                      -- app version the metadata is from
    source         TEXT,                      -- asc | manual
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (app_id, locale)
);

-- App Store Connect API keys; one key covers all apps of a developer account.
CREATE TABLE IF NOT EXISTS asc_accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    key_id      TEXT NOT NULL,
    issuer_id   TEXT NOT NULL,
    private_key TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""

# Columns added after a table first shipped: (table, column, type).
ADDED_COLUMNS = [
    ("apps", "markets", "TEXT"),
    ("scans", "paused_until", "TEXT"),
    ("app_runs", "paused_until", "TEXT"),
    ("app_meta", "store_subtitle", "TEXT"),
    ("app_meta", "iap_names", "TEXT"),
    ("app_meta", "version", "TEXT"),
    ("app_meta", "source", "TEXT"),
]

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
        if conn.execute("PRAGMA user_version").fetchone()[0] < 3:
            # Versions < 3 had an older app-tracking model; start it afresh.
            conn.execute("PRAGMA foreign_keys = OFF")
            for t in LEGACY_TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {t}")
            conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        for table, column, kind in ADDED_COLUMNS:
            if not conn.execute(f"SELECT 1 FROM pragma_table_info('{table}') WHERE name = ?", (column,)).fetchone():
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        # A crash mid-scan leaves it "running" forever; close it on boot.
        for table in ("scans", "app_runs"):
            conn.execute(
                f"UPDATE {table} SET status='failed', error='interrupted', finished_at=? WHERE status='running'",
                (now(),),
            )


def after(seconds: float) -> str:
    return datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + seconds, timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
