"""
Pipeline output on the real sample days in data/samples/.

- tests/fixtures/golden/<date>.json holds the points and expo_calx rows of
  each GeoJSON sample day; the pipeline must not change them by accident.
  Regenerate with `python -m tests.fixtures.update_golden` only for an
  intended change.
- 1009-1012 (GPX) are checked against what the person actually did that
  day (taxi, bus, walking, MTR including the above-ground East Rail line
  and an underground ride with a fix at each station).

Samples and the road centrelines are not in git: a test whose files are
missing from this checkout is skipped, not failed.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.config import io_types as io_cfg
from app.config.settings import REPO_ROOT
from app.pipeline.parsers import parse_pings
from app.pipeline.parsers.gpx import read_gpx
from app.pipeline.roads import RoadIndex
from app.pipeline.run import run_pipeline
from app.pipeline.time_split import to_hk
from tests.fixtures.update_golden import golden_of

GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "golden"
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
ROAD_CENTRELINES = REPO_ROOT / "data" / "road_centrelines" / "RoadCentreLine.geojson"


@pytest.fixture(scope="module")
def roads() -> RoadIndex:
    if not ROAD_CENTRELINES.exists():
        pytest.skip(f"{ROAD_CENTRELINES} not present in this checkout")
    return RoadIndex.from_geojson(json.loads(ROAD_CENTRELINES.read_text(encoding="utf-8")))


def _records(sample: Path):
    if not sample.exists():
        pytest.skip(f"{sample.name} not present in this checkout")
    if sample.suffix == ".gpx":
        return read_gpx(sample.read_bytes())
    return json.loads(sample.read_text(encoding="utf-8"))["features"]


@pytest.mark.parametrize("golden_path", sorted(GOLDEN_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_pipeline_matches_golden(golden_path, roads):
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    result = run_pipeline(_records(SAMPLES_DIR / golden["source"]), roads)
    actual = golden_of(result)
    assert actual["points"] == golden["points"]
    assert actual["rows"] == golden["rows"]


@pytest.mark.parametrize("sample", sorted(SAMPLES_DIR.glob("*.geojson")) + sorted(SAMPLES_DIR.glob("*.gpx")),
                         ids=lambda p: p.name)
def test_sample_day_keeps_every_second(sample, roads):
    """The hourly slices add up to exactly the first-to-last ping time."""
    records = _records(sample)
    pings = parse_pings(records)
    result = run_pipeline(records, roads)
    span_hours = (pings[-1].utc - pings[0].utc).total_seconds() / 3600
    assert sum(s.delta_t_hours for s in result.slices) == pytest.approx(span_hours)
    assert len(result.points) < len(pings) / 4


@pytest.mark.parametrize("date, expect_in_hk", [
    ("20260826", False),  # recorded in Inner Mongolia
    ("20260830", True),
    ("20260831", True),
    ("20260901", True),
])
def test_sample_days_in_hk_flag(date, expect_in_hk, roads):
    result = run_pipeline(_records(SAMPLES_DIR / f"{date}.geojson"), roads)
    assert {p.in_hk for p in result.points} == {expect_in_hk}


MTR, VEHICLE, OUTDOOR, OTHER_INDOOR = io_cfg.MTR, io_cfg.VEHICLE, io_cfg.OUTDOOR, io_cfg.OTHER_INDOOR

# (HK time, io) the person reported, without place labels (so their office
# and home are Other Indoor here).
TIMELINES = {
    "1009": [
        ("2026-10-09 23:03", MTR),           # Kwun Tong line, a fix at each station (Lok Fu, Kowloon Tong, ...)
        ("2026-10-09 23:06", MTR),
    ],
    "1010": [
        ("2026-10-10 21:45", OUTDOOR),       # walk in Tai Kok Tsui; GPS caught up with 350 m in one 2 s jump
    ],
    "1011": [
        ("2026-10-11 12:40", OUTDOOR),       # walk to Telford Plaza
        ("2026-10-11 15:00", VEHICLE),       # taxi, Kowloon Bay -> Lai Chi Kok
        ("2026-10-11 16:30", OTHER_INDOOR),  # studio
        ("2026-10-11 17:33", MTR),           # Lai Chi Kok -> Kwai Fong
        ("2026-10-11 18:30", OTHER_INDOOR),  # dinner in the mall
        ("2026-10-11 20:05", MTR),           # Kwai Fong -> Prince Edward
        ("2026-10-11 23:05", VEHICLE),       # bus back to the office
    ],
    "1012": [
        ("2026-10-12 17:45", VEHICLE),       # bus, HKUST -> Diamond Hill
        ("2026-10-12 18:03", MTR),           # Tuen Ma line, Diamond Hill -> Tai Wai
        ("2026-10-12 18:10", MTR),           # changing lines at Tai Wai
        ("2026-10-12 18:18", MTR),           # East Rail line (above ground) -> University
        ("2026-10-12 19:30", OTHER_INDOOR),  # university classroom
        ("2026-10-12 21:18", MTR),           # East Rail line -> Tai Wai
        ("2026-10-12 21:28", MTR),           # changing lines at Tai Wai
        ("2026-10-12 21:35", MTR),           # Tuen Ma line -> Diamond Hill
        ("2026-10-12 21:44", MTR),           # Kwun Tong line -> Kowloon Bay
        ("2026-10-12 22:16", VEHICLE),       # taxi to the office
        ("2026-10-13 08:03", VEHICLE),       # shuttle bus
        ("2026-10-13 08:18", MTR),           # Kowloon Bay -> Diamond Hill
        ("2026-10-13 08:30", VEHICLE),       # bus to HKUST
    ],
}


@pytest.mark.parametrize("day", sorted(TIMELINES))
def test_sample_day_matches_what_the_person_did(day, roads):
    result = run_pipeline(_records(SAMPLES_DIR / f"{day}_route_history.gpx"), roads)
    for when, expected in TIMELINES[day]:
        t = datetime.strptime(when, "%Y-%m-%d %H:%M")
        (point,) = [p for p in result.points if to_hk(p.start_utc) <= t < to_hk(p.end_utc)]
        assert point.io == expected, f"{day} {when}: {point.io} ({point.reason.value}), expected {expected}"
