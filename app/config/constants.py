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
# Compass names of the 8 directions, clockwise from north, 45 degrees apart.
COMPASS_POINTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# --- Hong Kong region ---
# expo_calx only scores points inside Hong Kong. This is a simplified
# outline of the HK SAR (land and waters) as (lng, lat) vertices, clockwise
# from the north-west. It follows the Shenzhen border (Deep Bay, Shenzhen
# River, Sha Tau Kok, Mirs Bay) to within roughly 1 km, so a point right on
# the border can land on the wrong side. Replace it with an official
# boundary if that accuracy is not enough; app/pipeline/region.py works with
# any simple polygon.
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

# --- Road centrelines (app/pipeline/roads.py) ---
# Lands Department "Road Centreline" dataset on the CSDI portal, as GeoJSON
# (WGS84 lng/lat LineStrings). Download it to settings.road_centrelines_path.
ROAD_CENTRELINES_URL = (
    "https://static.csdi.gov.hk/csdi-webpage/file-api"
    "?dataset_id=landsd_rcd_1637310758814_80061&format=geojson&layer_name=RoadCentreLine"
)
# Feature property holding a centreline's English street name.
ROAD_NAME_PROPERTY = "ENGLISHSTREETNAME"
# Road distances are measured on a flat projection around this (lng, lat),
# the middle of Hong Kong: accurate to well under 1% anywhere in HK.
ROAD_PROJECTION_ORIGIN = (114.17, 22.32)

# --- Input formats ---
# `type` of a record made from one GPX <trkpt> (app/pipeline/parsers/gpx.py).
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


# --- Pipeline output (stored in the DB and returned by the API) ---
class IoSource(str, Enum):
    """Where a point's io type came from."""

    LABEL = "label"        # a place label matched the stay
    INFERRED = "inferred"  # the decision tree in app/pipeline/classify.py


class PointKind(str, Enum):
    """What one output point stands for (app/pipeline/points.py)."""

    STAY = "stay"    # a whole stay (or MTR interchange), at the median of its pings
    ROUTE = "route"  # one kept ping of a thinned walk / vehicle / MTR route


class Reason(str, Enum):
    """Which branch of the decision tree (app/pipeline/classify.py) gave a
    point its io type."""

    JUMP = "jump"                      # MTR: travelled while GPS was dark (underground)
    RAIL = "rail"                      # MTR: a vehicle run that kept away from every road
    INTERCHANGE = "interchange"        # MTR: a short stop between two MTR runs (changing lines)
    VEHICLE = "vehicle"                # Vehicle
    WALK = "walk"                      # Outdoor
    PLACE_LABEL = "place_label"        # a place label's category
    NIGHT_STAY = "night_stay"          # Home: a long stay overlapping 00:00-06:00 HK
    NEAR_NIGHT_STAY = "near_night_stay"  # Home: the same place as a night stay
    SHORT_STOP = "short_stop"          # Outdoor: a stay shorter than the indoor minimum
    LONG_STAY = "long_stay"            # Other Indoor


# --- Ingest batches ---
class BatchStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


# Status returned by POST /ingest once a batch is stored and queued.
INGEST_QUEUED_STATUS = "queued"
