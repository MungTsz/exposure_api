"""
Place labels: shared circles users put on a map (see
app/io_types/place_labels.py). Only the owner (contributor_id) may change or
delete a label, and no read here is ever returned to other clients.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.db.connection import get_conn
from app.io_types.place_labels import PlaceLabel

_COLUMNS = "label_id, contributor_id, category, lat, lng, radius_m, updated_at"


class UpsertOutcome(Enum):
    CREATED = "created"
    UPDATED = "updated"
    IGNORED = "ignored"      # the stored label is as new or newer
    FORBIDDEN = "forbidden"  # the label belongs to another contributor


class DeleteOutcome(Enum):
    DELETED = "deleted"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _get(conn, label_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(f"SELECT {_COLUMNS} FROM place_labels WHERE label_id = ?", (label_id,)).fetchone()
    return dict(row) if row else None


def to_place_label(stored: Dict[str, Any]) -> PlaceLabel:
    """A stored label (as upsert/delete return it) as a PlaceLabel."""
    return PlaceLabel(**{k: stored[k] for k in ("label_id", "contributor_id", "category", "lng", "lat", "radius_m")})


def upsert(
    label_id: str, contributor_id: str, category: str, lat: float, lng: float, radius_m: float,
    updated_at: datetime,
) -> Tuple[UpsertOutcome, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Create or update a label, unless it belongs to another contributor or
    the stored copy is as new or newer. Returns the outcome, the label as now
    stored (None if FORBIDDEN) and the label as stored before (None unless
    UPDATED). `updated_at` must be timezone-aware."""
    values = {
        "label_id": label_id, "contributor_id": contributor_id, "category": category,
        "lat": lat, "lng": lng, "radius_m": radius_m, "updated_at": _to_utc_iso(updated_at),
    }
    with get_conn() as conn:
        # Lock before reading so a concurrent retry can't interleave.
        conn.execute("BEGIN IMMEDIATE")
        stored = _get(conn, label_id)
        if stored is None:
            conn.execute(
                f"INSERT INTO place_labels ({_COLUMNS}) "
                "VALUES (:label_id, :contributor_id, :category, :lat, :lng, :radius_m, :updated_at)",
                values,
            )
            return UpsertOutcome.CREATED, values, None
        if stored["contributor_id"] != contributor_id:
            return UpsertOutcome.FORBIDDEN, None, None
        if datetime.fromisoformat(stored["updated_at"]) >= updated_at:
            return UpsertOutcome.IGNORED, stored, None
        conn.execute(
            """
            UPDATE place_labels
            SET category = :category, lat = :lat, lng = :lng, radius_m = :radius_m, updated_at = :updated_at
            WHERE label_id = :label_id
            """,
            values,
        )
        return UpsertOutcome.UPDATED, values, stored


def delete(label_id: str, contributor_id: str) -> Tuple[DeleteOutcome, Optional[Dict[str, Any]]]:
    """Returns the outcome and, if DELETED, the label as it was stored."""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        stored = _get(conn, label_id)
        if stored is None:
            return DeleteOutcome.NOT_FOUND, None
        if stored["contributor_id"] != contributor_id:
            return DeleteOutcome.FORBIDDEN, None
        conn.execute("DELETE FROM place_labels WHERE label_id = ?", (label_id,))
        return DeleteOutcome.DELETED, stored


def get_all() -> List[PlaceLabel]:
    """Every stored label, for classifying stays. Internal use only."""
    with get_conn() as conn:
        cur = conn.execute("SELECT label_id, contributor_id, category, lng, lat, radius_m FROM place_labels")
        return [to_place_label(dict(r)) for r in cur.fetchall()]
