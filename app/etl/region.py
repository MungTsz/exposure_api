"""
Marks which hourly slices are inside Hong Kong. expo_calx only scores HK
points, so slices outside it are kept (the app still draws them) but never
sent to the API.

The check is a bounding-box reject followed by a ray-casting
point-in-polygon test against constants.HK_BOUNDARY. Most points far from
HK fail the box test after four comparisons; the polygon test is O(number
of vertices) with no allocation.
"""

from typing import List, Sequence, Tuple

from app.config.constants import HK_BOUNDARY
from app.etl.models import HourlySlice

# Each edge as (lng1, lat1, lng2, lat2), closing the polygon back to the start.
_EDGES: Tuple[Tuple[float, float, float, float], ...] = tuple(
    (*a, *b) for a, b in zip(HK_BOUNDARY, HK_BOUNDARY[1:] + HK_BOUNDARY[:1])
)
_MIN_LNG = min(lng for lng, _ in HK_BOUNDARY)
_MAX_LNG = max(lng for lng, _ in HK_BOUNDARY)
_MIN_LAT = min(lat for _, lat in HK_BOUNDARY)
_MAX_LAT = max(lat for _, lat in HK_BOUNDARY)


def _in_polygon(lng: float, lat: float, edges: Sequence[Tuple[float, float, float, float]]) -> bool:
    inside = False
    for x1, y1, x2, y2 in edges:
        if (y1 > lat) != (y2 > lat) and lng < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def is_in_hk(lng: float, lat: float) -> bool:
    if not (_MIN_LNG <= lng <= _MAX_LNG and _MIN_LAT <= lat <= _MAX_LAT):
        return False
    return _in_polygon(lng, lat, _EDGES)


def mark_in_hk(slices: List[HourlySlice]) -> List[HourlySlice]:
    """Set each slice's `in_hk` from its position."""
    for s in slices:
        s.in_hk = is_in_hk(s.lng, s.lat)
    return slices
