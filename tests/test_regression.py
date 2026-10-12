"""
The refactored pipeline must produce exactly the same output as the
pre-refactor code. tests/fixtures/golden/<date>.json was captured from the
old flat etl.py on each data/samples/<date>.geojson (pid "golden").

A sample file missing from this checkout (only some are tracked in git) is
skipped, not failed.
"""

import json
from pathlib import Path

import pytest

from app.config.constants import HK_HOUR_FORMAT, HK_TIME_FORMAT
from app.etl.parsers import parse_pings
from app.etl.parsers.gpx import read_gpx
from app.etl.pipeline import run_pipeline
from app.etl.request_rows import to_request_rows

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "golden"
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
GOLDEN_PID = "golden"


@pytest.mark.parametrize("golden_path", sorted(GOLDEN_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_pipeline_matches_golden(golden_path):
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    sample = SAMPLES_DIR / golden["source"]
    if not sample.exists():
        pytest.skip(f"{sample.name} not present in this checkout")
    features = json.loads(sample.read_text(encoding="utf-8"))["features"]

    slices = run_pipeline(features).slices
    rows = to_request_rows(GOLDEN_PID, slices)

    assert json.loads(json.dumps(rows)) == golden["rows"]
    assert [
        {
            "hour_start_hk": s.hour_start_hk.strftime(HK_HOUR_FORMAT),
            "start_time_hk": s.start_time_hk.strftime(HK_TIME_FORMAT),
            "io": s.io, "lng": s.lng, "lat": s.lat, "delta_t_hours": s.delta_t_hours,
        }
        for s in slices
    ] == golden["segments"]


def test_20260830_row_and_call_counts():
    """Documented figures for the densest sample day: 270 rows, 7 API calls of 40."""
    sample = SAMPLES_DIR / "20260830.geojson"
    if not sample.exists():
        pytest.skip("20260830.geojson not present in this checkout")
    features = json.loads(sample.read_text(encoding="utf-8"))["features"]
    rows = to_request_rows(GOLDEN_PID, run_pipeline(features).slices)
    assert len(rows) == 270
    assert -(-len(rows) // 40) == 7


@pytest.mark.parametrize("date, expect_in_hk", [
    ("20260826", False),  # recorded in Inner Mongolia
    ("20260830", True),
    ("20260831", True),
    ("20260901", True),
])
def test_sample_days_in_hk_flag(date, expect_in_hk):
    sample = SAMPLES_DIR / f"{date}.geojson"
    if not sample.exists():
        pytest.skip(f"{sample.name} not present in this checkout")
    features = json.loads(sample.read_text(encoding="utf-8"))["features"]
    assert {s.in_hk for s in run_pipeline(features).slices} == {expect_in_hk}


@pytest.mark.parametrize("sample", sorted(SAMPLES_DIR.glob("*.gpx")), ids=lambda p: p.name)
def test_gpx_sample_runs_through_pipeline(sample):
    """GPX samples have only lat/lon/time (no speed): every track point must
    parse and the pipeline must keep the whole first-to-last ping time."""
    records = read_gpx(sample.read_bytes())
    pings = parse_pings(records)
    assert len(pings) == len(records) > 0

    result = run_pipeline(records)
    assert result.slices and all(s.in_hk for s in result.slices)
    span_hours = (pings[-1].utc - pings[0].utc).total_seconds() / 3600
    assert sum(s.delta_t_hours for s in result.slices) == pytest.approx(span_hours)
