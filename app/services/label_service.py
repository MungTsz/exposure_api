"""
Re-runs stored batches after a place label is created, moved or deleted, so
rows already stored follow the labels (e.g. a night stored as Home becomes
Office once someone puts an Office circle there).

A label can only change a stay with a ping within its radius plus
PLACE_LABEL_DRIFT_MARGIN_M (see app/io_types/registry.py), so only batches
with such a ping are re-run. process_batch loads the current labels and
replaces the batch's own rows, so re-running is all it takes. Every re-run
calls expo_calx again, for every pid near the label (labels are shared).
"""

import logging
import threading
from dataclasses import replace
from typing import Any, Dict, List, Sequence

from app.config import io_types as io_cfg
from app.db import batch_repository
from app.etl.geo import circle_bbox, within_circle
from app.etl.parsers import parse_pings
from app.io_types.place_labels import PlaceLabel
from app.services import ingest_service

logger = logging.getLogger(__name__)

# One re-run at a time: each one reads the labels when it starts, so the last
# to run - queued by the newest label change - always sees the newest labels.
_lock = threading.Lock()


def reprocess_near(labels: Sequence[PlaceLabel]) -> None:
    """Re-run every finished batch with a ping near any of `labels` (the
    label as now stored and/or as it was before). Never raises."""
    try:
        with _lock:
            batches = _batches_near(labels)
            logger.info(
                "place label change: re-running %d batch(es) %s",
                len(batches), [b["id"] for b in batches],
            )
            ingest_service.process_batches(batches)
    except Exception:
        logger.exception("place label change: re-running batches failed")


def _batches_near(labels: Sequence[PlaceLabel]) -> List[Dict[str, Any]]:
    found: Dict[int, Dict[str, Any]] = {}
    for label in labels:
        grown = replace(label, radius_m=label.radius_m + io_cfg.PLACE_LABEL_DRIFT_MARGIN_M)
        for batch in batch_repository.find_near(circle_bbox(grown)):
            if batch["id"] not in found and any(within_circle(grown, p) for p in parse_pings(batch["features"])):
                found[batch["id"]] = batch
    batches = sorted(found.values(), key=lambda b: b["id"])
    return [b for b in batches if not _covered_by_later(b, batches)]


def _covered_by_later(batch: Dict[str, Any], batches: List[Dict[str, Any]]) -> bool:
    """True if a later batch of the same pid spans all of this one's time
    (e.g. the same file uploaded again): re-running that one replaces this
    one's rows anyway."""
    if batch["first_ping_utc"] is None:
        return False
    return any(
        later["id"] > batch["id"] and later["pid"] == batch["pid"] and later["first_ping_utc"] is not None
        and later["first_ping_utc"] <= batch["first_ping_utc"] and later["last_ping_utc"] >= batch["last_ping_utc"]
        for later in batches
    )
