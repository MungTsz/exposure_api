"""Helpers shared by every input-format parser: read a time and a position,
or None if the value is missing or invalid."""

import math
from datetime import datetime, timezone
from typing import Any, Optional, Tuple


def parse_utc(text: Any) -> Optional[datetime]:
    """An ISO-8601 timestamp as an aware datetime. A trailing 'Z' means UTC;
    a time without a zone is taken as UTC (GPX times are UTC by spec and
    some writers leave off the 'Z')."""
    if not isinstance(text, str):
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _to_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_lng_lat(lng: Any, lat: Any) -> Optional[Tuple[float, float]]:
    """(lng, lat) as floats if both are numbers (or numeric strings) on the
    globe, else None."""
    lng, lat = _to_float(lng), _to_float(lat)
    if lng is None or lat is None or not (-180 <= lng <= 180 and -90 <= lat <= 90):
        return None
    return lng, lat
