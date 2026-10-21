"""
Parser for GeoJSON Point Features (the watch export format):

    {"type": "Feature",
     "properties": {"time": "2026-08-29T16:00:03.048Z", ...},
     "geometry": {"type": "Point", "coordinates": [lng, lat]}}

Only `properties.time` and the coordinates are read; every other property
(speed, accuracy, ...) is ignored.
"""

from typing import Any, Dict, Optional

from app.pipeline.models import Ping
from app.pipeline.parsers.common import parse_lng_lat, parse_utc


def can_parse(record: Dict[str, Any]) -> bool:
    return isinstance(record, dict) and "geometry" in record


def parse(record: Dict[str, Any]) -> Optional[Ping]:
    """None (the record is dropped) if its time or position is missing or invalid."""
    geometry, properties = record.get("geometry"), record.get("properties")
    if not isinstance(geometry, dict) or not isinstance(properties, dict):
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
        return None
    position = parse_lng_lat(coordinates[0], coordinates[1])
    utc = parse_utc(properties.get("time"))
    if position is None or utc is None:
        return None
    return Ping(utc=utc, lng=position[0], lat=position[1])
