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
  - -9 on an individual row means that row failed - not a whole-call error.
  - No documented rate limit and no server-confirmed batch size limit -
    MAX_ROWS_PER_CALL=40 is this app's conservative, unverified convention.
  - No built-in retry: a network failure raises straight through.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import List, Tuple

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("PRAISE_IR_CAL_BASE_URL", "https://praise-web.ust.hk/uwsgi/praise-ir-cal")
API_KEY = os.environ["PRAISE_API_KEY"]
MY_ID = os.environ["PRAISE_MYID"]
MAX_ROWS_PER_CALL = 40
# expo_calx batches have consistently taken 20-30s regardless of batch size
# (a large fixed per-call cost on PRAISE-HK's side); 60s gives real headroom
# over that instead of racing the observed latency.
REQUEST_TIMEOUT_SECONDS = 60

_SECRET_PARAM_RE = re.compile(r"(apikey|myid)=[^&\s]*", re.IGNORECASE)


def redact_secrets(text: str) -> str:
    """Strips apikey/myid values out of request-derived exception text (they
    end up in the URL, which requests/urllib3 echo verbatim into error
    messages) before it's logged or shown to an API caller."""
    return _SECRET_PARAM_RE.sub(r"\1=REDACTED", text)


class PraiseApiError(Exception):
    """Raised when a whole batch call fails (exposure field missing/not an array)."""


def _call_batch(rows: List[list]) -> List[Tuple[str, str, float]]:
    params = {
        "todo": "expo_calx",
        "input_data": json.dumps(rows, separators=(",", ":")),
        "apikey": API_KEY,
        "myid": MY_ID,
    }
    logger.info("expo_calx batch call: %d rows", len(rows))
    start = time.monotonic()
    try:
        resp = requests.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout as e:
        logger.warning("expo_calx batch timed out after %.2fs: %s", time.monotonic() - start, redact_secrets(str(e)))
        raise
    except requests.exceptions.RequestException as e:
        logger.warning("expo_calx batch failed after %.2fs: %s", time.monotonic() - start, redact_secrets(str(e)))
        raise
    elapsed = time.monotonic() - start

    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        logger.warning("expo_calx batch failed after %.2fs: %s", elapsed, redact_secrets(str(e)))
        raise
    data = resp.json()
    exposure = data.get("exposure")
    if not isinstance(exposure, list):
        msg = data.get("msg") if isinstance(data.get("msg"), str) else "PRAISE-HK expo_calx call failed"
        logger.warning("expo_calx batch failed after %.2fs: %s", elapsed, msg)
        raise PraiseApiError(msg)
    logger.info("expo_calx batch succeeded in %.2fs: %d results", elapsed, len(exposure))
    return [tuple(item) for item in exposure]


def calculate_exposure(rows: List[list]) -> List[Tuple[str, str, float]]:
    """Batches rows into chunks of 40 and calls sequentially, concatenating results in order."""
    results: List[Tuple[str, str, float]] = []
    for i in range(0, len(rows), MAX_ROWS_PER_CALL):
        batch = rows[i : i + MAX_ROWS_PER_CALL]
        results.extend(_call_batch(batch))
    return results
