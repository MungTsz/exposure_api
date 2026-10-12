"""POST /ingest (queue a JSON batch), POST /ingest/gpx (queue a GPX file) and
GET /ingest/{batch_id} (poll its status)."""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.api.schemas import BatchStatusOut, IngestQueued, IngestRequest
from app.config.constants import GPX_MEDIA_TYPE, INGEST_QUEUED_STATUS
from app.db import batch_repository
from app.etl.parsers import parse_pings
from app.etl.parsers.gpx import GpxError, read_gpx
from app.services import ingest_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])


@router.post(
    "/ingest",
    status_code=202,
    response_model=IngestQueued,
    responses={
        400: {"description": "no valid pings in request"},
        413: {"description": "request body larger than settings.max_ingest_body_bytes; nothing is stored"},
    },
)
def ingest(req: IngestRequest, background_tasks: BackgroundTasks) -> IngestQueued:
    """Store the raw batch and return at once; processing runs in the background."""
    return _queue_batch(req.pid, req.features, background_tasks)


@router.post(
    "/ingest/gpx",
    status_code=202,
    response_model=IngestQueued,
    responses={
        400: {"description": "not a readable GPX file, or no valid track points in it"},
        413: {"description": "request body larger than settings.max_ingest_body_bytes; nothing is stored"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {GPX_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def ingest_gpx(
    request: Request,
    background_tasks: BackgroundTasks,
    pid: str = Query(..., description="person/device id; every result row is stored under it"),
) -> IngestQueued:
    """Same as POST /ingest, but the body is a whole GPX file. Each <trkpt>
    becomes one stored record (see app/etl/parsers/gpx.py)."""
    data = await request.body()
    try:
        records = read_gpx(data)
    except GpxError as e:
        raise HTTPException(400, f"invalid GPX: {e}") from e
    # Parsing and the SQLite insert are blocking; keep them off the event loop.
    return await run_in_threadpool(_queue_batch, pid, records, background_tasks)


def _queue_batch(pid: str, features: List[Dict[str, Any]], background_tasks: BackgroundTasks) -> IngestQueued:
    if not parse_pings(features):
        raise HTTPException(400, "no valid pings in request")

    batch_id = batch_repository.create_batch(pid, features)
    logger.info("pid=%s queued batch=%d with %d raw features", pid, batch_id, len(features))
    background_tasks.add_task(ingest_service.process_batch, batch_id, pid, features)
    return IngestQueued(status=INGEST_QUEUED_STATUS, batch_id=batch_id)


@router.get("/ingest/{batch_id}", response_model=BatchStatusOut)
def get_ingest_status(batch_id: int) -> BatchStatusOut:
    batch = batch_repository.get_batch(batch_id)
    if batch is None:
        raise HTTPException(404, "unknown batch_id")
    return BatchStatusOut(**batch)
