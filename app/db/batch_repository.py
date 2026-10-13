"""Ingest batches: the raw upload plus its background-processing status."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config.constants import BatchStatus
from app.db.connection import NOW_SQL, get_conn
from app.etl.geo import BBox, bbox
from app.etl.models import Ping


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def create_batch(pid: str, features: List[Dict[str, Any]], pings: List[Ping]) -> int:
    """Store a raw batch. `pings` are its parsed, time-sorted pings (at least
    one); their box and time span are kept to find the batch again when a
    place label near it changes."""
    min_lng, min_lat, max_lng, max_lat = bbox(pings)
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO ingest_batches
                (pid, features_json, status, min_lng, min_lat, max_lng, max_lat, first_ping_utc, last_ping_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (pid, json.dumps(features), BatchStatus.PENDING.value, min_lng, min_lat, max_lng, max_lat,
             _to_utc_iso(pings[0].utc), _to_utc_iso(pings[-1].utc)),
        )
        return cur.lastrowid


def mark_processing(batch_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            f"UPDATE ingest_batches SET status = ?, updated_at = {NOW_SQL} WHERE id = ?",
            (BatchStatus.PROCESSING.value, batch_id),
        )


def mark_done(batch_id: int, hours_processed: int, rows: int) -> None:
    with get_conn() as conn:
        conn.execute(
            f"""
            UPDATE ingest_batches
            SET status = ?, hours_processed = ?, rows = ?, updated_at = {NOW_SQL}
            WHERE id = ?
            """,
            (BatchStatus.DONE.value, hours_processed, rows, batch_id),
        )


def mark_error(batch_id: int, detail: str) -> None:
    with get_conn() as conn:
        conn.execute(
            f"UPDATE ingest_batches SET status = ?, error_detail = ?, updated_at = {NOW_SQL} WHERE id = ?",
            (BatchStatus.ERROR.value, detail, batch_id),
        )


def get_batch(batch_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, pid, status, error_detail, hours_processed, rows, created_at, updated_at
            FROM ingest_batches
            WHERE id = ?
            """,
            (batch_id,),
        ).fetchone()
        return dict(row) if row else None


def get_incomplete_batches() -> List[Dict[str, Any]]:
    """Batches left pending/processing, e.g. by a crash mid-batch."""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, pid, features_json FROM ingest_batches WHERE status IN (?, ?) ORDER BY id",
            (BatchStatus.PENDING.value, BatchStatus.PROCESSING.value),
        )
        return [
            {"id": r["id"], "pid": r["pid"], "features": json.loads(r["features_json"])}
            for r in cur.fetchall()
        ]


def find_near(box: BBox) -> List[Dict[str, Any]]:
    """Finished (done or error) batches whose pings' box overlaps `box`, or
    whose box is unknown, oldest first. first/last_ping_utc are datetimes,
    or None for batches stored before they were recorded."""
    min_lng, min_lat, max_lng, max_lat = box
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, pid, features_json, first_ping_utc, last_ping_utc
            FROM ingest_batches
            WHERE status IN (?, ?)
              AND (min_lng IS NULL
                   OR NOT (max_lng < ? OR min_lng > ? OR max_lat < ? OR min_lat > ?))
            ORDER BY id
            """,
            (BatchStatus.DONE.value, BatchStatus.ERROR.value, min_lng, max_lng, min_lat, max_lat),
        )
        return [
            {
                "id": r["id"], "pid": r["pid"], "features": json.loads(r["features_json"]),
                "first_ping_utc": datetime.fromisoformat(r["first_ping_utc"]) if r["first_ping_utc"] else None,
                "last_ping_utc": datetime.fromisoformat(r["last_ping_utc"]) if r["last_ping_utc"] else None,
            }
            for r in cur.fetchall()
        ]
