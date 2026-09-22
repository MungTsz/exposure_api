"""
Minimal SQLite storage for hourly indoor/outdoor exposure results.

One row per (pid, IO label, run) - a clock hour can hold more than one row
of the same label if multiple separate runs land in it (e.g. two distinct
outdoor trips within the same hour), so callers sum delta_t_hours/
exposure_value across rows sharing an hour_start_hk+io for the chart, and
use each row's own lng/lat (ordered by start_time_hk) to draw a route.
Swap sqlite3 for Postgres/etc. later without changing the shape of this
module's public functions.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

DB_PATH = os.environ.get("EXPOSURE_DB_PATH", "exposure.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS hourly_exposure (
    pid TEXT NOT NULL,
    hour_start_hk TEXT NOT NULL,   -- 'YYYYMMDDHH0000', HK local - hour bucket, for chart grouping
    start_time_hk TEXT NOT NULL,   -- 'YYYYMMDDHHMMSS', HK local - this run's own precise start time
    io TEXT NOT NULL,              -- 'Indoor' | 'Outdoor'
    delta_t_hours REAL NOT NULL,
    exposure_value REAL,           -- NULL if expo_calx returned -9 for this row
    lng REAL,
    lat REAL,
    PRIMARY KEY (pid, io, start_time_hk)
);

CREATE TABLE IF NOT EXISTS ingest_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pid TEXT NOT NULL,
    features_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | processing | done | error
    error_detail TEXT,
    hours_processed INTEGER,
    rows INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def upsert_hourly(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO hourly_exposure (pid, hour_start_hk, start_time_hk, io, delta_t_hours, exposure_value, lng, lat)
            VALUES (:pid, :hour_start_hk, :start_time_hk, :io, :delta_t_hours, :exposure_value, :lng, :lat)
            ON CONFLICT(pid, io, start_time_hk) DO UPDATE SET
                hour_start_hk = excluded.hour_start_hk,
                delta_t_hours = excluded.delta_t_hours,
                exposure_value = excluded.exposure_value,
                lng = excluded.lng,
                lat = excluded.lat
            """,
            rows,
        )


def get_hourly(pid: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT hour_start_hk, start_time_hk, io, delta_t_hours, exposure_value, lng, lat
            FROM hourly_exposure
            WHERE pid = ?
            ORDER BY start_time_hk
            """,
            (pid,),
        )
        return [dict(r) for r in cur.fetchall()]


def create_batch(pid: str, features: List[Dict[str, Any]]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ingest_batches (pid, features_json) VALUES (?, ?)",
            (pid, json.dumps(features)),
        )
        return cur.lastrowid


def mark_processing(batch_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE ingest_batches SET status = 'processing', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
            (batch_id,),
        )


def mark_done(batch_id: int, hours_processed: int, rows: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE ingest_batches
            SET status = 'done', hours_processed = ?, rows = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            (hours_processed, rows, batch_id),
        )


def mark_error(batch_id: int, detail: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE ingest_batches
            SET status = 'error', error_detail = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            (detail, batch_id),
        )


def get_batch(batch_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id, pid, status, error_detail, hours_processed, rows, created_at, updated_at
            FROM ingest_batches
            WHERE id = ?
            """,
            (batch_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_incomplete_batches() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id, pid, features_json FROM ingest_batches WHERE status IN ('pending', 'processing') ORDER BY id"
        )
        return [
            {"id": r["id"], "pid": r["pid"], "features": json.loads(r["features_json"])}
            for r in cur.fetchall()
        ]
