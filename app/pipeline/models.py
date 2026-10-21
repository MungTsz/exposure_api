"""Data passed between pipeline steps (app/pipeline/run.py has the order)."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Sequence, Tuple

from app.config.constants import IoSource, PointKind, Reason


@dataclass(frozen=True)
class Ping:
    """One GPS point. Only time and position are read from any input."""

    utc: datetime
    lng: float
    lat: float


class Motion(str, Enum):
    """How the person moved during an interval, and later during a run."""

    JUMP = "jump"        # moved >= JUMP_DEG in one interval: travelled while GPS was dark
    STILL = "still"
    WALK = "walk"
    VEHICLE = "vehicle"
    # Set by app/pipeline/mtr.py only:
    RAIL = "rail"                 # a vehicle run that kept away from every road
    INTERCHANGE = "interchange"   # a short stop between two MTR runs


# Runs that become one point at the median of their pings; every other run
# is a route that keeps its (thinned) pings.
STAY_MOTIONS = (Motion.STILL, Motion.INTERCHANGE)


@dataclass(frozen=True)
class Track:
    """The day's pings, time-sorted with spikes removed, plus each
    interval's duration and distance.

    Interval i is the time from ping i-1 to ping i, so anything indexed by
    interval has one entry per ping. Interval 0 is empty (0 s, 0 deg).
    """

    pings: Tuple[Ping, ...]
    elapsed_s: Tuple[float, ...]  # seconds since the first ping
    dt_s: Tuple[float, ...]       # interval duration, seconds
    dd_deg: Tuple[float, ...]     # interval distance, |dlat| + |dlng| degrees

    @classmethod
    def from_pings(cls, pings: Sequence[Ping]) -> "Track":
        pings = tuple(pings)
        t0 = pings[0].utc if pings else None
        elapsed = tuple((p.utc - t0).total_seconds() for p in pings)
        dt = tuple(0.0 if i == 0 else elapsed[i] - elapsed[i - 1] for i in range(len(pings)))
        dd = tuple(
            0.0 if i == 0 else abs(pings[i].lat - pings[i - 1].lat) + abs(pings[i].lng - pings[i - 1].lng)
            for i in range(len(pings))
        )
        return cls(pings=pings, elapsed_s=elapsed, dt_s=dt, dd_deg=dd)

    def __len__(self) -> int:
        return len(self.pings)


@dataclass(frozen=True)
class Run:
    """Consecutive intervals [first, last] with the same motion. Its pings are
    first_ping..last: a run starts at the ping before its first interval."""

    motion: Motion
    first: int
    last: int

    @property
    def first_ping(self) -> int:
        return max(self.first - 1, 0)

    def seconds(self, track: Track) -> float:
        """Total duration of the run's intervals."""
        return track.elapsed_s[self.last] - track.elapsed_s[self.first_ping]

    def pings(self, track: Track) -> Tuple[Ping, ...]:
        return track.pings[self.first_ping:self.last + 1]


@dataclass(frozen=True)
class ClassifiedRun:
    """A run with the io type the decision tree gave it."""

    run: Run
    io: str
    io_source: IoSource
    reason: Reason


@dataclass(frozen=True)
class Point:
    """One point the app draws: a whole stay, or one kept ping of a route. It
    stands for the time [start_utc, end_utc)."""

    kind: PointKind
    io: str
    io_source: IoSource
    reason: Reason
    start_utc: datetime
    end_utc: datetime
    lng: float
    lat: float
    in_hk: bool
    heading_deg: Optional[float] = None  # route points: towards the next point, 0 = north, clockwise
    compass: Optional[str] = None        # heading_deg as N / NE / E / ...
    road_name: Optional[str] = None      # Vehicle points: the road it is on


@dataclass(frozen=True)
class HourlySlice:
    """The part of one point's time inside one HK-local clock hour: one DB
    row, and one expo_calx row if the point is inside Hong Kong."""

    point: Point
    hour_start_hk: datetime   # the hour bucket (for the chart / DB), HK local, naive
    start_time_hk: datetime   # this slice's own start (the API row's `t`), HK local, naive
    delta_t_hours: float
