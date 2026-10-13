import math

import pytest

from app.config import io_types as io_cfg
from app.config.constants import EARTH_RADIUS_M, IoSource
from app.etl.geo import circle_bbox, circle_overlap_ratio
from app.etl.pipeline import run_pipeline
from app.etl.stays import group_into_stays
from app.io_types import registry
from app.io_types.place_labels import PlaceLabel, match_place_label
from tests.conftest import feature, ping

INDOOR_SECONDS = io_cfg.STAY_INDOOR_MINUTES * 60
HOUR = 3600
LNG, LAT = 114.0, 22.0  # conftest.ping default position
M_PER_DEG_LAT = math.radians(1) * EARTH_RADIUS_M
M_PER_DEG_LNG = M_PER_DEG_LAT * math.cos(math.radians(LAT))

HOME, OFFICE, SCHOOL, OTHER = io_cfg.HOME_NAME, io_cfg.OFFICE_NAME, io_cfg.SCHOOL_NAME, io_cfg.OTHER_INDOOR_NAME


def at(east_m: float = 0.0, north_m: float = 0.0):
    """(lng, lat) this many meters east/north of (LNG, LAT)."""
    return LNG + east_m / M_PER_DEG_LNG, LAT + north_m / M_PER_DEG_LAT


def label(label_id, category, radius_m, east_m=0.0, north_m=0.0, contributor_id=None):
    lng, lat = at(east_m, north_m)
    return PlaceLabel(label_id, contributor_id or f"c-{label_id}", category, lng, lat, radius_m)


def pings_at(*offsets, start=12 * HOUR):
    """One ping per (east_m, north_m), a minute apart, from `start` seconds after 00:00 HK."""
    out = []
    for i, (east, north) in enumerate(offsets):
        lng, lat = at(east, north)
        out.append(ping(start + i * 60, lng=lng, lat=lat))
    return out


def indoor_stay(*offsets, start=12 * HOUR):
    """Pings at the offsets, then one more far away to end an indoor-length stay."""
    pings = pings_at(*offsets, start=start)
    end = start + max(INDOOR_SECONDS, len(offsets) * 60)
    lng, lat = at(east_m=5000)
    stays = group_into_stays(pings + [ping(end, lng=lng, lat=lat)])
    assert len(stays[0].pings) == len(offsets)
    return stays


# --- geometry ---

def test_circle_overlap_ratio():
    assert circle_overlap_ratio(label("a", HOME, 25), label("b", HOME, 25)) == pytest.approx(1.0)
    assert circle_overlap_ratio(label("a", HOME, 15), label("b", HOME, 15, east_m=40)) == 0.0
    assert circle_overlap_ratio(label("a", HOME, 15), label("b", HOME, 200)) == pytest.approx((15 / 200) ** 2)
    partial = circle_overlap_ratio(label("a", HOME, 25), label("b", HOME, 25, east_m=10))
    assert 0.5 < partial < 1.0


# --- matching ---


def test_circle_bbox_holds_the_grown_circle():
    c = label("a", HOME, 25)
    min_lng, min_lat, max_lng, max_lat = circle_bbox(c, 30)
    for east, north in ((55, 0), (-55, 0), (0, 55), (0, -55)):
        lng, lat = at(east * 0.999, north * 0.999)
        assert min_lng <= lng <= max_lng and min_lat <= lat <= max_lat
    lng, lat = at(east_m=56)
    assert not (min_lng <= lng <= max_lng)

def test_label_needs_half_the_pings():
    labels = [label("a", OFFICE, 10)]
    assert match_place_label(pings_at((0, 0), (0, 15)), labels) == OFFICE           # 1 of 2
    assert match_place_label(pings_at((0, 0), (0, 15), (0, 15)), labels) is None     # 1 of 3


def test_most_pings_wins_over_smaller_radius():
    # Home holds all 3 pings, the smaller Office circle only 2.
    labels = [label("home", HOME, 10), label("office", OFFICE, 8, north_m=12)]
    assert match_place_label(pings_at((0, 0), (0, 6), (0, 6)), labels) == HOME


def test_tie_goes_to_smaller_radius():
    labels = [label("home", HOME, 50), label("office", OFFICE, 15)]
    assert match_place_label(pings_at((0, 0), (0, 2)), labels) == OFFICE


def test_close_places_are_told_apart():
    labels = [label("home", HOME, 15), label("office", OFFICE, 15, east_m=40)]
    assert match_place_label(pings_at((0, 0), (3, 2), (-2, 4)), labels) == HOME
    assert match_place_label(pings_at((40, 0), (38, 3), (43, -2)), labels) == OFFICE


def test_crowd_vote_on_the_same_spot():
    # Office has the smaller circle, but three contributors say Home.
    labels = [
        label("h1", HOME, 25), label("h2", HOME, 25, east_m=2), label("h3", HOME, 25, north_m=2),
        label("o1", OFFICE, 20),
    ]
    pings = pings_at((0, 0), (1, 1))
    assert match_place_label(pings, labels) == HOME
    assert match_place_label(pings, list(reversed(labels))) == HOME


def test_one_contributor_votes_once_per_category():
    labels = [
        label("h1", HOME, 20, contributor_id="c1"), label("h2", HOME, 20, contributor_id="c1"),
        label("h3", HOME, 20, contributor_id="c1"),
        label("o1", OFFICE, 25, contributor_id="c2"), label("o2", OFFICE, 25, contributor_id="c3"),
    ]
    assert match_place_label(pings_at((0, 0)), labels) == OFFICE


def test_vote_tie_goes_to_better_circle():
    labels = [label("o1", OFFICE, 25), label("h1", HOME, 20)]
    assert match_place_label(pings_at((0, 0)), labels) == HOME


def test_small_circle_inside_big_one_is_not_outvoted():
    labels = [label("home", HOME, 15)] + [label(f"s{i}", SCHOOL, 200) for i in range(3)]
    assert match_place_label(pings_at((0, 0), (2, 2)), labels) == HOME


# --- labelling stays ---

def test_label_overrides_time_window():
    stays = registry.label_stays(indoor_stay((0, 0), (2, 2), start=14 * HOUR), [label("h", HOME, 25)])
    assert (stays[0].io, stays[0].io_source) == (HOME, IoSource.LABEL.value)
    assert (stays[0].lng, stays[0].lat) == at()

    stays = registry.label_stays(indoor_stay((0, 0), (2, 2), start=2 * HOUR), [label("o", OFFICE, 25)])
    assert (stays[0].io, stays[0].io_source) == (OFFICE, IoSource.LABEL.value)


def test_no_matching_label_keeps_the_rules():
    stays = registry.label_stays(indoor_stay((0, 0), start=14 * HOUR), [label("h", HOME, 25, east_m=500)])
    assert (stays[0].io, stays[0].io_source) == (OTHER, IoSource.INFERRED.value)


def test_short_stay_in_a_circle_stays_outdoor():
    stays = registry.label_stays(group_into_stays(pings_at((0, 0), (0, 5), (300, 0))), [label("o", OFFICE, 25)])
    assert (stays[0].io, stays[0].io_source) == (io_cfg.OUTDOOR_NAME, IoSource.INFERRED.value)



def test_night_split_by_drift_is_one_office_visit():
    # Indoor drift hops 22 m every 2 minutes, so every ping is its own short
    # stay: alone each one is Outdoor; inside an Office circle the hour is Office.
    offsets = [(0, 0) if i % 2 == 0 else (0, 22) for i in range(30)]
    pings = [ping(2 * HOUR + i * 120, *at(*o)) for i, o in enumerate(offsets)]
    stays = group_into_stays(pings + [ping(3 * HOUR, *at(east_m=5000))])
    assert {s.io for s in registry.label_stays(stays)} == {io_cfg.OUTDOOR_NAME}

    office, far = registry.label_stays(stays, [label("o", OFFICE, 25)])
    assert (office.io, office.io_source) == (OFFICE, IoSource.LABEL.value)
    assert (office.start_utc, office.end_utc) == (pings[0].utc, far.start_utc)
    assert len(office.pings) == 30
    assert (office.lng, office.lat) == at()
    assert far.io == io_cfg.OUTDOOR_NAME


def test_stay_drifted_just_outside_the_circle_joins_the_visit():
    # 01:00-01:30 drifted 40 m east (outside the 25 m circle, inside the drift
    # margin) - on its own a Home stay - then 01:30-02:00 inside the circle.
    drifted = [ping(HOUR + i * 300, *at(east_m=40)) for i in range(6)]
    inside = [ping(HOUR + 1800 + i * 300, *at()) for i in range(6)]
    stays = group_into_stays(drifted + inside + [ping(2 * HOUR, *at(east_m=5000))])
    assert [s.io for s in registry.label_stays(stays)][:2] == [HOME, HOME]

    office, far = registry.label_stays(stays, [label("o", OFFICE, 25)])
    assert (office.io, office.start_utc, office.end_utc) == (OFFICE, drifted[0].utc, far.start_utc)
    assert far.io == io_cfg.OUTDOOR_NAME


def test_stay_beyond_the_drift_margin_keeps_the_rules():
    inside = [ping(HOUR + i * 300, *at()) for i in range(6)]
    away = [ping(HOUR + 1800 + i * 300, *at(east_m=25 + io_cfg.PLACE_LABEL_DRIFT_MARGIN_M + 20)) for i in range(6)]
    stays = group_into_stays(inside + away + [ping(2 * HOUR, *at(east_m=5000))])
    labelled = registry.label_stays(stays, [label("o", OFFICE, 25)])
    assert [(s.io, s.io_source) for s in labelled[:2]] == [
        (OFFICE, IoSource.LABEL.value), (HOME, IoSource.INFERRED.value),
    ]


def test_pipeline_slices_carry_io_source():
    lng, lat = at()
    features = [feature(12 * HOUR, lng=lng, lat=lat), feature(12 * HOUR + INDOOR_SECONDS, lng=lng, lat=lat)]
    (labelled,) = run_pipeline(features, [label("s", SCHOOL, 25)]).slices
    assert (labelled.io, labelled.io_source) == (SCHOOL, IoSource.LABEL.value)
    (inferred,) = run_pipeline(features).slices
    assert (inferred.io, inferred.io_source) == (OTHER, IoSource.INFERRED.value)
