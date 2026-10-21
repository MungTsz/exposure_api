"""Step 0: removes GPS spikes - single pings where the position jumps far
away and straight back."""

from typing import List, Sequence

from app.config import pipeline as cfg
from app.pipeline.geo import deg_between
from app.pipeline.models import Ping


def remove_spikes(pings: Sequence[Ping]) -> List[Ping]:
    """Drop ping B of any consecutive A, B, C where A->B and B->C are both at
    least SPIKE_MIN_DEG and C is back within SPIKE_RETURN_RATIO of the
    shorter leg from A. Re-checks the previous triple after each drop,
    since removing B changes it."""
    kept = list(pings)
    i = 1
    while i < len(kept) - 1:
        a, b, c = kept[i - 1], kept[i], kept[i + 1]
        shorter_leg = min(deg_between(a, b), deg_between(b, c))
        if shorter_leg >= cfg.SPIKE_MIN_DEG and deg_between(a, c) <= cfg.SPIKE_RETURN_RATIO * shorter_leg:
            del kept[i]
            i = max(i - 1, 1)
        else:
            i += 1
    return kept
