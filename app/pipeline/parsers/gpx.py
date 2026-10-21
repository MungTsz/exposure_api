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

    {"type": "gpx_trkpt", "lat": "22.3376", "lon": "114.2636", "time": "2026-10-09T06:27:19Z"}

Only lat, lon and <time> are read. `<time>` is required (a point without it
cannot be placed in time). <ele>, <speed>, waypoints (<wpt>), planned routes
(<rte>) and extensions are not used.
"""

from typing import Any, Dict, List, Optional
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from app.config.constants import GPX_TRKPT_RECORD_TYPE
from app.pipeline.models import Ping
from app.pipeline.parsers.common import parse_lng_lat, parse_utc


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
        }
        for point in root.iter()
        if _local_name(point.tag) == "trkpt"
    ]


def can_parse(record: Dict[str, Any]) -> bool:
    return isinstance(record, dict) and record.get("type") == GPX_TRKPT_RECORD_TYPE


def parse(record: Dict[str, Any]) -> Optional[Ping]:
    """None (the point is dropped) if its time or position is missing or invalid."""
    position = parse_lng_lat(record.get("lon"), record.get("lat"))
    utc = parse_utc(record.get("time"))
    if position is None or utc is None:
        return None
    return Ping(utc=utc, lng=position[0], lat=position[1])
