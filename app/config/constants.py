"""
Fixed facts the code relies on: timezone, unit conversions, data formats
shared with the DB and the expo_calx API, and status values.

These are not tuning knobs. Thresholds that shape the pipeline's output
live in `pipeline.py` and `io_types.py`; deployment values live in
`settings.py`.
"""

from datetime import timedelta
from enum import Enum

# --- App ---
APP_TITLE = "Exposure ETL backend"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# --- Time ---
# Hong Kong is UTC+8 all year (no daylight saving time).
HK_OFFSET = timedelta(hours=8)
# HK-local timestamp format used by expo_calx's `t` field and the DB's
# start_time_hk column.
HK_TIME_FORMAT = "%Y%m%d%H%M%S"
# HK-local hour bucket format used by the DB's hour_start_hk column.
HK_HOUR_FORMAT = "%Y%m%d%H0000"

# --- Geometry ---
EARTH_RADIUS_M = 6371000.0

# --- Hong Kong region ---
# expo_calx only scores points inside Hong Kong. This is a simplified
# outline of the HK SAR (land and waters) as (lng, lat) vertices, clockwise
# from the north-west. It follows the Shenzhen border (Deep Bay, Shenzhen
# River, Sha Tau Kok, Mirs Bay) to within roughly 1 km, so a point right on
# the border can land on the wrong side. Replace it with an official
# boundary if that accuracy is not enough; app/etl/region.py works with any
# simple polygon.
HK_BOUNDARY = (
    (113.835, 22.395),  # western waters, north of Lung Kwu Chau
    (113.890, 22.450),  # Deep Bay mouth, south of Shekou
    (113.960, 22.488),  # Shenzhen Bay Bridge
    (114.010, 22.505),  # Deep Bay, off Mai Po
    (114.035, 22.512),  # Shenzhen River mouth
    (114.075, 22.516),  # Lok Ma Chau
    (114.112, 22.530),  # Lo Wu
    (114.135, 22.537),  # Man Kam To
    (114.160, 22.555),  # Heung Yuen Wai
    (114.190, 22.560),  # Lin Ma Hang
    (114.225, 22.545),  # Sha Tau Kok
    (114.270, 22.555),  # Starling Inlet, south of Yantian
    (114.330, 22.565),  # north of Kat O
    (114.420, 22.570),  # north of Tung Ping Chau
    (114.455, 22.550),  # east of Tung Ping Chau
    (114.455, 22.480),  # west of the Dapeng peninsula
    (114.510, 22.430),  # south-west of the Dapeng peninsula
    (114.510, 22.140),  # south-eastern waters
    (113.900, 22.140),  # southern waters, south of the Soko Islands
    (113.835, 22.180),  # south-west of Lantau
)

# --- Input formats ---
# `type` of a record made from one GPX <trkpt> (app/etl/parsers/gpx.py).
# These records are what an uploaded GPX file is stored as in a batch.
GPX_TRKPT_RECORD_TYPE = "gpx_trkpt"
# Content-Type of a GPX upload to POST /ingest/gpx.
GPX_MEDIA_TYPE = "application/gpx+xml"

# --- expo_calx API ---
EXPO_CALX_TODO = "expo_calx"
# Key inside each request row's `x` dict that carries the io label.
EXPO_CALX_IO_KEY = "IO"
# Decimal places delta_t_hours is rounded to in a request row.
DELTA_T_DECIMALS = 6
# expo_calx returns this as a row's value when just that row failed.
FAILED_ROW_SENTINEL = -9
# Query params whose values must never reach logs or API callers.
SECRET_QUERY_PARAMS = ("apikey", "myid")


# --- Place labels ---
# Limits on PUT /place-labels/{label_id}, agreed with the app.
PLACE_LABEL_ID_MAX_LENGTH = 64           # label_id and contributor_id
PLACE_LABEL_MIN_RADIUS_M = 10
PLACE_LABEL_MAX_RADIUS_M = 200


class IoSource(str, Enum):
    """Where a stay's io type came from."""

    LABEL = "label"        # a place label matched the stay
    INFERRED = "inferred"  # the io type rules in app/io_types/rules


# --- Ingest batches ---
class BatchStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


# Status returned by POST /ingest once a batch is stored and queued.
INGEST_QUEUED_STATUS = "queued"
