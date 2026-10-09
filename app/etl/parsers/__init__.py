"""
Turns raw input records into Pings. Each input format has its own module
here. A batch may mix formats: every record is handed to the first parser
whose can_parse() accepts it.

To support a new input format (e.g. live device tracking):
  1. Add a module here with `can_parse(record) -> bool` and
     `parse(record) -> Optional[Ping]` (return None to drop a record on
     purpose). Reuse the helpers in common.py.
  2. Append it to PARSERS below.
"""

import logging
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from app.etl.models import Ping
from app.etl.parsers import geojson

logger = logging.getLogger(__name__)


class Parser(NamedTuple):
    name: str
    can_parse: Callable[[Dict[str, Any]], bool]
    parse: Callable[[Dict[str, Any]], Optional[Ping]]


# Tried in order for each record.
PARSERS: List[Parser] = [
    Parser("geojson", geojson.can_parse, geojson.parse),
]


def parse_pings(records: List[Dict[str, Any]]) -> List[Ping]:
    """Parse raw records into a time-sorted Ping list. Records no parser
    recognizes are skipped and logged."""
    pings: List[Ping] = []
    unrecognized = 0
    for record in records:
        parser = next((p for p in PARSERS if p.can_parse(record)), None)
        if parser is None:
            unrecognized += 1
            continue
        ping = parser.parse(record)
        if ping is not None:
            pings.append(ping)
    if unrecognized:
        logger.warning("skipped %d record(s) in an unrecognized input format", unrecognized)
    pings.sort(key=lambda p: p.utc)
    return pings
