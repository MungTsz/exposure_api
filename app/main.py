"""
FastAPI entry point: `uvicorn app.main:app` from the repo root.

On startup the DB schema is created/updated and any batch left
pending/processing by a previous run (e.g. a crash mid-batch) is resumed in
a background thread.
"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.body_limit import MaxBodySizeMiddleware
from app.api.routes import exposure, ingest, place_labels
from app.config.constants import APP_TITLE, LOG_FORMAT
from app.config.settings import get_settings
from app.db import batch_repository
from app.db.connection import init_db
from app.services import ingest_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    incomplete = batch_repository.get_incomplete_batches()
    if incomplete:
        logger.info("resuming %d incomplete ingest batch(es) from a previous run", len(incomplete))
        threading.Thread(target=ingest_service.process_batches, args=(incomplete,), daemon=True).start()
    yield


def create_app() -> FastAPI:
    settings = get_settings()  # fails fast if PRAISE_API_KEY / PRAISE_MYID are missing
    logging.basicConfig(level=settings.log_level, format=LOG_FORMAT)

    app = FastAPI(title=APP_TITLE, lifespan=lifespan)
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_ingest_body_bytes)
    app.include_router(ingest.router)
    app.include_router(exposure.router)
    app.include_router(place_labels.router)
    return app


app = create_app()
