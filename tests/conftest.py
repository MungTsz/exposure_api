import json
import math
from datetime import datetime, timedelta, timezone
from typing import Sequence, Tuple

import pytest

from app.config.constants import EARTH_RADIUS_M
from app.config.settings import get_settings
from app.pipeline.models import Ping
from app.pipeline.roads import RoadIndex
from app.services import road_service

T0 = datetime(2026, 8, 29, 16, 0, 0, tzinfo=timezone.utc)  # 00:00 HK on 2026-08-30
HOUR = 3600

# Every synthetic ping is placed in metres from Central, Hong Kong (inside
# HK, so the rail test and expo_calx apply).
LNG0, LAT0 = 114.158, 22.282
M_PER_DEG_LAT = math.radians(1) * EARTH_RADIUS_M
M_PER_DEG_LNG = M_PER_DEG_LAT * math.cos(math.radians(LAT0))


@pytest.fixture(autouse=True)
def test_settings(monkeypatch, tmp_path):
    """Dummy secrets, a throwaway DB and an empty road file for every test.
    Env vars take priority over the real .env, so real keys are never used."""
    roads = tmp_path / "roads.geojson"
    roads.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    monkeypatch.setenv("PRAISE_API_KEY", "test-key")
    monkeypatch.setenv("PRAISE_MYID", "test-id")
    monkeypatch.setenv("EXPOSURE_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PRAISE_MAX_ROWS_PER_CALL", "40")
    monkeypatch.setenv("ROAD_CENTRELINES_PATH", str(roads))
    get_settings.cache_clear()
    road_service.get_road_index.cache_clear()
    yield get_settings()
    get_settings.cache_clear()
    road_service.get_road_index.cache_clear()


def at(east_m: float = 0.0, north_m: float = 0.0) -> Tuple[float, float]:
    """(lng, lat) this many metres east / north of Central."""
    return LNG0 + east_m / M_PER_DEG_LNG, LAT0 + north_m / M_PER_DEG_LAT


def ping(seconds: float, east_m: float = 0.0, north_m: float = 0.0) -> Ping:
    lng, lat = at(east_m, north_m)
    return Ping(utc=T0 + timedelta(seconds=seconds), lng=lng, lat=lat)


def _times(start_s: float, end_s: float, every_s: float):
    n = int(round((end_s - start_s) / every_s))
    return [start_s + k * every_s for k in range(n + 1)]


def pings_along(start_s: float, end_s: float, every_s: float, start_m=(0.0, 0.0), velocity_mps=(0.0, 0.0)):
    """Pings every `every_s` from start_s to end_s, moving at a constant
    velocity (east, north m/s) from start_m (east, north m)."""
    return [
        ping(t, start_m[0] + velocity_mps[0] * (t - start_s), start_m[1] + velocity_mps[1] * (t - start_s))
        for t in _times(start_s, end_s, every_s)
    ]


def jittering(start_s: float, end_s: float, every_s: float, centre_m=(0.0, 0.0), jitter_m: float = 10.0):
    """A still phone: pings hopping jitter_m east and back around centre_m."""
    return [
        ping(t, centre_m[0] + (jitter_m if k % 2 else 0.0), centre_m[1])
        for k, t in enumerate(_times(start_s, end_s, every_s))
    ]


def road_index(*lines_m: Sequence[Tuple[float, float]]) -> RoadIndex:
    """A RoadIndex of straight roads, each given as (east, north) metre vertices."""
    return RoadIndex([([at(*v) for v in line], f"ROAD {i}") for i, line in enumerate(lines_m)])


def feature(seconds: float, lng: float = LNG0, lat: float = LAT0) -> dict:
    props = {"time": (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [lng, lat]}}


GPX_1_1_NS = "http://www.topografix.com/GPX/1/1"


def gpx_document(points, namespace: str = GPX_1_1_NS) -> bytes:
    """A GPX file with one track. `points` are (seconds, lng, lat) like
    feature(); seconds=None writes a <trkpt> without <time>."""
    trkpts = []
    for seconds, lng, lat in points:
        time = "" if seconds is None else (
            f"<time>{(T0 + timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')}</time>"
        )
        trkpts.append(f'<trkpt lat="{lat}" lon="{lng}">{time}<ele>10.0</ele></trkpt>')
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" xmlns="{namespace}">'
        f'<trk><trkseg>{"".join(trkpts)}</trkseg></trk></gpx>'
    ).encode()
