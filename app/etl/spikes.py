"""Removes GPS spikes: single points where the path jumps away and straight back."""

from typing import List

from app.config import pipeline as cfg
from app.etl.geo import angle_at_deg
from app.etl.models import Ping


def remove_spike_outliers(pings: List[Ping]) -> List[Ping]:
    """Delete point B from any triple A,B,C where the path sharply doubles back
    (angle at B < SPIKE_ANGLE_DEG) within SPIKE_TIME_WINDOW_SECONDS.
    Re-scans after each deletion since removing B changes the next triple to check."""
    cleaned = list(pings)
    changed = True
    while changed:
        changed = False
        i = 1
        while i < len(cleaned) - 1:
            a, b, c = cleaned[i - 1], cleaned[i], cleaned[i + 1]
            span = (c.utc - a.utc).total_seconds()
            if span <= cfg.SPIKE_TIME_WINDOW_SECONDS and angle_at_deg(a, b, c) < cfg.SPIKE_ANGLE_DEG:
                del cleaned[i]
                changed = True
                continue
            i += 1
    return cleaned
