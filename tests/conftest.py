from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import get_settings
from app.etl.models import Ping

T0 = datetime(2026, 8, 29, 16, 0, 0, tzinfo=timezone.utc)  # 00:00 HK on 2026-08-30


@pytest.fixture(autouse=True)
def test_settings(monkeypatch, tmp_path):
    """Dummy secrets and a throwaway DB for every test. Env vars take
    priority over the real .env, so real keys are never used."""
    monkeypatch.setenv("PRAISE_API_KEY", "test-key")
    monkeypatch.setenv("PRAISE_MYID", "test-id")
    monkeypatch.setenv("EXPOSURE_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PRAISE_MAX_ROWS_PER_CALL", "40")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def ping(seconds: float, lng: float = 114.0, lat: float = 22.0, speed=None) -> Ping:
    return Ping(utc=T0 + timedelta(seconds=seconds), lng=lng, lat=lat, speed_mps=speed)


def feature(seconds: float, lng: float = 114.0, lat: float = 22.0, speed=None) -> dict:
    props = {"time": (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
    if speed is not None:
        props["speed"] = speed
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [lng, lat]}}
