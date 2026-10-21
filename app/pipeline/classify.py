"""
Step 4: the decision tree that gives each run its io type.

  run
  ├─ jump ............................................. MTR      (underground)
  ├─ rail (vehicle far from roads, step 3) ............ MTR      (above ground)
  ├─ interchange (step 3) ............................. MTR      (changing lines)
  ├─ vehicle .......................................... Vehicle  (taxi, bus, minibus, car, ...)
  ├─ walk ............................................. Outdoor
  └─ still (a stay)
      ├─ inside a place label ......................... the label's category
      ├─ >= INDOOR_MIN_S and overlaps HOME_HOURS ...... Home     (night stay)
      ├─ within HOME_RADIUS_DEG of a night stay ....... Home
      ├─ < INDOOR_MIN_S ............................... Outdoor  (short stop, e.g. waiting)
      └─ otherwise .................................... Other Indoor

Place labels come first, even at night: a night at a labelled office is
Office. Office and School are never guessed: without a label they are
Other Indoor. A night stay is only a Home anchor if no label says it is
something else.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import List, Optional, Sequence, Tuple

from app.config import io_types as cfg
from app.config.constants import IoSource, Reason
from app.pipeline.models import ClassifiedRun, Motion, Run, Track
from app.pipeline.place_labels import PlaceLabel, grow, match_place_label
from app.pipeline.runs import centre
from app.pipeline.time_split import to_hk

_MOVING = {
    Motion.JUMP: (cfg.MTR, Reason.JUMP),
    Motion.RAIL: (cfg.MTR, Reason.RAIL),
    Motion.INTERCHANGE: (cfg.MTR, Reason.INTERCHANGE),
    Motion.VEHICLE: (cfg.VEHICLE, Reason.VEHICLE),
    Motion.WALK: (cfg.OUTDOOR, Reason.WALK),
}


@dataclass(frozen=True)
class _Stay:
    """What the tree needs to know about a still run."""

    run: Run
    label: Optional[str]          # category of the matching place label
    long: bool                    # lasted >= INDOOR_MIN_S
    night: bool                   # overlaps HOME_HOURS of some day
    centre: Tuple[float, float]   # (lng, lat)


def classify_runs(track: Track, runs: Sequence[Run], place_labels: Sequence[PlaceLabel] = ()) -> List[ClassifiedRun]:
    labels = grow(place_labels)
    stays = {n: _describe(track, run, labels) for n, run in enumerate(runs) if run.motion == Motion.STILL}
    night_homes = [
        s.centre for s in stays.values() if s.night and s.long and s.label in (None, cfg.HOME)
    ]

    classified = []
    for n, run in enumerate(runs):
        if run.motion in _MOVING:
            io, reason = _MOVING[run.motion]
            classified.append(ClassifiedRun(run, io, IoSource.INFERRED, reason))
        else:
            classified.append(_classify_stay(stays[n], night_homes))
    return classified


def _describe(track: Track, run: Run, labels: Sequence[PlaceLabel]) -> _Stay:
    pings = run.pings(track)
    return _Stay(
        run=run,
        label=match_place_label(pings, labels),
        long=run.seconds(track) >= cfg.INDOOR_MIN_S,
        night=_overlaps_home_hours(to_hk(pings[0].utc), to_hk(pings[-1].utc)),
        centre=centre(track, run),
    )


def _classify_stay(stay: _Stay, night_homes: Sequence[Tuple[float, float]]) -> ClassifiedRun:
    if stay.label is not None:
        return ClassifiedRun(stay.run, stay.label, IoSource.LABEL, Reason.PLACE_LABEL)
    if stay.night and stay.long:
        return ClassifiedRun(stay.run, cfg.HOME, IoSource.INFERRED, Reason.NIGHT_STAY)
    lng, lat = stay.centre
    if any(abs(lng - h_lng) + abs(lat - h_lat) <= cfg.HOME_RADIUS_DEG for h_lng, h_lat in night_homes):
        return ClassifiedRun(stay.run, cfg.HOME, IoSource.INFERRED, Reason.NEAR_NIGHT_STAY)
    if not stay.long:
        return ClassifiedRun(stay.run, cfg.OUTDOOR, IoSource.INFERRED, Reason.SHORT_STOP)
    return ClassifiedRun(stay.run, cfg.OTHER_INDOOR, IoSource.INFERRED, Reason.LONG_STAY)


def _overlaps_home_hours(start_hk: datetime, end_hk: datetime) -> bool:
    """True if [start_hk, end_hk] overlaps [HOME_HOURS) on any of its days."""
    first_hour, last_hour = cfg.HOME_HOURS
    day: date = start_hk.date()
    while day <= end_hk.date():
        window_start = datetime.combine(day, time(first_hour))
        window_end = datetime.combine(day, time()) + timedelta(hours=last_hour)
        if start_hk < window_end and end_hk > window_start:
            return True
        day += timedelta(days=1)
    return False
