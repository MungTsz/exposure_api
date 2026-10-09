"""Data shapes passed between ETL steps."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Ping:
    """One GPS point."""

    utc: datetime
    lng: float
    lat: float
    speed_mps: Optional[float] = None  # device-reported speed, m/s; None if not provided


@dataclass
class Stay:
    """A run of consecutive pings that stay inside a small box and move
    slowly - i.e. how long the person stayed at one place. While moving,
    each ping usually becomes its own very short stay.

    `io`, `lng` and `lat` are empty until app.io_types.registry.label_stays
    fills them in.
    """

    start_utc: datetime
    end_utc: datetime
    pings: List[Ping] = field(default_factory=list)
    io: Optional[str] = None
    lng: Optional[float] = None
    lat: Optional[float] = None

    @property
    def duration_seconds(self) -> float:
        return (self.end_utc - self.start_utc).total_seconds()

    @property
    def duration_minutes(self) -> float:
        return max(0.0, self.duration_seconds / 60.0)


@dataclass
class HourlySlice:
    """The part of one stay that falls inside one HK-local clock hour."""

    hour_start_hk: datetime   # which hour bucket this slice belongs to (for the chart/DB)
    start_time_hk: datetime   # this slice's own precise start time (for the API row's `t`)
    io: str
    lng: float
    lat: float
    delta_t_hours: float
    # Inside Hong Kong? Only HK slices are sent to expo_calx. Set by
    # app.etl.region.mark_in_hk.
    in_hk: bool = True
