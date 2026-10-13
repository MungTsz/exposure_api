"""
The ETL pipeline in order. This is the only place the step order is
defined; the ingest service and the notebook both use it.

  1. parse_pings           raw records -> time-sorted pings
  2. remove_spike_outliers drop GPS spikes
  3. group_into_stays      pings -> stays (where and how long)
  4. label_stays           give each stay an io type (Home, Office, Outdoor, ...);
                           place labels first, then the io type rules
  5. simplify_routes       thin moving routes to ~one point per few hundred m
  6. split_by_hour         cut stays at HK-local clock hours
  7. mark_in_hk            flag slices outside Hong Kong (not sent to expo_calx)

Labelling (4) happens before the hour split (6) on purpose: a stay that
crosses an hour boundary is judged by its true total duration, not by
whichever partial slice lands in one hour.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from app.config.constants import IoSource
from app.etl.models import HourlySlice
from app.etl.parsers import parse_pings
from app.etl.region import mark_in_hk
from app.etl.routes import simplify_routes
from app.etl.spikes import remove_spike_outliers
from app.etl.stays import group_into_stays
from app.etl.time_split import split_by_hour
from app.io_types.place_labels import PlaceLabel
from app.io_types.registry import label_stays


@dataclass
class PipelineResult:
    slices: List[HourlySlice]
    # Counts after each step, for logging.
    pings: int
    pings_after_spikes: int
    stays: int
    stays_from_labels: int
    stays_after_routes: int
    # Time of the first and last parsed ping (None if there were none): the
    # span this batch covers, so its rows can replace older rows there.
    first_ping_utc: Optional[datetime] = None
    last_ping_utc: Optional[datetime] = None

    @property
    def hk_slices(self) -> List[HourlySlice]:
        """The slices to send to expo_calx, in order."""
        return [s for s in self.slices if s.in_hk]


def run_pipeline(records: List[Dict[str, Any]], place_labels: Sequence[PlaceLabel] = ()) -> PipelineResult:
    """`place_labels` are the stored place labels (loaded by the caller, so
    the pipeline stays free of I/O); with none, only the io type rules apply."""
    pings = parse_pings(records)
    cleaned = remove_spike_outliers(pings)
    stays = label_stays(group_into_stays(cleaned), place_labels)
    simplified = simplify_routes(stays)
    return PipelineResult(
        slices=mark_in_hk(split_by_hour(simplified)),
        pings=len(pings),
        pings_after_spikes=len(cleaned),
        stays=len(stays),
        stays_from_labels=sum(1 for s in stays if s.io_source == IoSource.LABEL.value),
        stays_after_routes=len(simplified),
        first_ping_utc=pings[0].utc if pings else None,
        last_ping_utc=pings[-1].utc if pings else None,
    )
