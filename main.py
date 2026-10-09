"""
The RN app POSTs a batch of raw GeoJSON pings to /ingest, which persists
them and returns immediately; the actual ETL pipeline + expo_calx calls run
afterward in a background task, since expo_calx is slow enough (dense days
can need dozens of sequential batch calls) that doing it inline would hold
the HTTP request open for many minutes. Poll GET /ingest/{batch_id} for
completion. etl.py and praise_client.py are unchanged by this - only this
file's wiring and db.py's job-tracking table.
"""

import logging
import threading
from typing import Any, Dict, List

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from . import db, etl, praise_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Exposure ETL backend")


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    incomplete = db.get_incomplete_batches()
    if incomplete:
        logger.info("resuming %d incomplete ingest batch(es) from a previous run", len(incomplete))
        threading.Thread(target=_process_backlog, args=(incomplete,), daemon=True).start()


def _process_backlog(batches: List[Dict[str, Any]]) -> None:
    for b in batches:
        _process_batch(b["id"], b["pid"], b["features"])


class IngestRequest(BaseModel):
    pid: str
    features: List[Dict[str, Any]]  # GeoJSON Features (watch export) and/or live-tracking location objects, may be mixed


@app.post("/ingest", status_code=202)
def ingest(req: IngestRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    pings = etl.parse_pings(req.features)
    if not pings:
        raise HTTPException(400, "no valid pings in request")

    batch_id = db.create_batch(req.pid, req.features)
    logger.info("pid=%s queued batch=%d with %d raw features", req.pid, batch_id, len(req.features))
    background_tasks.add_task(_process_batch, batch_id, req.pid, req.features)
    return {"status": "queued", "batch_id": batch_id}


def _process_batch(batch_id: int, pid: str, features: List[Dict[str, Any]]) -> None:
    db.mark_processing(batch_id)
    logger.info("batch=%d pid=%s processing %d raw features", batch_id, pid, len(features))

    pings = etl.parse_pings(features)
    logger.info("batch=%d pid=%s parsed %d pings", batch_id, pid, len(pings))

    cleaned = etl.remove_spike_outliers(pings)
    logger.info(
        "batch=%d pid=%s spike removal: %d pings remain (%d dropped)",
        batch_id, pid, len(cleaned), len(pings) - len(cleaned),
    )

    dwell_runs = etl.find_dwell_runs(cleaned)
    runs = etl.simplify_outdoor_trails(dwell_runs)
    logger.info(
        "batch=%d pid=%s %d dwell runs -> %d after outdoor trail simplification",
        batch_id, pid, len(dwell_runs), len(runs),
    )
    segments = [s for s in etl.split_by_hour(runs) if s.delta_t_hours > 0]
    logger.info("batch=%d pid=%s %d runs -> %d hourly segments", batch_id, pid, len(runs), len(segments))

    rows = etl.to_request_rows(pid, segments)
    if not rows:
        logger.info("batch=%d pid=%s no rows to send to expo_calx", batch_id, pid)
        db.mark_done(batch_id, 0, 0)
        return

    num_batches = (len(rows) + praise_client.MAX_ROWS_PER_CALL - 1) // praise_client.MAX_ROWS_PER_CALL
    logger.info("batch=%d pid=%s calling expo_calx with %d rows in %d batch(es)", batch_id, pid, len(rows), num_batches)

    try:
        exposure_results = praise_client.calculate_exposure(rows)
    except requests.exceptions.Timeout as e:
        detail = praise_client.redact_secrets(str(e))
        logger.warning("batch=%d pid=%s expo_calx call timed out: %s", batch_id, pid, detail)
        db.mark_error(batch_id, f"expo_calx call timed out: {detail}")
        return
    except requests.exceptions.RequestException as e:
        detail = praise_client.redact_secrets(str(e))
        logger.warning("batch=%d pid=%s expo_calx call failed: %s", batch_id, pid, detail)
        db.mark_error(batch_id, f"expo_calx call failed: {detail}")
        return
    except praise_client.PraiseApiError as e:
        detail = praise_client.redact_secrets(str(e))
        logger.warning("batch=%d pid=%s expo_calx call failed: %s", batch_id, pid, detail)
        db.mark_error(batch_id, f"expo_calx call failed: {detail}")
        return

    logger.info("batch=%d pid=%s expo_calx returned %d results", batch_id, pid, len(exposure_results))

    if len(exposure_results) != len(segments):
        db.mark_error(batch_id, "expo_calx returned a different row count than requested")
        return

    db_rows = []
    for seg, (_t, _pid, value) in zip(segments, exposure_results):
        db_rows.append(
            {
                "pid": pid,
                "hour_start_hk": seg.hour_start_hk.strftime("%Y%m%d%H0000"),
                "start_time_hk": seg.start_time_hk.strftime("%Y%m%d%H%M%S"),
                "io": seg.io,
                "delta_t_hours": seg.delta_t_hours,
                "exposure_value": None if value == -9 else value,
                "lng": seg.lng,
                "lat": seg.lat,
            }
        )
    db.upsert_hourly(db_rows)

    hours_processed = len({seg.hour_start_hk for seg in segments})
    logger.info("batch=%d pid=%s ingest complete: hours_processed=%d rows=%d", batch_id, pid, hours_processed, len(rows))
    db.mark_done(batch_id, hours_processed, len(rows))


@app.get("/ingest/{batch_id}")
def get_ingest_status(batch_id: int) -> Dict[str, Any]:
    batch = db.get_batch(batch_id)
    if batch is None:
        raise HTTPException(404, "unknown batch_id")
    return batch


@app.get("/exposure/hourly/{pid}")
def get_hourly(pid: str) -> List[Dict[str, Any]]:
    """Returns one entry per (hour, IO, run) - more than one row can share an
    hour_start_hk+io if multiple separate runs landed in that hour. Sum
    delta_t_hours/exposure_value across rows sharing an hour_start_hk+io for
    the stacked bar chart; use each row's own lng/lat (in start_time_hk
    order) to draw the route."""
    return db.get_hourly(pid)
