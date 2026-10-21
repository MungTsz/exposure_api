"""
Background processing of an ingest batch: load the place labels and road
centrelines, run the pipeline, score the rows with expo_calx, store the
results. Runs after POST /ingest has already returned, because a dense day
needs several ~20-30s expo_calx calls.

Only slices inside Hong Kong are sent to expo_calx. Slices outside HK are
still stored (in_hk = false, exposure_value = NULL) so the app can draw
them differently.
"""

import json
import logging
from collections import Counter
from typing import Any, Dict, List, Optional

import requests

from app.clients import praise_client
from app.config.constants import HK_HOUR_FORMAT, HK_TIME_FORMAT
from app.db import batch_repository, exposure_repository, place_label_repository
from app.pipeline.models import HourlySlice
from app.pipeline.request_rows import to_request_rows
from app.pipeline.run import PipelineResult, run_pipeline
from app.pipeline.time_split import to_hk
from app.services import road_service

logger = logging.getLogger(__name__)


def build_db_rows(
    pid: str, slices: List[HourlySlice], hk_results: List[praise_client.ExposureResult]
) -> List[Dict[str, Any]]:
    """One DB row per slice. `hk_results` holds one expo_calx result per
    in-HK slice, in slice order; slices outside HK get no result."""
    results = iter(hk_results)
    rows = []
    for s in slices:
        point = s.point
        r: Optional[praise_client.ExposureResult] = next(results) if point.in_hk else None
        rows.append({
            "pid": pid,
            "hour_start_hk": s.hour_start_hk.strftime(HK_HOUR_FORMAT),
            "start_time_hk": s.start_time_hk.strftime(HK_TIME_FORMAT),
            "io": point.io,
            "io_source": point.io_source.value,
            "reason": point.reason.value,
            "kind": point.kind.value,
            "delta_t_hours": s.delta_t_hours,
            "exposure_value": r.value if r else None,
            "lng": point.lng,
            "lat": point.lat,
            "heading_deg": point.heading_deg,
            "compass": point.compass,
            "road_name": point.road_name,
            "in_hk": point.in_hk,
            "api_result_json": json.dumps(r.raw) if r else None,
        })
    return rows


def process_batch(batch_id: int, pid: str, features: List[Dict[str, Any]]) -> None:
    """Process one batch; never raises - any failure is recorded on the batch."""
    log_prefix = f"batch={batch_id} pid={pid}"
    try:
        _process_batch(batch_id, pid, features, log_prefix)
    except Exception as e:
        logger.exception("%s unexpected error", log_prefix)
        batch_repository.mark_error(batch_id, f"unexpected error: {praise_client.redact_secrets(str(e))}")


def _process_batch(batch_id: int, pid: str, features: List[Dict[str, Any]], log_prefix: str) -> None:
    batch_repository.mark_processing(batch_id)
    logger.info("%s processing %d raw features", log_prefix, len(features))

    place_labels = place_label_repository.get_all()
    result = run_pipeline(features, road_service.get_road_index(), place_labels)
    _log_result(log_prefix, result, len(place_labels))

    hk_slices = result.hk_slices
    rows = to_request_rows(pid, hk_slices)
    results: List[praise_client.ExposureResult] = []
    if rows:
        logger.info(
            "%s calling expo_calx with %d rows in %d batch(es)",
            log_prefix, len(rows), praise_client.count_calls(len(rows)),
        )
        try:
            results = praise_client.calculate_exposure(rows)
        except (requests.exceptions.RequestException, praise_client.PraiseApiError) as e:
            what = "timed out" if isinstance(e, requests.exceptions.Timeout) else "failed"
            detail = praise_client.redact_secrets(str(e))
            logger.warning("%s expo_calx call %s: %s", log_prefix, what, detail)
            batch_repository.mark_error(batch_id, f"expo_calx call {what}: {detail}")
            return

        logger.info("%s expo_calx returned %d results", log_prefix, len(results))
        if len(results) != len(hk_slices):
            batch_repository.mark_error(batch_id, "expo_calx returned a different row count than requested")
            return

    db_rows = build_db_rows(pid, result.slices, results)
    if result.first_ping_utc is None:
        exposure_repository.upsert_hourly(db_rows)
    else:
        # Replace whatever an earlier upload stored for the same time span.
        exposure_repository.replace_hourly(
            pid,
            to_hk(result.first_ping_utc).strftime(HK_TIME_FORMAT),
            to_hk(result.last_ping_utc).strftime(HK_TIME_FORMAT),
            db_rows,
        )

    hours_processed = len({s.hour_start_hk for s in result.slices})
    logger.info("%s ingest complete: hours_processed=%d rows=%d", log_prefix, hours_processed, len(db_rows))
    batch_repository.mark_done(batch_id, hours_processed, len(db_rows))


def _log_result(log_prefix: str, result: PipelineResult, label_count: int) -> None:
    minutes = Counter()
    for s in result.slices:
        minutes[s.point.io] += s.delta_t_hours * 60
    hk = len(result.hk_slices)
    logger.info(
        "%s pings=%d after_spikes=%d runs=%d points=%d hourly_slices=%d (inside HK=%d, outside=%d) labels=%d",
        log_prefix, result.pings, result.pings_after_spikes, result.runs, len(result.points), len(result.slices),
        hk, len(result.slices) - hk, label_count,
    )
    logger.info("%s minutes per io: %s", log_prefix, {io: round(m, 1) for io, m in minutes.most_common()})


def process_batches(batches: List[Dict[str, Any]]) -> None:
    """Process batches one after another (used to resume a backlog on startup)."""
    for b in batches:
        process_batch(b["id"], b["pid"], b["features"])
