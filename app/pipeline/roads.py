"""
Road centrelines: for each ping, how far the nearest road is and its name.

Built once per process from the Lands Department "Road Centreline" GeoJSON
(constants.ROAD_CENTRELINES_URL; app/services/road_service.py loads the
file). Used by the rail test (app/pipeline/mtr.py: a train runs beside the
roads, a car on them) and to name the road of Vehicle points.

Distances are in metres on a flat projection around ROAD_PROJECTION_ORIGIN
(the middle of Hong Kong), so they are only meaningful inside Hong Kong.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import shapely

from app.config.constants import EARTH_RADIUS_M, ROAD_NAME_PROPERTY, ROAD_PROJECTION_ORIGIN
from app.pipeline.geo import HasLngLat

_M_PER_DEG = math.radians(1) * EARTH_RADIUS_M
_ORIGIN_LNG, _ORIGIN_LAT = ROAD_PROJECTION_ORIGIN
_M_PER_DEG_LNG = _M_PER_DEG * math.cos(math.radians(_ORIGIN_LAT))

# One road line: its (lng, lat) vertices and its name (None if unnamed).
RoadLine = Tuple[Sequence[Tuple[float, float]], Optional[str]]


@dataclass(frozen=True)
class NearestRoad:
    distance_m: float     # math.inf if the index holds no road
    name: Optional[str]   # None if unnamed (or no road)


def _project(lng_lat: np.ndarray) -> np.ndarray:
    """(n, 2) array of lng, lat -> metres east, north of the origin."""
    return np.column_stack((
        (lng_lat[:, 0] - _ORIGIN_LNG) * _M_PER_DEG_LNG,
        (lng_lat[:, 1] - _ORIGIN_LAT) * _M_PER_DEG,
    ))


class RoadIndex:
    """Nearest-road lookups over a fixed set of road lines."""

    def __init__(self, lines: Iterable[RoadLine]):
        geometries, names = [], []
        for vertices, name in lines:
            if len(vertices) >= 2:
                geometries.append(shapely.LineString(_project(np.asarray(vertices, dtype=float))))
                names.append(name)
        self._names: List[Optional[str]] = names
        self._tree = shapely.STRtree(geometries)

    def __len__(self) -> int:
        return len(self._names)

    @classmethod
    def from_geojson(cls, data: Dict[str, Any]) -> "RoadIndex":
        """From a GeoJSON FeatureCollection of LineString / MultiLineString
        features in WGS84 lng/lat; the name is the ROAD_NAME_PROPERTY
        property."""
        lines: List[RoadLine] = []
        for feature in data.get("features", []):
            geometry = feature.get("geometry") or {}
            name = (feature.get("properties") or {}).get(ROAD_NAME_PROPERTY)
            if geometry.get("type") == "LineString":
                lines.append((geometry["coordinates"], name))
            elif geometry.get("type") == "MultiLineString":
                lines.extend((part, name) for part in geometry["coordinates"])
        return cls(lines)

    def nearest(self, points: Sequence[HasLngLat]) -> List[NearestRoad]:
        """The nearest road of each point, in order."""
        if not points:
            return []
        if not self._names:
            return [NearestRoad(math.inf, None)] * len(points)
        xy = _project(np.array([(p.lng, p.lat) for p in points], dtype=float))
        (point_idx, road_idx), distances = self._tree.query_nearest(
            shapely.points(xy), return_distance=True, all_matches=False,
        )
        out: List[NearestRoad] = [NearestRoad(math.inf, None)] * len(points)
        for p, r, d in zip(point_idx, road_idx, distances):
            out[p] = NearestRoad(float(d), self._names[r])
        return out
