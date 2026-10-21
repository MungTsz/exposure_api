"""
Step 2: consecutive intervals with the same motion form a run; clean up
what GPS noise left behind.

Repeated until nothing changes, first match wins each round:
  2a  a still / walk run of <= STOP_MAX_S between two vehicle / jump runs is
      part of that trip (traffic light, bus stop)
  2b  the shortest GPS blip - a vehicle run < MIN_VEHICLE_RUN_S or a walk run
      < MIN_WALK_RUN_S - joins a neighbour: a jump next to a vehicle blip,
      else the longer neighbour
  2c  still + walk of <= SAME_PLACE_MAX_WALK_S + still, with the two still
      runs' centres <= SAME_PLACE_DEG apart, is one stay (walking around a
      mall)
Then once:
  2d  a jump next to a vehicle run is GPS loss during that road trip
"""

from statistics import median
from typing import List, Optional, Sequence, Tuple

from app.config import pipeline as cfg
from app.pipeline.models import Motion, Run, Track

_MIN_RUN_S = {Motion.VEHICLE: cfg.MIN_VEHICLE_RUN_S, Motion.WALK: cfg.MIN_WALK_RUN_S}


def to_runs(motions: Sequence[Motion]) -> List[Run]:
    """Consecutive intervals with the same motion -> runs."""
    runs: List[Run] = []
    for i, motion in enumerate(motions):
        if runs and runs[-1].motion == motion:
            runs[-1] = Run(motion, runs[-1].first, i)
        else:
            runs.append(Run(motion, i, i))
    return runs


def to_motions(runs: Sequence[Run]) -> List[Motion]:
    """Runs -> one motion per interval (the inverse of to_runs)."""
    return [run.motion for run in runs for _ in range(run.first, run.last + 1)]


def centre(track: Track, run: Run) -> Tuple[float, float]:
    """(lng, lat): the median of the run's pings."""
    pings = run.pings(track)
    return median(p.lng for p in pings), median(p.lat for p in pings)


def build_runs(track: Track, motions: Sequence[Motion]) -> List[Run]:
    """Runs of the interval motions, cleaned up (2a-2d above)."""
    runs = to_runs(motions)
    while _join_stop(track, runs) or _join_blip(track, runs) or _merge_same_place(track, runs):
        pass
    return _gps_loss_in_trip(runs)


def _neighbours(runs: List[Run], n: int) -> Tuple[Optional[Run], Optional[Run]]:
    return (runs[n - 1] if n > 0 else None), (runs[n + 1] if n + 1 < len(runs) else None)


def _relabel(runs: List[Run], n: int, motion: Motion) -> None:
    """Give run n a new motion and merge it with same-motion neighbours."""
    runs[n] = Run(motion, runs[n].first, runs[n].last)
    if n + 1 < len(runs) and runs[n + 1].motion == motion:
        runs[n] = Run(motion, runs[n].first, runs[n + 1].last)
        del runs[n + 1]
    if n > 0 and runs[n - 1].motion == motion:
        runs[n - 1] = Run(motion, runs[n - 1].first, runs[n].last)
        del runs[n]


def _join_stop(track: Track, runs: List[Run]) -> bool:
    """2a. Returns True if it changed something."""
    fast = {Motion.VEHICLE, Motion.JUMP}
    for n, run in enumerate(runs):
        prev, nxt = _neighbours(runs, n)
        if (run.motion in (Motion.STILL, Motion.WALK) and prev and nxt
                and run.seconds(track) <= cfg.STOP_MAX_S and {prev.motion, nxt.motion} <= fast):
            trip = Motion.VEHICLE if Motion.VEHICLE in (prev.motion, nxt.motion) else Motion.JUMP
            _relabel(runs, n, trip)
            return True
    return False


def _join_blip(track: Track, runs: List[Run]) -> bool:
    """2b. Returns True if it changed something."""
    blips = sorted(
        (run.seconds(track), n) for n, run in enumerate(runs)
        if run.motion in _MIN_RUN_S and run.seconds(track) < _MIN_RUN_S[run.motion]
    )
    for _, n in blips:
        neighbours = [r for r in _neighbours(runs, n) if r]
        if not neighbours:
            continue
        jumps = [r for r in neighbours if r.motion == Motion.JUMP]
        if runs[n].motion == Motion.VEHICLE and jumps:
            target = jumps[0]
        else:
            target = max(neighbours, key=lambda r: r.seconds(track))  # the earlier one on a tie
        _relabel(runs, n, target.motion)
        return True
    return False


def _merge_same_place(track: Track, runs: List[Run]) -> bool:
    """2c. Returns True if it changed something."""
    for n in range(1, len(runs) - 1):
        a, walk, b = runs[n - 1], runs[n], runs[n + 1]
        if (a.motion == b.motion == Motion.STILL and walk.motion == Motion.WALK
                and walk.seconds(track) <= cfg.SAME_PLACE_MAX_WALK_S):
            (a_lng, a_lat), (b_lng, b_lat) = centre(track, a), centre(track, b)
            if abs(a_lng - b_lng) + abs(a_lat - b_lat) <= cfg.SAME_PLACE_DEG:
                _relabel(runs, n, Motion.STILL)
                return True
    return False


def _gps_loss_in_trip(runs: List[Run]) -> List[Run]:
    """2d. A jump next to a vehicle run becomes vehicle."""
    motions = []
    for n, run in enumerate(runs):
        near_vehicle = any(r is not None and r.motion == Motion.VEHICLE for r in _neighbours(runs, n))
        motions.append(Motion.VEHICLE if run.motion == Motion.JUMP and near_vehicle else run.motion)
    return to_runs(to_motions([Run(m, r.first, r.last) for m, r in zip(motions, runs)]))
