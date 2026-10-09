import json
import threading
import time
from unittest.mock import MagicMock

import pytest
import requests

from app.clients import praise_client


def _response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_batches_rows_and_keeps_order(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        rows = json.loads(params["input_data"])
        calls.append(len(rows))
        return _response({"exposure": [[r[0], r[1], 1.0] for r in rows]})

    monkeypatch.setattr(praise_client.requests, "get", fake_get)
    rows = [[str(i), "p", 114.0, 22.0, {"IO": "Outdoor"}, 0.1] for i in range(85)]
    results = praise_client.calculate_exposure(rows)
    assert sorted(calls) == [5, 40, 40]
    assert praise_client.count_calls(85) == 3
    assert [r.t for r in results] == [str(i) for i in range(85)]


def test_calls_run_in_parallel_up_to_the_cap(monkeypatch, test_settings):
    test_settings.praise_max_concurrent_calls = 3
    lock = threading.Lock()
    running, peak = 0, 0

    def fake_get(url, params, timeout):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        rows = json.loads(params["input_data"])
        return _response({"exposure": [[r[0], r[1], 1.0] for r in rows]})

    monkeypatch.setattr(praise_client.requests, "get", fake_get)
    rows = [[str(i), "p", 114.0, 22.0, {"IO": "Outdoor"}, 0.1] for i in range(40 * 10)]
    results = praise_client.calculate_exposure(rows)
    assert peak == 3
    assert [r.t for r in results] == [str(i) for i in range(400)]


def test_failed_row_becomes_none_and_raw_is_kept(monkeypatch):
    item = ["20260830000000", "p", -9, {"extra": "field"}]
    monkeypatch.setattr(praise_client.requests, "get", lambda *a, **k: _response({"exposure": [item]}))
    (result,) = praise_client.calculate_exposure([["20260830000000", "p", 114.0, 22.0, {"IO": "Home"}, 1.0]])
    assert result.value is None
    assert result.raw == item


def test_missing_exposure_raises(monkeypatch):
    monkeypatch.setattr(praise_client.requests, "get", lambda *a, **k: _response({"msg": "bad key"}))
    with pytest.raises(praise_client.PraiseApiError, match="bad key"):
        praise_client.calculate_exposure([["t", "p", 0, 0, {}, 1.0]])


def test_network_error_raises_through(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(praise_client.requests, "get", boom)
    with pytest.raises(requests.exceptions.ConnectionError):
        praise_client.calculate_exposure([["t", "p", 0, 0, {}, 1.0]])


def test_redact_secrets():
    text = "GET https://x/y?todo=expo_calx&apikey=SECRET&myid=ME failed"
    assert praise_client.redact_secrets(text) == "GET https://x/y?todo=expo_calx&apikey=REDACTED&myid=REDACTED failed"
