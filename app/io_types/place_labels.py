"""
Place labels: circles (centre + radius) that users put on a map to say
"this place is a Home / Office / School / Other Indoor". They are shared by
everyone - a label describes the place, not the person who added it - so
every pid's stays are checked against every contributor's labels.

match_place_label picks the category for one stay:
  1. Candidates: labels whose circle holds at least
     PLACE_LABEL_MIN_PING_FRACTION of the stay's pings.
  2. Best circle: most of the stay's pings inside, then the smaller radius
     (the more specific place), then the smaller label_id.
  3. Crowd vote: candidates that are the same spot as the best circle
     (overlap ratio >= PLACE_LABEL_SAME_SPOT_MIN_OVERLAP) vote, one vote per
     distinct contributor per category. Most votes wins; a tie goes to the
     category with the better-ranked circle (step 2).
The result depends only on the labels, never on their order.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set

from app.config import io_types as cfg
from app.etl.geo import circle_overlap_ratio, distance_m, within_circle
from app.etl.models import Ping


@dataclass(frozen=True)
class PlaceLabel:
    label_id: str
    contributor_id: str
    category: str  # one of config.io_types.PLACE_LABEL_CATEGORIES
    lng: float
    lat: float
    radius_m: float


def match_place_label(pings: List[Ping], labels: Sequence[PlaceLabel]) -> Optional[str]:
    """The category the labels give a stay with these pings, or None if no
    label matches."""
    if not pings or not labels:
        return None

    # Every ping is within `spread` of the first one, so a circle farther
    # than radius + spread from it can't hold any ping.
    first = pings[0]
    spread = max(distance_m(first, p) for p in pings)
    min_inside = cfg.PLACE_LABEL_MIN_PING_FRACTION * len(pings)

    inside: Dict[str, int] = {}
    candidates: List[PlaceLabel] = []
    for label in labels:
        if distance_m(first, label) > label.radius_m + spread:
            continue
        count = sum(1 for p in pings if within_circle(label, p))
        if count >= min_inside:
            inside[label.label_id] = count
            candidates.append(label)
    if not candidates:
        return None

    ranked = sorted(candidates, key=lambda c: (-inside[c.label_id], c.radius_m, c.label_id))
    best = ranked[0]
    # Categories are inserted in rank order and max() keeps the first of
    # equal counts, so a tie goes to the category with the better circle.
    voters: Dict[str, Set[str]] = {}
    for c in ranked:
        if c is best or circle_overlap_ratio(best, c) >= cfg.PLACE_LABEL_SAME_SPOT_MIN_OVERLAP:
            voters.setdefault(c.category, set()).add(c.contributor_id)
    return max(voters, key=lambda category: len(voters[category]))
