"""Cuts stays at HK-local clock-hour boundaries."""

from datetime import datetime, timedelta, timezone
from typing import List

from app.config.constants import HK_OFFSET
from app.etl.models import HourlySlice, Stay


def to_hk(dt_utc: datetime) -> datetime:
    """Returns a naive datetime whose fields read as HK-local wall-clock time."""
    return (dt_utc.astimezone(timezone.utc) + HK_OFFSET).replace(tzinfo=None)


def split_by_hour(stays: List[Stay]) -> List[HourlySlice]:
    """Slice each labelled stay at HK-local clock-hour boundaries.

    expo_calx scores each row against that hour's AQHI reading, so a stay
    spanning e.g. 15:30-18:00 must become three rows (15:30, 16:00, 17:00),
    not one. Each slice keeps the stay's io label - already decided from the
    stay's true, uncut duration - and gets its own prorated delta_t_hours.
    Zero-length slices are never produced.
    """
    slices: List[HourlySlice] = []
    for stay in stays:
        start_hk = to_hk(stay.start_utc)
        end_hk = to_hk(stay.end_utc)
        hour_cursor = start_hk.replace(minute=0, second=0, microsecond=0)
        while hour_cursor < end_hk:
            next_hour = hour_cursor + timedelta(hours=1)
            slice_start = max(start_hk, hour_cursor)
            slice_end = min(end_hk, next_hour)
            duration_hours = (slice_end - slice_start).total_seconds() / 3600.0
            if duration_hours > 0:
                slices.append(
                    HourlySlice(
                        hour_start_hk=hour_cursor,
                        start_time_hk=slice_start,
                        io=stay.io,
                        lng=stay.lng,
                        lat=stay.lat,
                        delta_t_hours=duration_hours,
                    )
                )
            hour_cursor = next_hour
    return slices
