"""
The pipeline in order. This is the only place the step order is defined;
the ingest service, the tests and the walkthrough notebook all use it.

  input   parse_pings         raw records (GeoJSON / GPX) -> time-sorted pings (time, lng, lat)
  step 0  remove_spikes       drop GPS spikes
  step 1  interval_motions    jump / still / walk / vehicle per interval (ping -> next ping)
  step 2  build_runs          intervals with the same motion -> runs, cleaned up
  step 3  find_mtr            rail test (distance to road), MTR time extension, interchanges
  step 4  classify_runs       decision tree: io type of each run (place labels first)
  step 5  build_points        stays -> 1 point, routes -> thinned points with heading / road
          split_by_hour       cut each point's time at HK clock hours

The io type is decided per run, before the hour split, so a stay crossing
an hour is judged by its whole duration.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from app.pipeline.classify import classify_runs
from app.pipeline.models import HourlySlice, Point, Track
from app.pipeline.motion import interval_motions
from app.pipeline.mtr import find_mtr
from app.pipeline.parsers import parse_pings
from app.pipeline.place_labels import PlaceLabel
from app.pipeline.points import build_points
from app.pipeline.roads import RoadIndex
from app.pipeline.runs import build_runs
from app.pipeline.spikes import remove_spikes
from app.pipeline.time_split import split_by_hour


@dataclass(frozen=True)
class PipelineResult:
    points: List[Point]
    slices: List[HourlySlice]
    # Counts after each step, for logging.
    pings: int
    pings_after_spikes: int
    runs: int
    # Time of the first and last parsed ping (None if there were none): the
    # span this batch covers, so its rows can replace older rows there.
    first_ping_utc: Optional[datetime] = None
    last_ping_utc: Optional[datetime] = None

    @property
    def hk_slices(self) -> List[HourlySlice]:
        """The slices to send to expo_calx, in order."""
        return [s for s in self.slices if s.point.in_hk]


def run_pipeline(
    records: List[Dict[str, Any]], roads: RoadIndex, place_labels: Sequence[PlaceLabel] = (),
) -> PipelineResult:
    """`roads` and `place_labels` are loaded by the caller, so the pipeline
    stays free of I/O."""
    pings = parse_pings(records)
    track = Track.from_pings(remove_spikes(pings))
    nearest_roads = roads.nearest(track.pings)

    runs = find_mtr(track, build_runs(track, interval_motions(track)), nearest_roads)
    classified = classify_runs(track, runs, place_labels)
    points = build_points(track, classified, nearest_roads)
    return PipelineResult(
        points=points,
        slices=split_by_hour(points),
        pings=len(pings),
        pings_after_spikes=len(track),
        runs=len(classified),
        first_ping_utc=pings[0].utc if pings else None,
        last_ping_utc=pings[-1].utc if pings else None,
    )
