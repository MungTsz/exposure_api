"""
Step 5a: the points the app draws, each standing for a stretch of time.

  - a stay (still run) or an MTR interchange -> 1 point at the median of its
    pings, which also removes the indoor / in-station GPS jitter
  - a route (walk / vehicle / MTR run) -> its pings thinned by
    Douglas-Peucker (the thinned line stays within ROUTE_TOL_M of every
    ping, so every turn is kept) plus at least one point every
    ROUTE_MAX_GAP_DEG, plus both ends of every GPS gap (e.g. the two
    stations of an underground MTR ride)

A point owns the time until the next point. Route points carry the heading
towards the next point (heading_deg, compass) and Vehicle points the name
of the road they are on. A route's last ping only closes the line (it owns
no time), so it is not a point; neither is any other point without time.
"""

from statistics import median
from typing import List, Optional, Sequence

from app.config import io_types as io_cfg
from app.config import pipeline as cfg
from app.config.constants import PointKind
from app.pipeline.geo import bearing_deg, compass_point, deg_between, douglas_peucker, to_xy_meters
from app.pipeline.models import STAY_MOTIONS, ClassifiedRun, Point, Track
from app.pipeline.region import is_in_hk
from app.pipeline.roads import NearestRoad


def build_points(track: Track, runs: Sequence[ClassifiedRun], roads: Sequence[NearestRoad]) -> List[Point]:
    """`runs` from classify_runs, in order; `roads` holds each ping's nearest road."""
    if not track.pings:
        return []
    origin = track.pings[0]
    xy = [to_xy_meters(p.lng, p.lat, origin.lng, origin.lat) for p in track.pings]
    points: List[Point] = []
    for n, classified in enumerate(runs):
        if classified.run.motion in STAY_MOTIONS:
            points.append(_stay_point(track, classified))
        else:
            next_is_route = n + 1 < len(runs) and runs[n + 1].run.motion not in STAY_MOTIONS
            points.extend(_route_points(track, classified, xy, roads, next_is_route))
    return [p for p in points if p.end_utc > p.start_utc]


def _stay_point(track: Track, classified: ClassifiedRun) -> Point:
    pings = classified.run.pings(track)
    lng, lat = median(p.lng for p in pings), median(p.lat for p in pings)
    return Point(
        kind=PointKind.STAY, io=classified.io, io_source=classified.io_source, reason=classified.reason,
        start_utc=pings[0].utc, end_utc=pings[-1].utc, lng=lng, lat=lat, in_hk=is_in_hk(lng, lat),
    )


def _route_points(
    track: Track, classified: ClassifiedRun, xy: Sequence, roads: Sequence[NearestRoad], next_is_route: bool,
) -> List[Point]:
    run = classified.run
    kept = _thin(track, list(range(run.first_ping, run.last + 1)), xy)
    headings = _headings([xy[i] for i in kept])
    ends = [track.pings[i].utc for i in kept[1:]] + [track.pings[run.last].utc]
    if next_is_route:
        # The next route starts with this run's last ping; it is its point.
        kept, headings, ends = kept[:-1], headings[:-1], ends[:-1]

    points = []
    for i, heading, end in zip(kept, headings, ends):
        ping = track.pings[i]
        points.append(Point(
            kind=PointKind.ROUTE, io=classified.io, io_source=classified.io_source, reason=classified.reason,
            start_utc=ping.utc, end_utc=end, lng=ping.lng, lat=ping.lat, in_hk=is_in_hk(ping.lng, ping.lat),
            heading_deg=heading, compass=compass_point(heading),
            road_name=_road_name(classified.io, roads[i]),
        ))
    return points


def _thin(track: Track, indices: List[int], xy: Sequence) -> List[int]:
    """The ping indices of a route to keep (see the module docstring)."""
    keep = douglas_peucker([xy[i][0] for i in indices], [xy[i][1] for i in indices], cfg.ROUTE_TOL_M)
    last = 0
    for j in range(1, len(indices)):
        if deg_between(track.pings[indices[j]], track.pings[indices[last]]) >= cfg.ROUTE_MAX_GAP_DEG:
            keep[j] = True
            last = j
        if track.dd_deg[indices[j]] >= cfg.JUMP_DEG:  # both ends of a GPS gap
            keep[j] = keep[j - 1] = True
    return [i for i, k in zip(indices, keep) if k]


def _headings(xy: Sequence) -> List[Optional[float]]:
    """Heading from each point to the next; the last repeats the one before."""
    if len(xy) < 2:
        return [None] * len(xy)
    headings = [bearing_deg(*a, *b) for a, b in zip(xy, xy[1:])]
    return headings + [headings[-1]]


def _road_name(io: str, road: NearestRoad) -> Optional[str]:
    return road.name if io == io_cfg.VEHICLE and road.distance_m <= cfg.ROAD_NAME_MAX_M else None
