"""
Shared geometry helpers. Every distance, direction and shape check in the
pipeline goes through here.

Two distances are used, on purpose:
  - deg_between: |dlat| + |dlng| in degrees, for distances between pings
    (simple, no trig; the thresholds in app/config/pipeline.py use it).
  - metres, on a local flat-earth projection (to_xy_meters), for anything
    compared with real sizes: place label radii, route shape, roads.

Works on anything with `lng` and `lat` attributes (Ping, PlaceLabel, ...).
"""

import math
from typing import Iterable, List, Optional, Protocol, Sequence, Tuple

from app.config.constants import COMPASS_POINTS, EARTH_RADIUS_M


class HasLngLat(Protocol):
    lng: float
    lat: float


class HasCircle(HasLngLat, Protocol):
    radius_m: float


def deg_between(a: HasLngLat, b: HasLngLat) -> float:
    """|dlat| + |dlng| in degrees (1 m ~ 0.00001 deg in Hong Kong)."""
    return abs(b.lat - a.lat) + abs(b.lng - a.lng)


def to_xy_meters(lng: float, lat: float, origin_lng: float, origin_lat: float) -> Tuple[float, float]:
    """Position in meters east (x) and north (y) of the origin."""
    x = math.radians(lng - origin_lng) * math.cos(math.radians(origin_lat)) * EARTH_RADIUS_M
    y = math.radians(lat - origin_lat) * EARTH_RADIUS_M
    return x, y


def distance_m(a: HasLngLat, b: HasLngLat) -> float:
    return math.hypot(*to_xy_meters(b.lng, b.lat, a.lng, a.lat))


def within_circle(circle: HasCircle, p: HasLngLat) -> bool:
    return distance_m(circle, p) <= circle.radius_m


def bearing_deg(x1: float, y1: float, x2: float, y2: float) -> Optional[float]:
    """Direction from (x1, y1) to (x2, y2) in degrees, 0 = north, clockwise;
    None if the two points are the same."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return None
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360


def compass_point(heading_deg: Optional[float]) -> Optional[str]:
    """The nearest of the 8 compass directions (N, NE, E, ...)."""
    if heading_deg is None:
        return None
    return COMPASS_POINTS[int(((heading_deg + 22.5) % 360) // 45)]


def douglas_peucker(xs: Sequence[float], ys: Sequence[float], tolerance: float) -> List[bool]:
    """Douglas-Peucker line simplification: True for each point to keep so
    that the line through the kept points stays within `tolerance` of every
    point. The first and last points are always kept."""
    n = len(xs)
    keep = [False] * n
    if n == 0:
        return keep
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        dx, dy = xs[b] - xs[a], ys[b] - ys[a]
        seg2 = dx * dx + dy * dy
        worst, worst_dist = -1, -1.0
        for i in range(a + 1, b):
            px, py = xs[i] - xs[a], ys[i] - ys[a]
            u = min(1.0, max(0.0, (px * dx + py * dy) / seg2)) if seg2 else 0.0
            dist = math.hypot(px - u * dx, py - u * dy)
            if dist > worst_dist:
                worst, worst_dist = i, dist
        if worst_dist > tolerance:
            keep[worst] = True
            stack += [(a, worst), (worst, b)]
    return keep


# (min_lng, min_lat, max_lng, max_lat) in degrees.
BBox = Tuple[float, float, float, float]


def bbox(points: Iterable[HasLngLat]) -> BBox:
    """The smallest lat/lng box holding every point (at least one)."""
    points = list(points)
    lngs, lats = [p.lng for p in points], [p.lat for p in points]
    return min(lngs), min(lats), max(lngs), max(lats)


def circle_bbox(circle: HasCircle, extra_m: float = 0.0) -> BBox:
    """The lat/lng box around the circle with its radius grown by extra_m."""
    r = circle.radius_m + extra_m
    dlat = math.degrees(r / EARTH_RADIUS_M)
    dlng = math.degrees(r / (EARTH_RADIUS_M * math.cos(math.radians(circle.lat))))
    return circle.lng - dlng, circle.lat - dlat, circle.lng + dlng, circle.lat + dlat


def circle_overlap_ratio(a: HasCircle, b: HasCircle) -> float:
    """Intersection area / union area of two circles: 1 = the same circle,
    0 = no overlap. A small circle inside a big one scores low."""
    d, r1, r2 = distance_m(a, b), a.radius_m, b.radius_m
    if d >= r1 + r2:
        return 0.0
    if d <= abs(r1 - r2):
        intersection = math.pi * min(r1, r2) ** 2
    else:
        intersection = (
            r1 ** 2 * math.acos((d ** 2 + r1 ** 2 - r2 ** 2) / (2 * d * r1))
            + r2 ** 2 * math.acos((d ** 2 + r2 ** 2 - r1 ** 2) / (2 * d * r2))
            - 0.5 * math.sqrt((-d + r1 + r2) * (d + r1 - r2) * (d - r1 + r2) * (d + r1 + r2))
        )
    union = math.pi * r1 ** 2 + math.pi * r2 ** 2 - intersection
    return intersection / union
