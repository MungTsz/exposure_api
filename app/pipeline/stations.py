"""
MTR stations: for each ping, how far the nearest MTR station entrance is.

Built once per process from the station entrances file
(settings.mtr_stations_path, from OpenStreetMap; app/services/station_service.py
loads and refreshes it). Used by the MTR step (app/pipeline/mtr.py): an MTR
ride starts and ends at a station.

Distances are in metres on the same flat projection as the roads
(app/pipeline/roads.py), so they are only meaningful inside Hong Kong.
"""

import math
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np
import shapely

from app.pipeline.geo import HasLngLat
from app.pipeline.roads import project


class StationIndex:
    """Nearest-entrance lookups over a fixed set of (lng, lat) points."""

    def __init__(self, entrances: Iterable[Tuple[float, float]]):
        lng_lat = np.asarray(list(entrances), dtype=float).reshape(-1, 2)
        self._count = len(lng_lat)
        self._tree = shapely.STRtree(shapely.points(project(lng_lat)))

    def __len__(self) -> int:
        return self._count

    @classmethod
    def from_geojson(cls, data: Dict[str, Any]) -> "StationIndex":
        """From a GeoJSON FeatureCollection of Point features in WGS84 lng/lat."""
        return cls(
            tuple(f["geometry"]["coordinates"][:2]) for f in data.get("features", [])
            if (f.get("geometry") or {}).get("type") == "Point"
        )

    def distances_m(self, points: Sequence[HasLngLat]) -> List[float]:
        """Metres from each point to the nearest entrance, in order
        (math.inf if the index is empty)."""
        if not points or not self._count:
            return [math.inf] * len(points)
        xy = project(np.array([(p.lng, p.lat) for p in points], dtype=float))
        (point_idx, _), distances = self._tree.query_nearest(
            shapely.points(xy), return_distance=True, all_matches=False,
        )
        out = [math.inf] * len(points)
        for p, d in zip(point_idx, distances):
            out[p] = float(d)
        return out
