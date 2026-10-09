import pytest

from app.config import io_types as io_cfg
from app.config import pipeline as cfg
from app.etl.parsers import parse_pings
from app.etl.region import is_in_hk, mark_in_hk
from app.etl.request_rows import to_request_rows
from app.etl.routes import simplify_routes
from app.etl.spikes import remove_spike_outliers
from app.etl.stays import group_into_stays
from app.etl.time_split import split_by_hour
from app.io_types import registry
from tests.conftest import feature, ping

INDOOR_SECONDS = io_cfg.STAY_INDOOR_MINUTES * 60


# --- parsers ---

def test_parse_geojson_sorts_and_cleans_speed():
    pings = parse_pings([feature(10, speed=-1), feature(0, lng=114.1, lat=22.1, speed=0.5)])
    assert [p.lng for p in pings] == [114.1, 114.0]
    assert pings[0].speed_mps == 0.5
    assert pings[1].speed_mps is None


def test_unrecognized_records_are_skipped():
    pings = parse_pings([{"timestamp": "2026-08-29T16:00:00Z", "coords": {}}, feature(0)])
    assert len(pings) == 1


# --- spikes ---

def test_spike_is_removed():
    a, spike, c = ping(0), ping(10, lng=114.01), ping(20, lng=114.0001)
    assert remove_spike_outliers([a, spike, c]) == [a, c]


def test_slow_turn_back_is_kept():
    pings = [ping(0), ping(cfg.SPIKE_TIME_WINDOW_SECONDS, lng=114.01), ping(cfg.SPIKE_TIME_WINDOW_SECONDS * 2)]
    assert remove_spike_outliers(pings) == pings


# --- stays ---

def test_pings_in_box_and_slow_form_one_stay():
    stays = group_into_stays([ping(0), ping(60, lng=114.0001), ping(120, lat=22.0001)])
    assert len(stays) == 1
    assert stays[0].duration_seconds == 120


def test_fast_ping_starts_new_stay():
    stays = group_into_stays([ping(0), ping(60, speed=cfg.SPEED_THRESHOLD_MPS + 1)])
    assert len(stays) == 2
    assert stays[0].end_utc == stays[1].start_utc


# --- io types ---

def test_long_stay_is_indoor_at_first_ping():
    stays = registry.label_stays(group_into_stays([ping(0), ping(INDOOR_SECONDS, lng=114.0001)]))
    assert stays[0].io == io_cfg.INDOOR_NAME
    assert (stays[0].lng, stays[0].lat) == (114.0, 22.0)


def test_short_stay_is_outdoor_at_average():
    stays = registry.label_stays(group_into_stays([ping(0), ping(60, lng=114.0001), ping(120, lng=114.5)]))
    assert stays[0].io == io_cfg.OUTDOOR_NAME
    assert stays[0].lng == (114.0 + 114.0001) / 2


def test_last_io_type_matches_everything():
    assert registry.IO_TYPES[-1].matches(group_into_stays([ping(0)])[0])


# --- routes ---

def _moving_stays(n: int, step_deg: float, step_s: float = 1.0):
    pings = [ping(i * step_s, lng=114.0 + i * step_deg, speed=5.0) for i in range(n)]
    return registry.label_stays(group_into_stays(pings))


def test_route_is_thinned_and_keeps_total_time():
    stays = _moving_stays(200, step_deg=0.0001)  # ~11m per second
    simplified = simplify_routes(stays)
    assert 1 < len(simplified) < len(stays)
    total = lambda ss: sum(s.duration_seconds for s in ss)
    assert total(simplified) == total(stays)


def test_route_breaks_at_big_jump():
    stays = _moving_stays(3, step_deg=0.0001)
    far = registry.label_stays(group_into_stays([ping(3, lng=114.1, speed=5.0)]))
    simplified = simplify_routes(stays + far)
    assert simplified[-1].lng == 114.1


def test_stationary_stays_pass_through():
    stays = registry.label_stays(group_into_stays([ping(0), ping(INDOOR_SECONDS)]))
    assert simplify_routes(stays) == stays


# --- hour split + request rows ---

def test_stay_crossing_hours_is_split_and_prorated():
    stays = registry.label_stays(group_into_stays([ping(30 * 60), ping(150 * 60)]))  # 00:30-02:30 HK
    slices = split_by_hour(stays)
    assert [s.delta_t_hours for s in slices] == [0.5, 1.0, 0.5]
    assert {s.io for s in slices} == {io_cfg.INDOOR_NAME}


def test_request_rows_use_api_label():
    stays = registry.label_stays(group_into_stays([ping(0), ping(INDOOR_SECONDS)]))
    rows = to_request_rows("p1", split_by_hour(stays))
    assert rows == [["20260830000000", "p1", 114.0, 22.0, {"IO": io_cfg.INDOOR_API_LABEL}, 0.333333]]


# --- Hong Kong region ---

@pytest.mark.parametrize("name, lng, lat", [
    ("Central", 114.158, 22.282),
    ("Tuen Mun", 113.973, 22.391),
    ("Tai O", 113.862, 22.254),
    ("Airport", 113.915, 22.308),
    ("Mai Po", 114.035, 22.490),
    ("Sheung Shui", 114.128, 22.501),
    ("Sha Tau Kok", 114.222, 22.543),
    ("Tung Ping Chau", 114.430, 22.545),
    ("Po Toi", 114.250, 22.170),
])
def test_points_inside_hk(name, lng, lat):
    assert is_in_hk(lng, lat), name


@pytest.mark.parametrize("name, lng, lat", [
    ("Shekou", 113.915, 22.485),
    ("Shenzhen Bay Park", 113.960, 22.510),
    ("Futian", 114.055, 22.540),
    ("Luohu", 114.117, 22.540),
    ("Yantian", 114.270, 22.580),
    ("Nan'ao, Dapeng", 114.475, 22.530),
    ("Macau", 113.550, 22.190),
    ("sea south of HK waters", 114.0, 22.0),
    ("Inner Mongolia (data/samples/20260826)", 108.3, 40.7),
])
def test_points_outside_hk(name, lng, lat):
    assert not is_in_hk(lng, lat), name


def test_mark_in_hk_flags_each_slice():
    stays = registry.label_stays(group_into_stays([ping(0, lng=114.158, lat=22.282), ping(60), ping(120)]))
    slices = mark_in_hk(split_by_hour(stays))
    assert [s.in_hk for s in slices] == [True, False]
