import pytest

from app.config import io_types as io_cfg
from app.config.constants import IoSource, Reason
from app.pipeline.classify import classify_runs
from app.pipeline.geo import circle_bbox, circle_overlap_ratio
from app.pipeline.models import Motion, Track
from app.pipeline.place_labels import PlaceLabel, match_place_label
from app.pipeline.run import run_pipeline
from app.pipeline.runs import to_runs
from tests.conftest import HOUR, at, feature, jittering, ping, road_index

HOME, OFFICE, SCHOOL, OTHER = io_cfg.HOME, io_cfg.OFFICE, io_cfg.SCHOOL, io_cfg.OTHER_INDOOR


def label(label_id, category, radius_m, east_m=0.0, north_m=0.0, contributor_id=None):
    return PlaceLabel(label_id, contributor_id or f"c-{label_id}", category, *at(east_m, north_m), radius_m)


def pings_at(*offsets, start=12 * HOUR):
    """One ping per (east_m, north_m), a minute apart, from `start` seconds after 00:00 HK."""
    return [ping(start + i * 60, east, north) for i, (east, north) in enumerate(offsets)]


def classify_stay(pings, labels):
    """(io, io_source, reason) of one still run made of these pings."""
    track = Track.from_pings(pings)
    (c,) = classify_runs(track, to_runs([Motion.STILL] * len(pings)), labels)
    return c.io, c.io_source, c.reason


# --- geometry ---

def test_circle_overlap_ratio():
    assert circle_overlap_ratio(label("a", HOME, 25), label("b", HOME, 25)) == pytest.approx(1.0)
    assert circle_overlap_ratio(label("a", HOME, 15), label("b", HOME, 15, east_m=40)) == 0.0
    assert circle_overlap_ratio(label("a", HOME, 15), label("b", HOME, 200)) == pytest.approx((15 / 200) ** 2)
    partial = circle_overlap_ratio(label("a", HOME, 25), label("b", HOME, 25, east_m=10))
    assert 0.5 < partial < 1.0


def test_circle_bbox_holds_the_grown_circle():
    c = label("a", HOME, 25)
    min_lng, min_lat, max_lng, max_lat = circle_bbox(c, 30)
    for east, north in ((55, 0), (-55, 0), (0, 55), (0, -55)):
        lng, lat = at(east * 0.999, north * 0.999)
        assert min_lng <= lng <= max_lng and min_lat <= lat <= max_lat
    lng, _ = at(east_m=56)
    assert not (min_lng <= lng <= max_lng)


# --- matching one stay against the labels ---

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


# --- labels in the decision tree ---

def test_label_comes_before_the_night_rule():
    afternoon_home = jittering(14 * HOUR, 15 * HOUR, 60)
    night_office = jittering(2 * HOUR, 3 * HOUR, 60)
    assert classify_stay(afternoon_home, [label("h", HOME, 25)]) == (HOME, IoSource.LABEL, Reason.PLACE_LABEL)
    assert classify_stay(night_office, [label("o", OFFICE, 25)]) == (OFFICE, IoSource.LABEL, Reason.PLACE_LABEL)


def test_no_matching_label_keeps_the_rules():
    stay = jittering(14 * HOUR, 15 * HOUR, 60)
    assert classify_stay(stay, [label("h", HOME, 25, east_m=500)]) == (OTHER, IoSource.INFERRED, Reason.LONG_STAY)


def test_short_stay_inside_a_label_gets_the_label():
    stay = jittering(12 * HOUR, 12 * HOUR + 300, 60)
    assert classify_stay(stay, [label("o", OFFICE, 25)])[0] == OFFICE


@pytest.mark.parametrize("east_m, matches", [
    (25 + io_cfg.PLACE_LABEL_DRIFT_MARGIN_M - 10, True),   # drifted outside the circle, within the margin
    (25 + io_cfg.PLACE_LABEL_DRIFT_MARGIN_M + 20, False),  # beyond the margin
])
def test_drift_margin(east_m, matches):
    stay = jittering(14 * HOUR, 15 * HOUR, 60, centre_m=(east_m, 0), jitter_m=5)
    io = classify_stay(stay, [label("o", OFFICE, 25)])[0]
    assert (io == OFFICE) is matches


def test_a_night_at_a_labelled_office_does_not_make_its_area_home():
    office_night = jittering(2 * HOUR, 5 * HOUR, 300)
    # The walk run starts at the last office ping, so it holds the long gap
    # and the evening stay is just 19:00-19:10.
    walk = [ping(19 * HOUR - 600 + k * 10, east_m=k) for k in range(60)]
    nearby = jittering(19 * HOUR, 19 * HOUR + 600, 60, centre_m=(0, 60))  # outside the grown 10 + 30 m circle
    pings = office_night + walk + nearby
    motions = [Motion.STILL] * len(office_night) + [Motion.WALK] * len(walk) + [Motion.STILL] * len(nearby)
    track = Track.from_pings(pings)

    def ios(labels):
        return [c.io for c in classify_runs(track, to_runs(motions), labels)]

    assert ios([label("o", OFFICE, 10)]) == [OFFICE, io_cfg.OUTDOOR, io_cfg.OUTDOOR]
    assert ios([]) == [HOME, io_cfg.OUTDOOR, HOME]  # unlabelled: the night stay is home, and so is its area


def test_pipeline_points_carry_io_source():
    lng, lat = at()
    features = [feature(12 * HOUR + k * 60, lng=lng, lat=lat) for k in range(21)]
    roads = road_index()
    (labelled,) = run_pipeline(features, roads, [label("s", SCHOOL, 25)]).points
    assert (labelled.io, labelled.io_source) == (SCHOOL, IoSource.LABEL)
    (inferred,) = run_pipeline(features, roads).points
    assert (inferred.io, inferred.io_source) == (OTHER, IoSource.INFERRED)
