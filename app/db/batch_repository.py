"""Ingest batches: the raw upload plus its background-processing status."""

import json
from typing import Any, Dict, List, Optional

from app.config.constants import BatchStatus
from app.db.connection import NOW_SQL, get_conn


def create_batch(pid: str, features: List[Dict[str, Any]]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ingest_batches (pid, features_json, status) VALUES (?, ?, ?)",
            (pid, json.dumps(features), BatchStatus.PENDING.value),
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
