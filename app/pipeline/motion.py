"""
Step 1: the motion of each interval (jump / still / walk / vehicle).

A ping's own speed is useless indoors: GPS jitter moves a still phone by
10-20 m per ping whatever the time between pings, so with 1 s between pings
it looks like 15 m/s. Instead each ping gets v_net: the net displacement
between the first and last ping within V_NET_WINDOW_S around it, minus the
jitter floor NOISE_DEG, over their time span. Jitter cancels out; real
movement adds up.
"""

from bisect import bisect_left, bisect_right
from typing import List, Optional

from app.config import pipeline as cfg
from app.pipeline.geo import deg_between
from app.pipeline.models import Motion, Track


def v_net(track: Track, i: int) -> Optional[float]:
    """Net speed around ping i in deg/s, or None if no other ping is within
    the window (or they all share one timestamp)."""
    t = track.elapsed_s
    j = bisect_left(t, t[i] - cfg.V_NET_WINDOW_S / 2)
    k = bisect_right(t, t[i] + cfg.V_NET_WINDOW_S / 2) - 1
    span = t[k] - t[j]
    if span <= 0:
        return None
    return max(deg_between(track.pings[j], track.pings[k]) - cfg.NOISE_DEG, 0.0) / span


def interval_motions(track: Track) -> List[Motion]:
    """One Motion per interval (interval i = ping i-1 -> ping i). Interval 0
    has no duration and copies interval 1. First match wins:

      jump     moved >= JUMP_DEG (~300 m): travelled while GPS was dark
      still    lasted >= GAP_S (no pings in between), or v_net < STILL_MAX_V
      walk     v_net < WALK_MAX_V
      vehicle  otherwise

    The interval's v_net is the mean of its two end pings' v_net.
    """
    n = len(track)
    if n < 2:
        return [Motion.STILL] * n
    speeds = [v_net(track, i) for i in range(n)]
    motions = [Motion.STILL] * n
    for i in range(1, n):
        known = [v for v in (speeds[i - 1], speeds[i]) if v is not None]
        v = sum(known) / len(known) if known else None
        if track.dd_deg[i] >= cfg.JUMP_DEG:
            motions[i] = Motion.JUMP
        elif track.dt_s[i] >= cfg.GAP_S or v is None or v < cfg.STILL_MAX_V:
            motions[i] = Motion.STILL
        elif v < cfg.WALK_MAX_V:
            motions[i] = Motion.WALK
        else:
            motions[i] = Motion.VEHICLE
    motions[0] = motions[1]
    return motions
