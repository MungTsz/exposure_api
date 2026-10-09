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
  4. Thin each continuous Outdoor trail to roughly one dot per lat/lng box
     (or per max time span, see config.TRAIL_DOT_*): 1Hz tracking while
     moving otherwise yields one run per ping.
  5. Slice each labelled run at HK-local clock-hour boundaries, carrying the
     run's io label into every hour it touches (and prorating delta_t).
  6. Build one [t, pid, lng, lat, x, delta_t] row per (hour, IO segment).

Tunable constants (other than HK_OFFSET) now live in config.py - adjust
values there rather than editing the logic below.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone, timedelta
from typing import List, Literal, Dict, Any, Optional
import math

try:
    from . import config
except ImportError:  # imported as a bare top-level module (e.g. testing.ipynb)
    import config

HK_OFFSET = timedelta(hours=8)

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
    (angle at B < config.SPIKE_ANGLE_DEG) within config.SPIKE_TIME_WINDOW_SECONDS.
    Re-scans after each deletion since removing B changes the next triple to check."""
    cleaned = list(pings)
    changed = True
    while changed:
        changed = False
        i = 1
        while i < len(cleaned) - 1:
            a, b, c = cleaned[i - 1], cleaned[i], cleaned[i + 1]
            span = (c.utc - a.utc).total_seconds()
            if span <= config.SPIKE_TIME_WINDOW_SECONDS and _angle_at_b_deg(a, b, c) < config.SPIKE_ANGLE_DEG:
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
        abs(p.lat - ref.lat) <= config.DWELL_DEGREE_THRESHOLD
        and abs(p.lng - ref.lng) <= config.DWELL_DEGREE_THRESHOLD
    )


def _is_slow_enough(p: Ping) -> bool:
    return p.speed_mps is None or p.speed_mps < config.SPEED_THRESHOLD_MPS


def find_dwell_runs(pings: List[Ping]) -> List[DwellRun]:
    """Split the full, continuous ping stream into indoor/outdoor runs by dwell time.

    A "run" is a maximal sequence of consecutive points that all stay within
    config.DWELL_DEGREE_THRESHOLD (a flat lat/lng box, not haversine) of the
    run's first point AND report a speed below config.SPEED_THRESHOLD_MPS
    (points with no speed reading are judged on distance alone). If the run's
    total duration clears config.DWELL_INDOOR_MINUTES, it's labelled Indoor;
    otherwise Outdoor. A genuinely-indoor stop and an outdoor wait of the same
    duration (e.g. waiting for a bus) are indistinguishable by this signal
    alone - tuning these three config.py values is the only lever, not a
    structural fix (documented known limitation).
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
        io: IoLabel = "Indoor" if duration_min >= config.DWELL_INDOOR_MINUTES else "Outdoor"
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


def _xy_meters(lng: float, lat: float, origin_lng: float, origin_lat: float) -> tuple:
    """Local flat-earth projection around the origin (same as _angle_at_b_deg's)."""
    x = math.radians(lng - origin_lng) * math.cos(math.radians(origin_lat)) * 6371000.0
    y = math.radians(lat - origin_lat) * 6371000.0
    return x, y


def _meters_between(a: DwellRun, b: DwellRun) -> float:
    return math.hypot(*_xy_meters(b.lng, b.lat, a.lng, a.lat))


def _is_trail_continuation(prev: DwellRun, curr: DwellRun) -> bool:
    """True if curr continues the same continuous Outdoor trail as prev. A
    long prev run (its end is the next ping's time, so this means a GPS
    dropout) or a big jump between the two points breaks the trail."""
    return (
        prev.io == "Outdoor"
        and curr.io == "Outdoor"
        and (prev.end_utc - prev.start_utc).total_seconds() <= config.MAX_GAP_SECONDS
        and _meters_between(prev, curr) <= config.MAX_GAP_METERS
    )


def _trail_dot_keep(trail: List[DwellRun]) -> List[int]:
    """Indices of trail points to keep. Starting from the last kept point,
    a point is dropped while it stays inside a config.TRAIL_DOT_DEGREE_THRESHOLD
    lat/lng box of it AND less than config.TRAIL_DOT_MAX_SECONDS have passed;
    the first point breaking either condition is kept and becomes the new
    reference. The trail's last point gets no special treatment: forcing it
    in left near-duplicate dots at every trail end, and if it is dropped its
    time folds into a kept dot at most one box away."""
    keep = [0]
    ref = trail[0]
    for m in range(1, len(trail)):
        p = trail[m]
        if (
            abs(p.lat - ref.lat) > config.TRAIL_DOT_DEGREE_THRESHOLD
            or abs(p.lng - ref.lng) > config.TRAIL_DOT_DEGREE_THRESHOLD
            or (p.start_utc - ref.start_utc).total_seconds() >= config.TRAIL_DOT_MAX_SECONDS
        ):
            keep.append(m)
            ref = p
    return keep


def simplify_outdoor_trails(runs: List[DwellRun]) -> List[DwellRun]:
    """Thin each continuous Outdoor trail down to its shape-defining points.

    While moving, 1Hz tracking makes nearly every ping its own ~1-second
    Outdoor run (it leaves find_dwell_runs's dwell box immediately), which
    floods expo_calx with rows and the app's map with dots. Consecutive
    Outdoor runs are grouped into trails (see _is_trail_continuation), each
    trail is reduced with _trail_dot_keep, and every kept point becomes
    one run at its own position, extended to cover the time of the dropped
    points after it - total time is preserved exactly. Indoor runs pass
    through untouched.
    """
    out: List[DwellRun] = []
    i, n = 0, len(runs)
    while i < n:
        if runs[i].io != "Outdoor":
            out.append(runs[i])
            i += 1
            continue
        j = i + 1
        while j < n and _is_trail_continuation(runs[j - 1], runs[j]):
            j += 1
        trail = runs[i:j]
        kept = _trail_dot_keep(trail)
        for a, b in zip(kept, kept[1:] + [len(trail)]):
            out.append(replace(trail[a], end_utc=trail[b - 1].end_utc))
        i = j
    return out


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
