"""
Parser for GPX track points (GPX 1.0 or 1.1, e.g. a RouteHistory export):

    <gpx ...><trk><trkseg>
      <trkpt lat="22.3376" lon="114.2636">
        <time>2026-10-09T06:27:19Z</time>
        <ele>143.07</ele>
      </trkpt>
    </trkseg></trk></gpx>

An uploaded GPX file is first turned into one record per <trkpt> by
read_gpx(); those records are what a batch stores, so a stored batch is
parsed the same way as a GeoJSON one:

    {"type": "gpx_trkpt", "lat": "22.3376", "lon": "114.2636",
     "time": "2026-10-09T06:27:19Z", "speed": None}

`<time>` is required (a point without it cannot be placed in time).
`<speed>` (m/s) exists only in GPX 1.0 and is optional. `<ele>`, waypoints
(<wpt>), planned routes (<rte>) and extensions are not used.
"""

import math
from datetime import timezone
from typing import Any, Dict, List, Optional
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from app.config.constants import GPX_TRKPT_RECORD_TYPE
from app.etl.models import Ping
from app.etl.parsers.common import clean_speed, parse_iso_utc


class GpxError(ValueError):
    """The uploaded file is not a readable GPX document."""


def _local_name(tag: Any) -> str:
    """'{http://www.topografix.com/GPX/1/1}trkpt' -> 'trkpt' (works for any
    GPX version's namespace, or none)."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child_text(element: Element, name: str) -> Optional[str]:
    for child in element:
        if _local_name(child.tag) == name:
            text = (child.text or "").strip()
            return text or None
    return None


def read_gpx(data: bytes) -> List[Dict[str, Any]]:
    """One record per <trkpt> in the file, in file order. Values are kept
    as the file's strings; parse() validates them. Raises GpxError if the
    file is not well-formed XML or not a GPX document."""
    try:
        root = fromstring(data)
    except (ParseError, DefusedXmlException) as e:
        raise GpxError(f"not a readable XML document ({e})") from e
    if _local_name(root.tag) != "gpx":
        raise GpxError(f"root element is <{_local_name(root.tag)}>, expected <gpx>")

    return [
        {
            "type": GPX_TRKPT_RECORD_TYPE,
            "lat": point.get("lat"),
            "lon": point.get("lon"),
            "time": _child_text(point, "time"),
            "speed": _child_text(point, "speed"),
        }
        for point in root.iter()
        if _local_name(point.tag) == "trkpt"
    ]


def can_parse(record: Dict[str, Any]) -> bool:
    return isinstance(record, dict) and record.get("type") == GPX_TRKPT_RECORD_TYPE


def _to_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse(record: Dict[str, Any]) -> Optional[Ping]:
    """None (the point is dropped) if its time is missing or unreadable, or
    its lat/lon is not a valid coordinate."""
    lat, lng = _to_float(record.get("lat")), _to_float(record.get("lon"))
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None

    time_text = record.get("time")
    if not isinstance(time_text, str):
        return None
    try:
        utc = parse_iso_utc(time_text)
    except ValueError:
        return None
    if utc.tzinfo is None:
        # GPX times are UTC by spec; some writers leave off the 'Z'.
        utc = utc.replace(tzinfo=timezone.utc)

    return Ping(utc=utc, lng=lng, lat=lat, speed_mps=clean_speed(_to_float(record.get("speed"))))
