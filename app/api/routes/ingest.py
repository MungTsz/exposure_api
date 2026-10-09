"""POST /ingest (queue a batch) and GET /ingest/{batch_id} (poll its status)."""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.api.schemas import BatchStatusOut, IngestQueued, IngestRequest
from app.config.constants import INGEST_QUEUED_STATUS
from app.db import batch_repository
from app.etl.parsers import parse_pings
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
    if not parse_pings(req.features):
        raise HTTPException(400, "no valid pings in request")

    batch_id = batch_repository.create_batch(req.pid, req.features)
    logger.info("pid=%s queued batch=%d with %d raw features", req.pid, batch_id, len(req.features))
    background_tasks.add_task(ingest_service.process_batch, batch_id, req.pid, req.features)
    return IngestQueued(status=INGEST_QUEUED_STATUS, batch_id=batch_id)


@router.get("/ingest/{batch_id}", response_model=BatchStatusOut)
def get_ingest_status(batch_id: int) -> BatchStatusOut:
    batch = batch_repository.get_batch(batch_id)
    if batch is None:
        raise HTTPException(404, "unknown batch_id")
    return BatchStatusOut(**batch)
