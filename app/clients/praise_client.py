"""
Client for the legacy PRAISE-HK expo_calx endpoint.

Reference doc caveats this respects:
  - todo=expo_calx (not the newer get_exposure_list), on the praise-ir-cal
    uwsgi script, not the general praise-service base URL.
  - 6-field positional rows: [t, pid, lng, lat, x, delta_t] - no record_id,
    speed, or status fields.
  - apikey/myid are plain query params, once per batch call.
  - Success is inferred from `exposure` being an array; `msg` is only
    present on failure.
  - FAILED_ROW_SENTINEL (-9) on an individual row means that row failed -
    not a whole-call error.
  - No documented rate limit and no server-confirmed batch size limit (see
    settings.praise_max_rows_per_call).
  - No built-in retry: a network failure raises straight through.

Calls run in parallel on one process-wide thread pool of
settings.praise_max_concurrent_calls workers, so the number of expo_calx
calls in flight never exceeds that cap, however many batches are being
processed at once.

Each result keeps the API's full item in `raw`, so fields the API adds
later are not lost even before code reads them.
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, List, Optional

import requests

from app.config.constants import EXPO_CALX_TODO, FAILED_ROW_SENTINEL, SECRET_QUERY_PARAMS
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_SECRET_PARAM_RE = re.compile(rf"({'|'.join(SECRET_QUERY_PARAMS)})=[^&\s]*", re.IGNORECASE)


class PraiseApiError(Exception):
    """Raised when a whole batch call fails (exposure field missing/not an array,
    or a result item in an unexpected shape)."""


@dataclass(frozen=True)
class ExposureResult:
    t: str
    pid: str
    value: Optional[float]  # None if expo_calx returned FAILED_ROW_SENTINEL for this row
    raw: Any                # the API's item exactly as returned


def redact_secrets(text: str) -> str:
    """Strips apikey/myid values out of request-derived exception text (they
    end up in the URL, which requests/urllib3 echo verbatim into error
    messages) before it's logged or shown to an API caller."""
    return _SECRET_PARAM_RE.sub(r"\1=REDACTED", text)


def _to_result(item: Any) -> ExposureResult:
    # Documented item shape: [t, pid, value, ...]. Anything after value is
    # kept in `raw` only.
    if not isinstance(item, (list, tuple)) or len(item) < 3:
        raise PraiseApiError(f"unexpected expo_calx result item: {item!r}")
    t, pid, value = item[0], item[1], item[2]
    return ExposureResult(t=t, pid=pid, value=None if value == FAILED_ROW_SENTINEL else value, raw=item)


def _call_batch(rows: List[list]) -> List[ExposureResult]:
    settings = get_settings()
    params = {
        "todo": EXPO_CALX_TODO,
        "input_data": json.dumps(rows, separators=(",", ":")),
        "apikey": settings.praise_api_key,
        "myid": settings.praise_myid,
    }
    logger.info("expo_calx batch call: %d rows", len(rows))
    start = time.monotonic()
    try:
        resp = requests.get(
            settings.praise_ir_cal_base_url, params=params, timeout=settings.praise_request_timeout_seconds
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning("expo_calx batch failed after %.2fs: %s", time.monotonic() - start, redact_secrets(str(e)))
        raise
    elapsed = time.monotonic() - start

    data = resp.json()
    exposure = data.get("exposure")
    if not isinstance(exposure, list):
        msg = data.get("msg") if isinstance(data.get("msg"), str) else "PRAISE-HK expo_calx call failed"
        logger.warning("expo_calx batch failed after %.2fs: %s", elapsed, msg)
        raise PraiseApiError(msg)
    logger.info("expo_calx batch succeeded in %.2fs: %d results", elapsed, len(exposure))
    return [_to_result(item) for item in exposure]


@lru_cache
def _pool(max_workers: int) -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="expo_calx")


def calculate_exposure(rows: List[list]) -> List[ExposureResult]:
    """Splits rows into batches of praise_max_rows_per_call, calls them in
    parallel (at most praise_max_concurrent_calls at once, process-wide) and
    returns results in the same order as `rows`.

    If any call fails, calls not yet started are cancelled and the first
    failure (in row order) is raised.
    """
    settings = get_settings()
    batch_size = settings.praise_max_rows_per_call
    pool = _pool(settings.praise_max_concurrent_calls)
    futures = [pool.submit(_call_batch, rows[i : i + batch_size]) for i in range(0, len(rows), batch_size)]
    results: List[ExposureResult] = []
    try:
        for f in futures:
            results.extend(f.result())
    except BaseException:
        for f in futures:
            f.cancel()
        raise
    return results


def count_calls(num_rows: int) -> int:
    """How many expo_calx calls calculate_exposure will make for num_rows rows."""
    batch_size = get_settings().praise_max_rows_per_call
    return (num_rows + batch_size - 1) // batch_size
