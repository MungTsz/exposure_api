"""What an io type is. Concrete io types live in rules/, one module each."""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Tuple

from app.etl.models import Stay


class IoKind(str, Enum):
    """Decides how the pipeline treats a stay of this io type, so no
    pipeline code ever checks for a specific io name."""

    # Not moving (Indoor, Office, ...): the stay is placed at its first
    # ping, since GPS drift while staying put shouldn't read as distinct
    # locations.
    STATIONARY = "stationary"
    # Moving (Outdoor, Bus, ...): the stay is placed at the average of its
    # pings, and consecutive stays of the same io type form a route that
    # app.etl.routes thins out.
    MOVING = "moving"


@dataclass(frozen=True)
class IoType:
    name: str        # stored in the DB `io` column and returned to the app
    api_label: str   # sent to expo_calx as {"IO": api_label}
    kind: IoKind
    matches: Callable[[Stay], bool]  # True if a stay belongs to this io type

    def position(self, stay: Stay) -> Tuple[float, float]:
        """(lng, lat) to report for a stay of this io type."""
        pings = stay.pings
        if self.kind == IoKind.STATIONARY:
            return pings[0].lng, pings[0].lat
        return sum(p.lng for p in pings) / len(pings), sum(p.lat for p in pings) / len(pings)
