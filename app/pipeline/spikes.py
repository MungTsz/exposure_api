"""Step 0: removes GPS spikes - single pings where the position jumps far
away and straight back, or that only an impossible speed could reach."""

import math
from typing import List, Sequence

from app.config import pipeline as cfg
from app.pipeline.geo import deg_between
from app.pipeline.models import Ping


def remove_spikes(pings: Sequence[Ping]) -> List[Ping]:
    """Drop ping B of any consecutive A, B, C that _is_spike. Pings repeating
    B's exact position right after it count as B and go with it, if they
    span at most SPIKE_MAX_S. Re-checks the previous triple after each drop,
    since removing B changes it."""
    kept = list(pings)
    i = 1
    while i < len(kept) - 1:
        j = i
        while j + 1 < len(kept) and (kept[j + 1].lng, kept[j + 1].lat) == (kept[i].lng, kept[i].lat):
            j += 1
        if (j + 1 < len(kept) and (kept[j].utc - kept[i].utc).total_seconds() <= cfg.SPIKE_MAX_S
                and _is_spike(kept[i - 1], kept[i], kept[j], kept[j + 1])):
            del kept[i:j + 1]
            i = max(i - 1, 1)
        else:
            i += 1
    return kept


def _is_spike(a: Ping, b: Ping, b_last: Ping, c: Ping) -> bool:
    """A->B and B->C are both at least SPIKE_MIN_DEG, and C is back within
    SPIKE_RETURN_RATIO of the shorter leg from A, or B takes more than
    SPIKE_MAX_V to reach or leave while A->C does not. `b_last` is the last
    ping repeating B's position (B itself if none)."""
    ab, bc, ac = deg_between(a, b), deg_between(b, c), deg_between(a, c)
    shorter_leg = min(ab, bc)
    if shorter_leg < cfg.SPIKE_MIN_DEG:
        return False
    if ac <= cfg.SPIKE_RETURN_RATIO * shorter_leg:
        return True
    return max(_speed(ab, a, b), _speed(bc, b_last, c)) > cfg.SPIKE_MAX_V >= _speed(ac, a, c)


def _speed(deg: float, start: Ping, end: Ping) -> float:
    """deg/s from start to end; infinite for a move in no time."""
    seconds = (end.utc - start.utc).total_seconds()
    if seconds > 0:
        return deg / seconds
    return math.inf if deg > 0 else 0.0
