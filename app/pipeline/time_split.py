"""Step 5b: cuts each point's time at HK-local clock-hour boundaries."""

from datetime import datetime, timedelta, timezone
from typing import List, Sequence

from app.config.constants import HK_OFFSET
from app.pipeline.models import HourlySlice, Point


def to_hk(dt_utc: datetime) -> datetime:
    """A naive datetime whose fields read as HK-local wall-clock time."""
    return (dt_utc.astimezone(timezone.utc) + HK_OFFSET).replace(tzinfo=None)


def split_by_hour(points: Sequence[Point]) -> List[HourlySlice]:
    """Slice each point's time [start, end) at HK-local clock hours.

    expo_calx scores each row against that hour's AQHI reading, so a stay
    from 15:30 to 18:00 must become three rows (15:30, 16:00, 17:00), not
    one. Each slice keeps its point (io, position, ...) - already decided
    from the whole run - and gets its own share of the time. Zero-length
    slices are never produced, so total time is kept exactly.
    """
    slices: List[HourlySlice] = []
    for point in points:
        start_hk, end_hk = to_hk(point.start_utc), to_hk(point.end_utc)
        hour = start_hk.replace(minute=0, second=0, microsecond=0)
        while hour < end_hk:
            next_hour = hour + timedelta(hours=1)
            slice_start, slice_end = max(start_hk, hour), min(end_hk, next_hour)
            if slice_end > slice_start:
                slices.append(HourlySlice(
                    point=point,
                    hour_start_hk=hour,
                    start_time_hk=slice_start,
                    delta_t_hours=(slice_end - slice_start).total_seconds() / 3600.0,
                ))
            hour = next_hour
    return slices
