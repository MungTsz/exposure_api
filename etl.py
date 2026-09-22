"""
ETL pipeline for turning raw watch/phone GPS pings into expo_calx request rows.

Order of operations (matches the agreed design):
  1. Parse + sort raw pings.
  2. Remove GPS spike outliers (sharp-angle-in-a-short-time test).
  3. Classify indoor vs outdoor by dwell time over the full, continuous ping
     stream (a run of nearby, slow-moving points lasting long enough = indoor).
     "Nearby" is a flat lat/lng degree box around the run's first point, not
     haversine - deliberately simple. Classifying before splitting by hour
     means a dwell run that straddles an hour boundary is judged by its true
     total duration, not by whichever partial slice happens to land in one
     clock hour.
  4. Slice each labelled run at HK-local clock-hour boundaries, carrying the
     run's io label into every hour it touches (and prorating delta_t).
  5. Build one [t, pid, lng, lat, x, delta_t] row per (hour, IO segment).

Tunable constants are at the top - adjust these to match your device's
ping rate and walking speed rather than editing the logic below.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Literal, Dict, Any, Optional
import math

HK_OFFSET = timedelta(hours=8)

# --- Spike filter tuning ---
SPIKE_ANGLE_DEG = 20.0          # angle at B below this = suspected spike
SPIKE_TIME_WINDOW_SECONDS = 120  # only flag as a spike if A->C happened this fast

# --- Indoor/outdoor dwell tuning ---
DWELL_INDOOR_MINUTES = 20       # a stationary run at least this long = indoor

# Degree-based "same location" tolerance for GPS drift, deliberately used
# instead of a haversine distance so the check is a plain lat/lng delta
# comparison. 0.0001 deg of lat/lng is ~11.1m, so ~0.00018 deg is ~20m - loose
# enough to absorb typical indoor GPS drift (consumer GPS horizontal accuracy
# is commonly 5-20m, worse indoors from multipath), tight enough not to merge
# genuinely separate stops. Applied as a flat box on both lat and lng, with
# no per-latitude correction for longitude compression.
DWELL_DEGREE_THRESHOLD = 0.00018

# A point only counts as part of the same stationary run if its own reported
# speed (when the device provides one - many raw pings don't) is below this.
# ~1.0 m/s (~3.6 km/h) is a brisk-walk cutoff: comfortably separates indoor
# puttering (getting up, pacing a room) from real outdoor travel (walking
# commute is usually 1.2 m/s+, any transit mode faster still), without
# rejecting legitimate slow indoor movement. A missing speed reading doesn't
# block a point from joining the run - only a reported *fast* speed does.
SPEED_THRESHOLD_MPS = 1.0

IoLabel = Literal["Indoor", "Outdoor"]


@dataclass
class Ping:
    utc: datetime
    lng: float
    lat: float
    speed_mps: Optional[float] = None  # device-reported speed, m/s; None if not provided


@dataclass
class DwellRun:
    start_utc: datetime
    end_utc: datetime
    io: IoLabel
    lng: float  # anchor (first ping) for Indoor runs, avg over the run's pings for Outdoor
    lat: float  # anchor (first ping) for Indoor runs, avg over the run's pings for Outdoor


@dataclass
class IoSegment:
    hour_start_hk: datetime   # which hour bucket this segment belongs to (for the chart/DB)
    start_time_hk: datetime   # this segment's own precise start time (for the API row's `t`)
    io: IoLabel
    lng: float
    lat: float
    delta_t_hours: float


def parse_pings(features: List[Dict[str, Any]]) -> List[Ping]:
    """Parse a list of raw ping dicts into a sorted Ping list. Accepts two
    input shapes, detected per record so a batch may mix both:
      - GeoJSON Feature (watch-exported test data): properties.time,
        geometry.coordinates.
      - Live device tracking (react-native-background-geolocation-style):
        timestamp, coords.latitude/coords.longitude. Records with
        sample=True are transient readings not meant to be persisted, and
        are dropped; mock=True (fake-GPS) records are kept as-is.
    """
    pings = []
    for f in features:
        ping = _parse_geojson_feature(f) if "geometry" in f else _parse_live_tracking_ping(f)
        if ping is not None:
            pings.append(ping)
    pings.sort(key=lambda p: p.utc)
    return pings


def _clean_speed(speed: Optional[float]) -> Optional[float]:
    # react-native-background-geolocation reports -1 (or negative) for
    # "unknown" rather than omitting the field - treat that as no reading.
    if speed is None or speed < 0:
        return None
    return speed


def _parse_geojson_feature(f: Dict[str, Any]) -> Ping:
    t = f["properties"]["time"]
    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    lng, lat = f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1]
    return Ping(utc=dt, lng=lng, lat=lat, speed_mps=_clean_speed(f["properties"].get("speed")))


def _parse_live_tracking_ping(f: Dict[str, Any]) -> Optional[Ping]:
    if f.get("sample"):
        return None
    dt = datetime.fromisoformat(f["timestamp"].replace("Z", "+00:00"))
    coords = f["coords"]
    return Ping(utc=dt, lng=coords["longitude"], lat=coords["latitude"], speed_mps=_clean_speed(coords.get("speed")))


def _angle_at_b_deg(a: Ping, b: Ping, c: Ping) -> float:
    """Angle ABC using a local flat-earth projection around B (fine at foot/vehicle scale)."""

    def to_xy(p: Ping) -> tuple:
        x = math.radians(p.lng - b.lng) * math.cos(math.radians(b.lat)) * 6371000.0
        y = math.radians(p.lat - b.lat) * 6371000.0
        return x, y

    ax, ay = to_xy(a)
    cx, cy = to_xy(c)
    mag_a, mag_c = math.hypot(ax, ay), math.hypot(cx, cy)
    if mag_a == 0 or mag_c == 0:
        return 180.0  # degenerate (duplicate points) - treat as a normal straight path
    cos_theta = max(-1.0, min(1.0, (ax * cx + ay * cy) / (mag_a * mag_c)))
    return math.degrees(math.acos(cos_theta))


def remove_spike_outliers(pings: List[Ping]) -> List[Ping]:
    """Delete point B from any triple A,B,C where the path sharply doubles back
    (angle at B < SPIKE_ANGLE_DEG) within SPIKE_TIME_WINDOW_SECONDS. Re-scans
    after each deletion since removing B changes the next triple to check."""
    cleaned = list(pings)
    changed = True
    while changed:
        changed = False
        i = 1
        while i < len(cleaned) - 1:
            a, b, c = cleaned[i - 1], cleaned[i], cleaned[i + 1]
            span = (c.utc - a.utc).total_seconds()
            if span <= SPIKE_TIME_WINDOW_SECONDS and _angle_at_b_deg(a, b, c) < SPIKE_ANGLE_DEG:
                del cleaned[i]
                changed = True
                continue
            i += 1
    return cleaned


def _to_hk(dt_utc: datetime) -> datetime:
    """Returns a naive datetime whose fields read as HK-local wall-clock time."""
    return (dt_utc.astimezone(timezone.utc) + HK_OFFSET).replace(tzinfo=None)


def _within_dwell_box(ref: Ping, p: Ping) -> bool:
    return (
        abs(p.lat - ref.lat) <= DWELL_DEGREE_THRESHOLD
        and abs(p.lng - ref.lng) <= DWELL_DEGREE_THRESHOLD
    )


def _is_slow_enough(p: Ping) -> bool:
    return p.speed_mps is None or p.speed_mps < SPEED_THRESHOLD_MPS


def find_dwell_runs(pings: List[Ping]) -> List[DwellRun]:
    """Split the full, continuous ping stream into indoor/outdoor runs by dwell time.

    A "run" is a maximal sequence of consecutive points that all stay within
    DWELL_DEGREE_THRESHOLD (a flat lat/lng box, not haversine) of the run's
    first point AND report a speed below SPEED_THRESHOLD_MPS (points with no
    speed reading are judged on distance alone). If the run's total duration
    clears DWELL_INDOOR_MINUTES, it's labelled Indoor; otherwise Outdoor.
    Note: the very last run has no further point to bound its end, so its
    duration is computed up to its own last ping only.
    """
    if not pings:
        return []

    runs: List[DwellRun] = []

    def flush(run: List[Ping], end_time_utc: datetime) -> None:
        if not run:
            return
        start_utc = run[0].utc
        duration_min = max(0.0, (end_time_utc - start_utc).total_seconds() / 60.0)
        io: IoLabel = "Indoor" if duration_min >= DWELL_INDOOR_MINUTES else "Outdoor"
        if io == "Indoor":
            # Collapse the whole dwell to its anchor point - GPS drift while
            # stationary indoors shouldn't be treated as distinct locations.
            lng, lat = run[0].lng, run[0].lat
        else:
            lng = sum(p.lng for p in run) / len(run)
            lat = sum(p.lat for p in run) / len(run)
        runs.append(
            DwellRun(
                start_utc=start_utc,
                end_utc=end_time_utc,
                io=io,
                lng=lng,
                lat=lat,
            )
        )

    run = [pings[0]]
    for p in pings[1:]:
        ref = run[0]
        if _within_dwell_box(ref, p) and _is_slow_enough(p):
            run.append(p)
        else:
            flush(run, p.utc)
            run = [p]
    flush(run, pings[-1].utc)
    return runs


def split_by_hour(runs: List[DwellRun]) -> List[IoSegment]:
    """Slice each dwell run at HK-local clock-hour boundaries.

    Each hour-slice inherits the parent run's io label - already determined
    from the run's true, un-chopped duration by find_dwell_runs - and gets
    its own prorated delta_t_hours. A run crossing an hour boundary therefore
    produces two (or more) same-labelled rows instead of being reclassified,
    and possibly mislabelled, per hour.
    """
    segments: List[IoSegment] = []
    for run in runs:
        start_hk = _to_hk(run.start_utc)
        end_hk = _to_hk(run.end_utc)
        hour_cursor = start_hk.replace(minute=0, second=0, microsecond=0)
        while hour_cursor < end_hk:
            next_hour = hour_cursor + timedelta(hours=1)
            slice_start = max(start_hk, hour_cursor)
            slice_end = min(end_hk, next_hour)
            duration_hours = (slice_end - slice_start).total_seconds() / 3600.0
            if duration_hours > 0:
                segments.append(
                    IoSegment(
                        hour_start_hk=hour_cursor,
                        start_time_hk=slice_start,
                        io=run.io,
                        lng=run.lng,
                        lat=run.lat,
                        delta_t_hours=duration_hours,
                    )
                )
            hour_cursor = next_hour
    return segments


# expo_calx doesn't recognize "Indoor" as an IO value, so it's translated to
# "Home" at this API-request boundary only; "Indoor" stays the label
# everywhere else (dwell classification, DB, chart).
_API_IO_LABEL = {"Indoor": "Home", "Outdoor": "Outdoor"}


def to_request_rows(pid: str, segments: List[IoSegment]) -> List[list]:
    """Convert IoSegments (with delta_t > 0) into expo_calx's 6-field rows."""
    rows = []
    for seg in segments:
        if seg.delta_t_hours <= 0:
            continue
        rows.append(
            [
                seg.start_time_hk.strftime("%Y%m%d%H%M%S"),
                pid,
                seg.lng,
                seg.lat,
                {"IO": _API_IO_LABEL[seg.io]},
                round(seg.delta_t_hours, 6),
            ]
        )
    return rows
