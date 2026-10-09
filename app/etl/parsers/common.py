"""Helpers shared by every input-format parser."""

from datetime import datetime
from typing import Optional


def parse_iso_utc(text: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting a trailing 'Z' for UTC."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def clean_speed(speed: Optional[float]) -> Optional[float]:
    """Devices often report -1 (or another negative value) for "unknown"
    speed instead of leaving the field out - treat that as no reading."""
    if speed is None or speed < 0:
        return None
    return speed
