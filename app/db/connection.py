"""
SQLite connection and schema. The repositories in this package are the only
code that talks to the database, so swapping SQLite for Postgres/etc. later
stays confined to app/db.
"""

import sqlite3
from contextlib import contextmanager
from typing import Iterator, List, Tuple

from app.config.constants import BatchStatus, IoSource
from app.config.settings import get_settings

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS hourly_exposure (
    pid TEXT NOT NULL,
    hour_start_hk TEXT NOT NULL,   -- 'YYYYMMDDHH0000', HK local - hour bucket, for chart grouping
    start_time_hk TEXT NOT NULL,   -- 'YYYYMMDDHHMMSS', HK local - this stay's own precise start time
    io TEXT NOT NULL,              -- io type name, see app/io_types
    io_source TEXT NOT NULL DEFAULT '{IoSource.INFERRED.value}',  -- see constants.IoSource
    delta_t_hours REAL NOT NULL,
    exposure_value REAL,           -- NULL if expo_calx reported this row as failed
    lng REAL,
    lat REAL,
    in_hk INTEGER NOT NULL DEFAULT 1,  -- 1 inside Hong Kong; 0 outside (not sent to expo_calx, exposure_value NULL)
    api_result_json TEXT,          -- the expo_calx result item as returned, kept for fields not yet in their own column
    PRIMARY KEY (pid, io, start_time_hk)
);

CREATE TABLE IF NOT EXISTS ingest_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pid TEXT NOT NULL,
    features_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '{BatchStatus.PENDING.value}',  -- see constants.BatchStatus
    error_detail TEXT,
    hours_processed INTEGER,
    rows INTEGER,
    -- Where and when the batch's pings are, so a place label change can find
    -- the batches to re-run. NULL for batches stored before these columns.
    min_lng REAL,
    min_lat REAL,
    max_lng REAL,
    max_lat REAL,
    first_ping_utc TEXT,           -- UTC ISO 8601
    last_ping_utc TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Shared, crowd-sourced place labels (PUT /place-labels/{{label_id}}).
-- Never returned to other clients: Home circles are sensitive.
CREATE TABLE IF NOT EXISTS place_labels (
    label_id TEXT PRIMARY KEY,     -- client-generated
    contributor_id TEXT NOT NULL,  -- random per-install id, not linked to a pid
    category TEXT NOT NULL,        -- an io type name, see config.io_types.PLACE_LABEL_CATEGORIES
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    radius_m REAL NOT NULL,
    updated_at TEXT NOT NULL,      -- client's updated_at, as UTC ISO 8601
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""

# Columns added after a table was first created. CREATE TABLE IF NOT EXISTS
# skips existing tables, so init_db adds these to an older database file.
ADDED_COLUMNS: List[Tuple[str, str, str]] = [
    ("hourly_exposure", "api_result_json", "TEXT"),
    ("hourly_exposure", "in_hk", "INTEGER NOT NULL DEFAULT 1"),
    ("hourly_exposure", "io_source", f"TEXT NOT NULL DEFAULT '{IoSource.INFERRED.value}'"),
    ("ingest_batches", "min_lng", "REAL"),
    ("ingest_batches", "min_lat", "REAL"),
    ("ingest_batches", "max_lng", "REAL"),
    ("ingest_batches", "max_lat", "REAL"),
    ("ingest_batches", "first_ping_utc", "TEXT"),
    ("ingest_batches", "last_ping_utc", "TEXT"),
]

# SQL expression for "now" in the timestamp format the tables use.
NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(get_settings().exposure_db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for table, column, sql_type in ADDED_COLUMNS:
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
