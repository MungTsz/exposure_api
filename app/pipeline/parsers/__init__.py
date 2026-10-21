"""
Input: turns raw records into Pings (time, lng, lat - nothing else is read).
Each input format has its own module here: geojson.py (GeoJSON Point
Features) and gpx.py (GPX track points; an uploaded GPX file is first split
into records by gpx.read_gpx). A batch may mix formats: every record is
handed to the first parser whose can_parse() accepts it.

To support a new input format (e.g. live device tracking):
  1. Add a module here with `can_parse(record) -> bool` and
     `parse(record) -> Optional[Ping]` (return None to drop a record with
     no valid time or position). Reuse the helpers in common.py.
  2. Append it to PARSERS below.
"""

import logging
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from app.pipeline.models import Ping
from app.pipeline.parsers import geojson, gpx

logger = logging.getLogger(__name__)


class Parser(NamedTuple):
    name: str
    can_parse: Callable[[Dict[str, Any]], bool]
    parse: Callable[[Dict[str, Any]], Optional[Ping]]


# Tried in order for each record.
PARSERS: List[Parser] = [
    Parser("geojson", geojson.can_parse, geojson.parse),
    Parser("gpx", gpx.can_parse, gpx.parse),
]


def find_parser(record: Dict[str, Any]) -> Optional[Parser]:
    """The first parser that accepts `record`, or None."""
    return next((p for p in PARSERS if p.can_parse(record)), None)


def parse_record(record: Dict[str, Any]) -> Optional[Ping]:
    """One record's Ping, or None if no parser recognizes it or its parser
    drops it. Doesn't log: used to sort a stored batch's records one by one
    (see app/services/day_service.py), where parse_pings's warning would
    repeat once per record."""
    parser = find_parser(record)
    return parser.parse(record) if parser else None


def parse_pings(records: List[Dict[str, Any]]) -> List[Ping]:
    """Parse raw records into a time-sorted Ping list. Records no parser
    recognizes, or with no valid time or position, are skipped and logged."""
    pings: List[Ping] = []
    unrecognized = invalid = 0
    for record in records:
        parser = find_parser(record)
        if parser is None:
            unrecognized += 1
            continue
        ping = parser.parse(record)
        if ping is None:
            invalid += 1
        else:
            pings.append(ping)
    if unrecognized:
        logger.warning("skipped %d record(s) in an unrecognized input format", unrecognized)
    if invalid:
        logger.warning("skipped %d record(s) with no valid time or position", invalid)
    pings.sort(key=lambda p: p.utc)
    return pings
