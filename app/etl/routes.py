"""Thins each continuous moving route down to roughly one point per few hundred meters."""

from dataclasses import replace
from typing import List

from app.config import pipeline as cfg
from app.etl.geo import meters_between, within_degree_box
from app.etl.models import Stay
from app.io_types import registry
from app.io_types.base import IoKind


def _is_moving(stay: Stay) -> bool:
    return registry.get(stay.io).kind == IoKind.MOVING


def _continues_route(prev: Stay, curr: Stay) -> bool:
    """True if curr continues the same route as prev: both moving with the
    same io type, no GPS dropout (prev lasting longer than MAX_GAP_SECONDS -
    its end is the next ping's time) and no big jump between them."""
    return (
        prev.io == curr.io
        and _is_moving(prev)
        and prev.duration_seconds <= cfg.MAX_GAP_SECONDS
        and meters_between(prev, curr) <= cfg.MAX_GAP_METERS
    )


def _route_point_indices(route: List[Stay]) -> List[int]:
    """Indices of route points to keep. Starting from the last kept point,
    a point is dropped while it stays inside a ROUTE_POINT_DEGREE_THRESHOLD
    lat/lng box of it AND less than ROUTE_POINT_MAX_SECONDS have passed;
    the first point breaking either condition is kept and becomes the new
    reference. The route's last point gets no special treatment: forcing it
    in left near-duplicate points at every route end, and if it is dropped
    its time folds into a kept point at most one box away."""
    keep = [0]
    ref = route[0]
    for m in range(1, len(route)):
        p = route[m]
        if (
            not within_degree_box(ref, p, cfg.ROUTE_POINT_DEGREE_THRESHOLD)
            or (p.start_utc - ref.start_utc).total_seconds() >= cfg.ROUTE_POINT_MAX_SECONDS
        ):
            keep.append(m)
            ref = p
    return keep


def simplify_routes(stays: List[Stay]) -> List[Stay]:
    """Thin each continuous moving route down to its route points.

    While moving, 1Hz tracking makes nearly every ping its own ~1-second
    stay, which floods expo_calx with rows and the app's map with points.
    Consecutive moving stays are grouped into routes (see _continues_route),
    each route is reduced with _route_point_indices, and every kept point
    becomes one stay at its own position, extended to cover the time (and
    pings) of the dropped points after it - total time is preserved
    exactly. Stationary stays pass through untouched.
    """
    out: List[Stay] = []
    i, n = 0, len(stays)
    while i < n:
        if not _is_moving(stays[i]):
            out.append(stays[i])
            i += 1
            continue
        j = i + 1
        while j < n and _continues_route(stays[j - 1], stays[j]):
            j += 1
        route = stays[i:j]
        kept = _route_point_indices(route)
        for a, b in zip(kept, kept[1:] + [len(route)]):
            merged_pings = [p for stay in route[a:b] for p in stay.pings]
            out.append(replace(route[a], end_utc=route[b - 1].end_utc, pings=merged_pings))
        i = j
    return out
