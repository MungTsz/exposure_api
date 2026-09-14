"""
Simple synchronous backend: the RN app POSTs a batch of raw GeoJSON pings
here, this process runs the full ETL pipeline and calls expo_calx directly
in the same request, then persists results. Good enough for one user's
daily ping volume; if this gets slow under real traffic, split /ingest
into "write raw pings, return immediately" + a queue worker that does the
ETL + API call asynchronously - the etl/praise_client/db modules don't
need to change, only how main.py wires them together.
"""

from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import db, etl, praise_client

app = FastAPI(title="Exposure ETL backend")


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()


class IngestRequest(BaseModel):
    pid: str
    features: List[Dict[str, Any]]  # GeoJSON Features (watch export) and/or live-tracking location objects, may be mixed


@app.post("/ingest")
def ingest(req: IngestRequest) -> Dict[str, int]:
    pings = etl.parse_pings(req.features)
    if not pings:
        raise HTTPException(400, "no valid pings in request")

    cleaned = etl.remove_spike_outliers(pings)
    runs = etl.find_dwell_runs(cleaned)
    segments = [s for s in etl.split_by_hour(runs) if s.delta_t_hours > 0]

    rows = etl.to_request_rows(req.pid, segments)
    if not rows:
        return {"hours_processed": 0, "rows": 0}

    try:
        exposure_results = praise_client.calculate_exposure(rows)
    except praise_client.PraiseApiError as e:
        raise HTTPException(502, f"expo_calx call failed: {e}")

    if len(exposure_results) != len(segments):
        raise HTTPException(502, "expo_calx returned a different row count than requested")

    db_rows = []
    for seg, (_t, _pid, value) in zip(segments, exposure_results):
        db_rows.append(
            {
                "pid": req.pid,
                "hour_start_hk": seg.hour_start_hk.strftime("%Y%m%d%H0000"),
                "io": seg.io,
                "delta_t_hours": seg.delta_t_hours,
                "exposure_value": None if value == -9 else value,
                "lng": seg.lng,
                "lat": seg.lat,
            }
        )
    db.upsert_hourly(db_rows)

    hours_processed = len({seg.hour_start_hk for seg in segments})
    return {"hours_processed": hours_processed, "rows": len(rows)}


@app.get("/exposure/hourly/{pid}")
def get_hourly(pid: str) -> List[Dict[str, Any]]:
    """Returns one entry per (hour, IO) - ready to feed straight into a stacked bar chart."""
    return db.get_hourly(pid)
