"""Groups the continuous ping stream into stays (where the person stayed, and for how long)."""

from typing import List

from app.config import pipeline as cfg
from app.etl.geo import within_degree_box
from app.etl.models import Ping, Stay


def _is_slow_enough(p: Ping) -> bool:
    return p.speed_mps is None or p.speed_mps < cfg.SPEED_THRESHOLD_MPS


def group_into_stays(pings: List[Ping]) -> List[Stay]:
    """Split the full, continuous ping stream into stays.

    A stay is a maximal sequence of consecutive pings that all stay within
    STAY_DEGREE_THRESHOLD (a flat lat/lng box, not haversine) of the stay's
    first ping AND report a speed below SPEED_THRESHOLD_MPS (pings with no
    speed reading are judged on distance alone). A stay ends when the next
    ping arrives, so its duration covers the time up to that ping. The very
    last stay has no further ping, so it ends at its own last ping.

    Uses a fixed reference point (the first ping) per stay, so a slowly
    drifting walk can split into more stays than ideal (known limitation).

    Stays are not labelled here - app.io_types.registry.label_stays does
    that, using each stay's true total duration (before any hour split).
    """
    if not pings:
        return []

    stays: List[Stay] = []
    current = [pings[0]]
    for p in pings[1:]:
        if within_degree_box(current[0], p, cfg.STAY_DEGREE_THRESHOLD) and _is_slow_enough(p):
            current.append(p)
        else:
            stays.append(Stay(start_utc=current[0].utc, end_utc=p.utc, pings=current))
            current = [p]
    stays.append(Stay(start_utc=current[0].utc, end_utc=pings[-1].utc, pings=current))
    return stays
