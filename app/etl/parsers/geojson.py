"""
Parser for GeoJSON Point Features (the watch export format):

    {"type": "Feature",
     "properties": {"time": "2026-08-29T16:00:03.048Z", "speed": 0.05, ...},
     "geometry": {"type": "Point", "coordinates": [lng, lat]}}

`properties.speed` (m/s) is optional.
"""

from typing import Any, Dict

from app.etl.models import Ping
from app.etl.parsers.common import clean_speed, parse_iso_utc


def can_parse(record: Dict[str, Any]) -> bool:
    return isinstance(record, dict) and "geometry" in record


def parse(record: Dict[str, Any]) -> Ping:
    props = record["properties"]
    lng, lat = record["geometry"]["coordinates"][0], record["geometry"]["coordinates"][1]
    return Ping(utc=parse_iso_utc(props["time"]), lng=lng, lat=lat, speed_mps=clean_speed(props.get("speed")))
