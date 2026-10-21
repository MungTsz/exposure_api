"""
Deployment settings: secrets, URLs, file paths and API-integration limits.

Values come from environment variables, falling back to the repo-root
`.env` file. A real shell-exported variable always wins over `.env`.
Variable names are case-insensitive (`PRAISE_API_KEY` sets
`praise_api_key`).

`PRAISE_API_KEY` and `PRAISE_MYID` have no default, so the app refuses to
start without them (see `app.main.create_app`).
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # --- PRAISE-HK expo_calx API ---
    praise_api_key: str
    praise_myid: str
    praise_ir_cal_base_url: str = "https://praise-web.ust.hk/uwsgi/praise-ir-cal"

    # No documented rate limit and no server-confirmed batch size limit -
    # 40 rows per call is this app's own conservative, unverified convention.
    praise_max_rows_per_call: int = 40

    # expo_calx batches have consistently taken 20-30s regardless of batch
    # size (a large fixed per-call cost on PRAISE-HK's side); 60s gives real
    # headroom over that instead of racing the observed latency.
    praise_request_timeout_seconds: float = 60.0

    # At most this many expo_calx calls run at the same time, shared by all
    # batches in this process (so several uploads arriving together still
    # never exceed it). A batch's calls run in parallel up to this cap
    # instead of one after another.
    praise_max_concurrent_calls: int = 10

    # --- POST /ingest ---
    # Request bodies larger than this (5 MB) are rejected with 413 and never
    # stored or processed. data/samples/20260830.geojson (8,139 pings, a
    # whole day) is ~2 MB.
    max_ingest_body_bytes: int = 5 * 1024 * 1024

    # --- Storage ---
    # Point this at a mounted persistent volume in production, or data is
    # lost on every restart.
    exposure_db_path: str = "exposure.db"

    # --- Road centrelines ---
    # Lands Department road centrelines as GeoJSON (constants.ROAD_CENTRELINES_URL),
    # loaded once at startup; the app refuses to start without them. A
    # relative path is relative to the repo root.
    road_centrelines_path: str = "data/road_centrelines/RoadCentreLine.geojson"

    # --- Logging ---
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
