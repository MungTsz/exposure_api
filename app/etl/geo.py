"""
Shared geometry helpers. Every distance/angle/box check in the pipeline and
in io type rules goes through here.

Works on anything with `lng` and `lat` attributes (Ping, Stay, ...). Uses a
local flat-earth projection, which is accurate enough at walking/vehicle
scale.
"""

import math
from typing import Protocol, Tuple

from app.config.constants import EARTH_RADIUS_M


class HasLngLat(Protocol):
    lng: float
    lat: float


def to_xy_meters(lng: float, lat: float, origin_lng: float, origin_lat: float) -> Tuple[float, float]:
    """Position in meters east (x) and north (y) of the origin."""
    x = math.radians(lng - origin_lng) * math.cos(math.radians(origin_lat)) * EARTH_RADIUS_M
    y = math.radians(lat - origin_lat) * EARTH_RADIUS_M
    return x, y


def manhattan_degrees(a: HasLngLat, b: HasLngLat) -> float:
    """|dlat| + |dlng| in degrees - a flat L1 approximation, not a haversine
    distance (deliberately simple, no trig)."""
    return abs(b.lat - a.lat) + abs(b.lng - a.lng)


def angle_at_deg(a: HasLngLat, b: HasLngLat, c: HasLngLat) -> float:
    """Angle ABC in degrees. 180 = straight line, near 0 = sharp turn back."""
    ax, ay = to_xy_meters(a.lng, a.lat, b.lng, b.lat)
    cx, cy = to_xy_meters(c.lng, c.lat, b.lng, b.lat)
    mag_a, mag_c = math.hypot(ax, ay), math.hypot(cx, cy)
    if mag_a == 0 or mag_c == 0:
        return 180.0  # degenerate (duplicate points) - treat as a normal straight path
    cos_theta = max(-1.0, min(1.0, (ax * cx + ay * cy) / (mag_a * mag_c)))
    return math.degrees(math.acos(cos_theta))


def within_degree_box(ref: HasLngLat, p: HasLngLat, threshold_deg: float) -> bool:
    """True if p is within threshold_deg of ref on both lat and lng - a flat
    lat/lng box, not a haversine distance (deliberately simple)."""
    return abs(p.lat - ref.lat) <= threshold_deg and abs(p.lng - ref.lng) <= threshold_deg


class HasCircle(HasLngLat, Protocol):
    radius_m: float


def distance_m(a: HasLngLat, b: HasLngLat) -> float:
    return math.hypot(*to_xy_meters(b.lng, b.lat, a.lng, a.lat))


def within_circle(circle: HasCircle, p: HasLngLat) -> bool:
    return distance_m(circle, p) <= circle.radius_m


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
