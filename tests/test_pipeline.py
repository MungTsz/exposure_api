"""One section per pipeline step, in step order (app/pipeline/run.py)."""

from datetime import datetime, timedelta

import pytest

from app.config import io_types as io_cfg
from app.config import pipeline as cfg
from app.config.constants import IoSource, PointKind, Reason
from app.pipeline.classify import classify_runs
from app.pipeline.models import Motion, Ping, Point, Run, Track
from app.pipeline.motion import interval_motions
from app.pipeline.mtr import find_mtr
from app.pipeline.parsers import parse_pings
from app.pipeline.parsers.gpx import GpxError, read_gpx
from app.pipeline.place_labels import PlaceLabel
from app.pipeline.points import build_points
from app.pipeline.region import is_in_hk
from app.pipeline.request_rows import to_request_rows
from app.pipeline.roads import NearestRoad
from app.pipeline.run import run_pipeline
from app.pipeline.runs import build_runs, to_motions, to_runs
from app.pipeline.spikes import remove_spikes
from app.pipeline.time_split import split_by_hour
from tests.conftest import (
    HOUR, LAT0, LNG0, T0, at, feature, gpx_document, jittering, ping, pings_along, road_index,
)

J, S, W, V = Motion.JUMP, Motion.STILL, Motion.WALK, Motion.VEHICLE
NO_ROADS = road_index()


def track_and_runs(*segments):
    """(motion, pings) segments in order -> (track, runs): the intervals
    ending at a segment's pings get its motion."""
    pings, motions = [], []
    for motion, seg in segments:
        pings += seg
        motions += [motion] * len(seg)
    return Track.from_pings(pings), to_runs(motions)


def far_roads(track):
    return [NearestRoad(1000.0, None)] * len(track)


# --- input: parsers ---

def test_geojson_reads_only_time_and_position_and_sorts():
    f = feature(10)
    f["properties"].update({"speed": 3.0, "accuracy": 5})
    pings = parse_pings([f, feature(0, lng=114.1, lat=22.1)])
    assert [(p.lng, p.lat) for p in pings] == [(114.1, 22.1), (LNG0, LAT0)]
    assert pings[1] == Ping(utc=T0 + timedelta(seconds=10), lng=LNG0, lat=LAT0)


@pytest.mark.parametrize("bad", [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [114.0, 22.0]}},                  # no properties
    {"type": "Feature", "properties": {"time": "yesterday"}, "geometry": {"coordinates": [114.0, 22.0]}},
    {"type": "Feature", "properties": {"time": "2026-08-29T16:00:00Z"}, "geometry": {"coordinates": [114.0]}},
    {"type": "Feature", "properties": {"time": "2026-08-29T16:00:00Z"}, "geometry": {"coordinates": [114.0, 95.0]}},
])
def test_geojson_record_without_valid_time_or_position_is_dropped(bad):
    assert parse_pings([bad, feature(0)]) == parse_pings([feature(0)])


def test_unrecognized_records_are_skipped():
    assert len(parse_pings([{"timestamp": "2026-08-29T16:00:00Z", "coords": {}}, feature(0)])) == 1


def test_gpx_track_points_become_pings():
    pings = parse_pings(read_gpx(gpx_document([(0, 114.1, 22.1), (60, 114.2, 22.2)])))
    assert [(p.lng, p.lat) for p in pings] == [(114.1, 22.1), (114.2, 22.2)]
    assert pings[1].utc - pings[0].utc == timedelta(seconds=60)
    assert pings[0].utc == parse_pings([feature(0)])[0].utc


def test_gpx_1_0_namespace_and_extra_elements_are_ignored():
    data = b"""<gpx version="1.0" xmlns="http://www.topografix.com/GPX/1/0"><trk><trkseg>
        <trkpt lat="22.1" lon="114.1"><time>2026-08-29T16:00:00Z</time><speed>1.5</speed><ele>3</ele></trkpt>
    </trkseg></trk></gpx>"""
    (record,) = read_gpx(data)
    assert set(record) == {"type", "lat", "lon", "time"}
    assert parse_pings([record])[0].lng == 114.1


def test_gpx_time_without_zone_is_utc():
    data = b'<gpx><trk><trkseg><trkpt lat="22.1" lon="114.1"><time>2026-08-29T16:00:00</time></trkpt></trkseg></trk></gpx>'
    (p,) = parse_pings(read_gpx(data))
    assert p.utc == T0


def test_gpx_points_without_time_or_valid_position_are_dropped():
    records = read_gpx(gpx_document([(0, 114.1, 22.1), (None, 114.1, 22.1), (60, 114.1, 95.0), (120, "x", 22.1)]))
    assert len(records) == 4
    assert len(parse_pings(records)) == 1


def test_gpx_waypoints_and_route_points_are_ignored():
    data = b"""<gpx><wpt lat="22.1" lon="114.1"><time>2026-08-29T16:00:00Z</time></wpt>
        <rte><rtept lat="22.1" lon="114.1"><time>2026-08-29T16:00:00Z</time></rtept></rte></gpx>"""
    assert read_gpx(data) == []


def test_gpx_and_geojson_records_mix_in_time_order():
    records = read_gpx(gpx_document([(30, 114.3, 22.3)])) + [feature(60), feature(0, lng=114.1)]
    assert [p.lng for p in parse_pings(records)] == [114.1, 114.3, LNG0]


@pytest.mark.parametrize("data", [b"not xml", b"<gpx><trk>", b"<kml></kml>", b""])
def test_unreadable_gpx_raises(data):
    with pytest.raises(GpxError):
        read_gpx(data)


def test_gpx_entity_expansion_is_refused():
    data = b"""<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "aaaaaaaaaa">]><gpx>&a;</gpx>"""
    with pytest.raises(GpxError):
        read_gpx(data)


# --- track ---

def test_track_holds_interval_durations_and_distances():
    track = Track.from_pings([ping(0), ping(10, east_m=100), ping(25, east_m=100, north_m=50)])
    assert track.dt_s == (0.0, 10.0, 15.0)
    assert track.dd_deg[0] == 0.0
    assert track.dd_deg[1] == pytest.approx(at(east_m=100)[0] - LNG0)
    assert Run(S, 1, 2).seconds(track) == 25.0 and Run(S, 2, 2).seconds(track) == 15.0


# --- step 0: spikes ---

def test_spike_out_and_back_is_removed():
    a, spike, c = ping(0), ping(10, east_m=500), ping(20, east_m=5)
    assert remove_spikes([a, spike, c]) == [a, c]


def test_short_turn_back_and_real_moves_are_kept():
    turn = [ping(0), ping(10, east_m=50), ping(20, east_m=5)]       # legs under ~100 m
    move = [ping(0), ping(10, east_m=500), ping(20, east_m=1000)]   # does not come back
    assert remove_spikes(turn) == turn
    assert remove_spikes(move) == move


@pytest.mark.parametrize("a, bad, c", [
    (ping(0), ping(6, east_m=150), ping(7, north_m=50)),             # 150 m off, back on the track 1 s later
    (ping(0), ping(50, east_m=1500), ping(57, east_m=1100)),         # overshoots the car by 400 m
])
def test_fix_only_an_impossible_speed_could_reach_is_removed(a, bad, c):
    assert remove_spikes([a, bad, c]) == [a, c]


@pytest.mark.parametrize("repeat_s, removed", [(cfg.SPIKE_MAX_S, True), (cfg.SPIKE_MAX_S + 60, False)])
def test_repeated_fallback_fix_is_one_spike_unless_it_lasts(repeat_s, removed):
    a, c = ping(0), ping(repeat_s + 120, east_m=5)
    repeats = [ping(t, north_m=150) for t in (60, 80, 60 + repeat_s)]
    expected = [a, c] if removed else [a, *repeats, c]
    assert remove_spikes([a, *repeats, c]) == expected


# --- step 1: motion ---

def test_jitter_in_place_is_still():
    track = Track.from_pings(jittering(0, 600, 1, jitter_m=15))
    assert set(interval_motions(track)) == {S}


def test_walking_is_walk():
    track = Track.from_pings(pings_along(0, 600, 5, velocity_mps=(0, 1.3)))
    assert set(interval_motions(track)) == {W}


def test_driving_is_vehicle():
    track = Track.from_pings(pings_along(0, 300, 1, velocity_mps=(15, 0)))
    assert set(interval_motions(track)) == {V}


def test_moving_far_while_gps_is_dark_is_a_jump():
    track = Track.from_pings([ping(0), ping(300, east_m=3000), ping(310, east_m=3000)])
    assert interval_motions(track)[:2] == [J, J]  # interval 0 copies interval 1


def test_long_gap_without_moving_is_still():
    assert interval_motions(Track.from_pings([ping(0), ping(cfg.GAP_S, east_m=20)])) == [S, S]


# --- step 2: runs ---

def test_runs_round_trip():
    motions = [S, S, W, V, V, S]
    assert [(r.motion, r.first, r.last) for r in to_runs(motions)] == [(S, 0, 1), (W, 2, 2), (V, 3, 4), (S, 5, 5)]
    assert to_motions(to_runs(motions)) == motions


def test_short_stop_inside_a_trip_joins_it():
    track = Track.from_pings(pings_along(0, 250, 10))
    runs = build_runs(track, [V] * 10 + [S] * 5 + [V] * 11)  # a 50 s stop at a traffic light
    assert [r.motion for r in runs] == [V]


def test_walk_blip_joins_the_longer_neighbour():
    track = Track.from_pings(pings_along(0, 650, 10))
    runs = build_runs(track, [S] * 30 + [W] * 3 + [S] * 33)  # a 30 s walk between two stays
    assert [r.motion for r in runs] == [S]


def test_vehicle_blip_next_to_a_jump_joins_the_jump():
    track = Track.from_pings(pings_along(0, 650, 10))
    runs = build_runs(track, [S] * 30 + [V] * 5 + [J] + [S] * 30)
    assert [r.motion for r in runs] == [S, J, S]


def _stay_walk_stay(second_stay_north_m):
    first = jittering(0, 300, 10)
    walk = pings_along(310, 420, 10, start_m=(0, 10), velocity_mps=(0, 0.5))
    second = jittering(430, 730, 10, centre_m=(0, second_stay_north_m))
    return track_and_runs((S, first), (W, walk), (S, second))


def test_two_stays_at_the_same_place_with_a_short_walk_are_one_stay():
    track, runs = _stay_walk_stay(second_stay_north_m=50)
    assert [r.motion for r in build_runs(track, to_motions(runs))] == [S]


def test_stays_at_different_places_stay_apart():
    track, runs = _stay_walk_stay(second_stay_north_m=500)
    assert [r.motion for r in build_runs(track, to_motions(runs))] == [S, W, S]


def test_jump_next_to_a_vehicle_run_is_the_same_road_trip():
    track = Track.from_pings(pings_along(0, 400, 10))
    runs = build_runs(track, [V] * 20 + [J] + [V] * 20)  # GPS lost in a tunnel
    assert [r.motion for r in runs] == [V]


# --- step 3: MTR ---

def _drive(**kw):
    return track_and_runs((V, pings_along(0, 300, 1, velocity_mps=(15, 0), **kw)))


def test_vehicle_run_away_from_roads_is_rail():
    track, runs = _drive()
    roads = road_index([(-100, -100), (6000, -100)]).nearest(track.pings)  # a road 100 m away
    assert [r.motion for r in find_mtr(track, runs, roads)] == [Motion.RAIL]


def test_vehicle_run_on_a_road_stays_vehicle():
    track, runs = _drive()
    roads = road_index([(-100, 3), (6000, 3)]).nearest(track.pings)
    assert [r.motion for r in find_mtr(track, runs, roads)] == [V]


def test_rail_test_only_applies_inside_hong_kong():
    macau = [Ping(p.utc, p.lng - 0.6, p.lat - 0.09) for p in pings_along(0, 300, 1, velocity_mps=(15, 0))]
    track, runs = track_and_runs((V, macau))
    assert [r.motion for r in find_mtr(track, runs, far_roads(track))] == [V]


@pytest.mark.parametrize("stops, expected", [
    (cfg.MTR_MAX_JUMPS, J),       # a fix at each station of an underground ride
    (cfg.MTR_MAX_JUMPS + 1, V),   # a phone logging once a minute on the road
])
def test_many_jumps_in_a_row_is_sparse_logging_not_mtr(stops, expected):
    pings = [ping(0)] + [ping(k * 60, east_m=k * 600) for k in range(1, stops + 1)]
    track, runs = track_and_runs((J, pings))
    roads = road_index([(-100, 0), (100000, 0)]).nearest(track.pings)  # on the road
    assert [r.motion for r in find_mtr(track, runs, roads)] == [expected]


@pytest.mark.parametrize("east_m, expected", [
    (350, W),    # data/samples/1010 21:45: GPS caught up with a walk in one 2 s jump
    (1000, J),
])
def test_jump_run_shorter_than_an_mtr_ride_is_a_walk(east_m, expected):
    track, runs = track_and_runs(
        (S, jittering(0, 600, 10)),
        (J, [ping(602, east_m=east_m)]),
        (S, jittering(610, 1200, 10, centre_m=(east_m, 0))),
    )
    assert [r.motion for r in find_mtr(track, runs, far_roads(track))] == [S, expected, S]


def test_jump_too_fast_for_mtr_started_earlier():
    station = jittering(0, 600, 10)
    arrival = [ping(620, east_m=3000), ping(630, east_m=3000)]  # 3 km in 20 s: stale pings before it
    track, runs = track_and_runs((S, station), (J, arrival[:1]), (S, arrival[1:]))
    jump = next(r for r in find_mtr(track, runs, far_roads(track)) if r.motion == J)
    needed_s = (at(east_m=3000)[0] - LNG0) / cfg.MTR_MAX_AVG_V
    assert jump.seconds(track) >= needed_s
    assert jump.seconds(track) < needed_s + 10


@pytest.mark.parametrize("stop_minutes, expected", [(5, Motion.INTERCHANGE), (15, S)])
def test_short_stop_between_two_mtr_rides_is_an_interchange(stop_minutes, expected):
    stop_s = stop_minutes * 60
    track, runs = track_and_runs(
        (J, [ping(0), ping(300, east_m=3000)]),
        (S, jittering(310, 300 + stop_s, 10, centre_m=(3000, 0))),
        (J, [ping(310 + stop_s, east_m=6000)]),
    )
    assert [r.motion for r in find_mtr(track, runs, far_roads(track))] == [J, expected, J]


# --- step 4: decision tree ---

def _classify(*segments, labels=()):
    track, runs = track_and_runs(*segments)
    return [(c.io, c.reason) for c in classify_runs(track, runs, labels)]


@pytest.mark.parametrize("motion, io, reason", [
    (J, io_cfg.MTR, Reason.JUMP),
    (Motion.RAIL, io_cfg.MTR, Reason.RAIL),
    (Motion.INTERCHANGE, io_cfg.MTR, Reason.INTERCHANGE),
    (V, io_cfg.VEHICLE, Reason.VEHICLE),
    (W, io_cfg.OUTDOOR, Reason.WALK),
])
def test_moving_runs(motion, io, reason):
    assert _classify((motion, pings_along(0, 60, 10))) == [(io, reason)]


def test_long_stay_at_night_is_home():
    assert _classify((S, jittering(0, 30 * 60, 60))) == [(io_cfg.HOME, Reason.NIGHT_STAY)]


def test_stay_overlapping_night_hours_from_the_evening_is_home():
    assert _classify((S, jittering(-2 * HOUR, 1 * HOUR, 60))) == [(io_cfg.HOME, Reason.NIGHT_STAY)]  # 22:00-01:00


def test_long_stay_by_day_is_other_indoor():
    assert _classify((S, jittering(12 * HOUR, 13 * HOUR, 60))) == [(io_cfg.OTHER_INDOOR, Reason.LONG_STAY)]


def test_short_stay_is_outdoor():
    assert _classify((S, jittering(12 * HOUR, 12 * HOUR + 600, 60))) == [(io_cfg.OUTDOOR, Reason.SHORT_STOP)]


def test_stay_at_the_place_of_a_night_stay_is_home():
    night = jittering(2 * HOUR, 7 * HOUR, 300)
    walk = pings_along(7 * HOUR + 10, 7 * HOUR + 600, 10, velocity_mps=(1, 0))
    evening = jittering(19 * HOUR, 19 * HOUR + 600, 60, centre_m=(0, 30))
    assert _classify((S, night), (W, walk), (S, evening)) == [
        (io_cfg.HOME, Reason.NIGHT_STAY), (io_cfg.OUTDOOR, Reason.WALK), (io_cfg.HOME, Reason.NEAR_NIGHT_STAY),
    ]


# --- step 5: points ---

def _points(*segments, roads=None):
    track, runs = track_and_runs(*segments)
    classified = classify_runs(track, runs)
    nearest = (roads or NO_ROADS).nearest(track.pings)
    return build_points(track, classified, nearest), track


def test_stay_is_one_point_at_the_median_owning_the_whole_stay():
    points, track = _points((S, jittering(12 * HOUR, 13 * HOUR, 60, jitter_m=20)))
    (p,) = points
    assert (p.kind, p.io, p.io_source) == (PointKind.STAY, io_cfg.OTHER_INDOOR, IoSource.INFERRED)
    assert (p.start_utc, p.end_utc) == (track.pings[0].utc, track.pings[-1].utc)
    assert (p.lng, p.lat) == (LNG0, LAT0)  # 31 of the 61 pings are at the centre, 30 hop 20 m east
    assert p.heading_deg is None and p.in_hk


def test_straight_route_is_thinned_keeps_its_time_and_heads_east():
    points, track = _points((W, pings_along(0, 600, 1, velocity_mps=(1.5, 0))))  # 900 m east
    assert 2 <= len(points) < 10
    assert {p.kind for p in points} == {PointKind.ROUTE}
    assert {p.compass for p in points} == {"E"} and points[0].heading_deg == pytest.approx(90, abs=0.5)
    assert sum((p.end_utc - p.start_utc).total_seconds() for p in points) == 600
    gaps = [abs(b.lng - a.lng) for a, b in zip(points, points[1:])]
    assert max(gaps) <= cfg.ROUTE_MAX_GAP_DEG * 1.01


def test_route_keeps_its_corner():
    east = pings_along(0, 400, 1, velocity_mps=(1.5, 0))
    north = pings_along(401, 800, 1, start_m=(601.5, 0), velocity_mps=(0, 1.5))
    points, _ = _points((W, east + north))
    corner = min(points, key=lambda p: abs(p.lng - at(east_m=600)[0]) + abs(p.lat - LAT0))
    assert abs(corner.lng - at(east_m=600)[0]) + abs(corner.lat - LAT0) < 0.0002  # within ~20 m
    assert [p.compass for p in points][0] == "E" and points[-1].compass == "N"


def test_both_ends_of_a_gps_gap_are_kept():
    leg = pings_along(0, 60, 1, velocity_mps=(10, 0))
    after = pings_along(600, 660, 1, start_m=(5600, 0), velocity_mps=(10, 0))  # 5 km while GPS was dark
    points, _ = _points((J, leg + after))
    starts = {p.start_utc for p in points}
    assert leg[-1].utc in starts and after[0].utc in starts


def test_vehicle_points_name_their_road():
    drive = pings_along(0, 120, 1, velocity_mps=(12, 0))
    named, _ = _points((V, drive), roads=road_index([(-100, 5), (3000, 5)]))
    unnamed, _ = _points((V, drive), roads=road_index([(-100, 200), (3000, 200)]))
    assert {p.road_name for p in named} == {"ROAD 0"}
    assert {p.road_name for p in unnamed} == {None}


def test_consecutive_routes_share_their_boundary_ping_once():
    walk = pings_along(0, 300, 5, velocity_mps=(1.2, 0))
    drive = pings_along(301, 600, 1, start_m=(362, 0), velocity_mps=(12, 0))
    points, track = _points((W, walk), (V, drive))
    starts = [p.start_utc for p in points]
    assert len(starts) == len(set(starts))
    assert sum((p.end_utc - p.start_utc).total_seconds() for p in points) == 600


# --- step 5: hourly slices and request rows ---

def test_point_crossing_hours_is_split_and_prorated():
    points, _ = _points((S, jittering(30 * 60, 150 * 60, 60)))  # 00:30-02:30 HK
    slices = split_by_hour(points)
    assert [s.delta_t_hours for s in slices] == [0.5, 1.0, 0.5]
    assert [s.hour_start_hk for s in slices] == [datetime(2026, 8, 30, h) for h in (0, 1, 2)]


def test_request_rows_use_the_api_labels():
    points = [_point(io) for io in io_cfg.IO_TYPES]
    rows = to_request_rows("p1", split_by_hour(points))
    assert [r[4] for r in rows] == [{"IO": io_cfg.API_LABELS[io]} for io in io_cfg.IO_TYPES]
    assert rows[0][:4] == ["20260830000000", "p1", LNG0, LAT0] and rows[0][5] == 0.016667


def _point(io):
    return Point(kind=PointKind.STAY, io=io, io_source=IoSource.INFERRED, reason=Reason.LONG_STAY,
                 start_utc=T0, end_utc=T0 + timedelta(minutes=1), lng=LNG0, lat=LAT0, in_hk=True)


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


# --- the whole pipeline ---

def _records(pings):
    return [
        {"type": "Feature", "properties": {"time": p.utc.isoformat()}, "geometry": {"coordinates": [p.lng, p.lat]}}
        for p in pings
    ]


def test_a_whole_morning():
    """Home all night, walk to the main road, drive along it, work at a labelled office."""
    home = jittering(0, 7 * HOUR, 60)
    walk = pings_along(7 * HOUR + 5, 7 * HOUR + 600, 5, velocity_mps=(1.3, 0))
    drive = pings_along(7 * HOUR + 601, 7 * HOUR + 1500, 1, start_m=(780, 0), velocity_mps=(12, 0))
    office_m = (780 + 12 * 899 + 30, 20)
    office = jittering(7 * HOUR + 1560, 12 * HOUR, 60, centre_m=office_m)
    label = PlaceLabel("o", "c", io_cfg.OFFICE, *at(*office_m), radius_m=25)
    roads = road_index([(-500, 0), (20000, 0)])

    result = run_pipeline(_records(home + walk + drive + office), roads, [label])

    io_order = [p.io for p in result.points]
    assert [io for i, io in enumerate(io_order) if i == 0 or io != io_order[i - 1]] == [
        io_cfg.HOME, io_cfg.OUTDOOR, io_cfg.VEHICLE, io_cfg.OFFICE,
    ]
    assert result.pings == len(home + walk + drive + office) and len(result.points) < result.pings / 10
    assert sum(s.delta_t_hours for s in result.slices) == pytest.approx(12)
    assert {p.road_name for p in result.points if p.io == io_cfg.VEHICLE} == {"ROAD 0"}
    office_point = result.points[-1]
    assert (office_point.io_source, office_point.reason) == (IoSource.LABEL, Reason.PLACE_LABEL)


def test_no_pings_no_output():
    result = run_pipeline([], NO_ROADS)
    assert (result.points, result.slices, result.first_ping_utc) == ([], [], None)


def test_one_ping_owns_no_time():
    result = run_pipeline(_records([ping(0)]), NO_ROADS)
    assert result.slices == [] and result.pings == 1
