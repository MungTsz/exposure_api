"""
Step 3: find the MTR rides among the runs.

  3a  not underground: a jump run with more than MTR_MAX_JUMPS jumps is a
      moving phone that logs rarely -> vehicle (so 3b decides between MTR
      and Vehicle; Vehicle when in doubt); a jump run covering less than
      MTR_MIN_DEG is shorter than any MTR ride -> walk (Outdoor when in
      doubt)
  3b  rail test: a vehicle run inside Hong Kong whose pings are at a median
      of >= RAIL_MIN_ROAD_M from the nearest road centreline is
      above-ground MTR (a car drives on the centreline, a train beside the
      roads) -> motion RAIL
  3c  time extension: a jump faster than MTR_MAX_AVG_V door to door started
      earlier - the pings before it were stale - so the still intervals
      right before it join the ride, newest first, until its speed is
      plausible
  3d  interchange: a still / walk run of <= INTERCHANGE_MAX_S between two
      MTR runs (jump / rail) is changing lines inside a station
      -> motion INTERCHANGE

Underground rides are the jumps themselves (GPS dark between two stations).
"""

from statistics import median
from typing import List, Sequence

from app.config import pipeline as cfg
from app.pipeline.geo import deg_between
from app.pipeline.models import Motion, Run, Track
from app.pipeline.region import is_in_hk
from app.pipeline.roads import NearestRoad
from app.pipeline.runs import centre, to_motions, to_runs

_MTR_MOTIONS = (Motion.JUMP, Motion.RAIL)


def find_mtr(track: Track, runs: Sequence[Run], roads: Sequence[NearestRoad]) -> List[Run]:
    """3a-3d on the runs of build_runs; `roads` holds each ping's nearest road."""
    motions = to_motions(runs)
    _not_underground(track, motions)
    _rail_test(track, motions, roads)
    _extend_jumps(track, motions)
    _interchanges(track, motions)
    return to_runs(motions)


def _not_underground(track: Track, motions: List[Motion]) -> None:
    """3a."""
    for run in to_runs(motions):
        if run.motion != Motion.JUMP:
            continue
        jumps = sum(1 for i in range(run.first, run.last + 1) if track.dd_deg[i] >= cfg.JUMP_DEG)
        if jumps > cfg.MTR_MAX_JUMPS:
            motions[run.first:run.last + 1] = [Motion.VEHICLE] * (run.last - run.first + 1)
        elif deg_between(track.pings[run.first_ping], track.pings[run.last]) < cfg.MTR_MIN_DEG:
            motions[run.first:run.last + 1] = [Motion.WALK] * (run.last - run.first + 1)


def _rail_test(track: Track, motions: List[Motion], roads: Sequence[NearestRoad]) -> None:
    """3b. The road data covers Hong Kong only, so outside HK a vehicle run
    stays a vehicle run."""
    for run in to_runs(motions):
        if run.motion != Motion.VEHICLE or not is_in_hk(*centre(track, run)):
            continue
        if median(r.distance_m for r in roads[run.first_ping:run.last + 1]) >= cfg.RAIL_MIN_ROAD_M:
            motions[run.first:run.last + 1] = [Motion.RAIL] * (run.last - run.first + 1)


def _extend_jumps(track: Track, motions: List[Motion]) -> None:
    """3c."""
    for run in to_runs(motions):
        if run.motion != Motion.JUMP:
            continue
        needed_s = deg_between(track.pings[run.first_ping], track.pings[run.last]) / cfg.MTR_MAX_AVG_V
        took_s, i = run.seconds(track), run.first - 1
        while took_s < needed_s and i >= 1 and motions[i] == Motion.STILL:
            motions[i] = Motion.JUMP
            took_s += track.dt_s[i]
            i -= 1


def _interchanges(track: Track, motions: List[Motion]) -> None:
    """3d."""
    runs = to_runs(motions)
    for n in range(1, len(runs) - 1):
        prev, run, nxt = runs[n - 1], runs[n], runs[n + 1]
        if (run.motion in (Motion.STILL, Motion.WALK) and run.seconds(track) <= cfg.INTERCHANGE_MAX_S
                and prev.motion in _MTR_MOTIONS and nxt.motion in _MTR_MOTIONS):
            motions[run.first:run.last + 1] = [Motion.INTERCHANGE] * (run.last - run.first + 1)
