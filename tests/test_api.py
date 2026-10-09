import json
import sqlite3

import pytest
import requests
from fastapi.testclient import TestClient

from app.clients import praise_client
from app.config import io_types as io_cfg
from app.config.constants import BatchStatus
from app.config.settings import get_settings
from tests.conftest import feature

INDOOR_SECONDS = io_cfg.STAY_INDOOR_MINUTES * 60
HK = {"lng": 114.158, "lat": 22.282}        # Central, Hong Kong
OUTSIDE_HK = {"lng": 113.550, "lat": 22.190}  # Macau


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _fake_exposure(rows):
    return [praise_client.ExposureResult(t=r[0], pid=r[1], value=2.5, raw=[r[0], r[1], 2.5]) for r in rows]


def test_ingest_then_read_hourly(client, monkeypatch):
    monkeypatch.setattr(praise_client, "calculate_exposure", _fake_exposure)
    body = {"pid": "p1", "features": [feature(0, **HK), feature(INDOOR_SECONDS, **HK)]}

    resp = client.post("/ingest", json=body)
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    batch_id = resp.json()["batch_id"]

    status = client.get(f"/ingest/{batch_id}").json()
    assert status["status"] == BatchStatus.DONE.value
    assert status["rows"] == 1

    rows = client.get("/exposure/hourly/p1").json()
    assert rows == [{
        "hour_start_hk": "20260830000000", "start_time_hk": "20260830000000", "io": io_cfg.INDOOR_NAME,
        "delta_t_hours": INDOOR_SECONDS / 3600, "exposure_value": 2.5, "lng": 114.158, "lat": 22.282,
        "in_hk": True,
    }]


def test_points_outside_hk_are_returned_but_not_scored(client, monkeypatch):
    sent = []

    def fake(rows):
        sent.extend(rows)
        return _fake_exposure(rows)

    monkeypatch.setattr(praise_client, "calculate_exposure", fake)
    features = [feature(0, **HK), feature(600, **OUTSIDE_HK), feature(1200, **OUTSIDE_HK)]
    batch_id = client.post("/ingest", json={"pid": "p1", "features": features}).json()["batch_id"]

    assert client.get(f"/ingest/{batch_id}").json()["rows"] == 2
    assert [(r[2], r[3]) for r in sent] == [(HK["lng"], HK["lat"])]
    rows = client.get("/exposure/hourly/p1").json()
    assert [(r["in_hk"], r["exposure_value"]) for r in rows] == [(True, 2.5), (False, None)]


def test_all_points_outside_hk_makes_no_api_call(client, monkeypatch):
    def fail(rows):
        raise AssertionError("expo_calx must not be called")

    monkeypatch.setattr(praise_client, "calculate_exposure", fail)
    features = [feature(0, **OUTSIDE_HK), feature(600, **OUTSIDE_HK)]
    batch_id = client.post("/ingest", json={"pid": "p1", "features": features}).json()["batch_id"]

    status = client.get(f"/ingest/{batch_id}").json()
    assert (status["status"], status["rows"]) == (BatchStatus.DONE.value, 1)
    (row,) = client.get("/exposure/hourly/p1").json()
    assert (row["in_hk"], row["exposure_value"]) == (False, None)


def _body_and_small_limit_client(monkeypatch):
    """A JSON body and a client whose size limit is one byte under it."""
    from app.main import create_app

    body = json.dumps({"pid": "p1", "features": [feature(0, **HK)] * 10}).encode()
    monkeypatch.setenv("MAX_INGEST_BODY_BYTES", str(len(body) - 1))
    get_settings.cache_clear()
    return body, TestClient(create_app())


def test_body_over_limit_is_413_and_not_stored(monkeypatch):
    body, client = _body_and_small_limit_client(monkeypatch)
    with client:
        resp = client.post("/ingest", content=body, headers={"Content-Type": "application/json"})
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"]
        assert client.get("/ingest/1").status_code == 404


def test_chunked_body_over_limit_is_413(monkeypatch):
    body, client = _body_and_small_limit_client(monkeypatch)
    chunks = (body[i : i + 100] for i in range(0, len(body), 100))  # no Content-Length
    with client:
        resp = client.post("/ingest", content=chunks, headers={"Content-Type": "application/json"})
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"]
        assert client.get("/ingest/1").status_code == 404


def test_api_result_is_stored_raw(client, monkeypatch, test_settings):
    monkeypatch.setattr(praise_client, "calculate_exposure", _fake_exposure)
    client.post("/ingest", json={"pid": "p1", "features": [feature(0, **HK), feature(60, **HK)]})
    with sqlite3.connect(test_settings.exposure_db_path) as conn:
        (raw,) = conn.execute("SELECT api_result_json FROM hourly_exposure").fetchone()
    assert json.loads(raw)[2] == 2.5


def test_ingest_without_valid_pings_is_400(client):
    resp = client.post("/ingest", json={"pid": "p1", "features": [{"unknown": True}]})
    assert resp.status_code == 400


def test_expo_calx_failure_marks_batch_error(client, monkeypatch):
    def timeout(rows):
        raise requests.exceptions.Timeout("read timed out apikey=SECRET")

    monkeypatch.setattr(praise_client, "calculate_exposure", timeout)
    features = [feature(0, **HK), feature(60, **HK)]
    batch_id = client.post("/ingest", json={"pid": "p1", "features": features}).json()["batch_id"]
    status = client.get(f"/ingest/{batch_id}").json()
    assert status["status"] == BatchStatus.ERROR.value
    assert status["error_detail"] == "expo_calx call timed out: read timed out apikey=REDACTED"


def test_unknown_batch_is_404(client):
    assert client.get("/ingest/999").status_code == 404
