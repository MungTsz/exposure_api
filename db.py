"""
Minimal SQLite storage for hourly indoor/outdoor exposure results.

One row per (pid, hour, IO label) - exactly the two stacked segments a
per-hour bar chart needs. Swap sqlite3 for Postgres/etc. later without
changing the shape of this module's public functions.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

DB_PATH = os.environ.get("EXPOSURE_DB_PATH", "exposure.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS hourly_exposure (
    pid TEXT NOT NULL,
    hour_start_hk TEXT NOT NULL,   -- 'YYYYMMDDHH0000', HK local
    io TEXT NOT NULL,              -- 'Indoor' | 'Outdoor'
    delta_t_hours REAL NOT NULL,
    exposure_value REAL,           -- NULL if expo_calx returned -9 for this row
    lng REAL,
    lat REAL,
    PRIMARY KEY (pid, hour_start_hk, io)
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
        conn.execute(SCHEMA)


def upsert_hourly(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO hourly_exposure (pid, hour_start_hk, io, delta_t_hours, exposure_value, lng, lat)
            VALUES (:pid, :hour_start_hk, :io, :delta_t_hours, :exposure_value, :lng, :lat)
            ON CONFLICT(pid, hour_start_hk, io) DO UPDATE SET
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
            SELECT hour_start_hk, io, delta_t_hours, exposure_value, lng, lat
            FROM hourly_exposure
            WHERE pid = ?
            ORDER BY hour_start_hk
            """,
            (pid,),
        )
        return [dict(r) for r in cur.fetchall()]
