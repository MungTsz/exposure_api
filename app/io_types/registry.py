"""
The list of io types and how a stay is assigned one.

A stay gets the first io type in IO_TYPES whose rule matches it, unless it
is part of a visit to a labelled place (see place_labels.py): then the
label's category wins, whatever the rules say - a label the user put on the
map is more reliable than the rules, so an Office circle at 02:00 is
Office, not Home.

A visit starts at a stay a label matches and takes in the consecutive stays
around it that match the same category, or would with the circles grown by
PLACE_LABEL_DRIFT_MARGIN_M. Indoor GPS drift splits a night at one place
into many short stays, each of which the rules alone would call Outdoor (or
Home, if it drifted out of the circle). A visit counts once it lasts
STAY_INDOOR_MINUTES or holds a stationary stay, so walking past an office
stays Outdoor; its stays become one stay with the label's category.

To add an io type (e.g. Bus):
  1. Add its NAME, API_LABEL and rule thresholds to app/config/io_types.py.
  2. Add rules/<name>.py defining an IoType with its `matches(stay)` rule
     (use app/etl/geo.py for any distance/box checks) and the right kind
     (stationary or moving).
  3. Add it to IO_TYPES below, before any type it should win over.
"""

from dataclasses import replace
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from app.config import io_types as cfg
from app.config.constants import IoSource
from app.etl.models import Stay
from app.io_types.base import IoKind, IoType
from app.io_types.place_labels import PlaceLabel, match_place_label
from app.io_types.rules.home import HOME
from app.io_types.rules.office import OFFICE
from app.io_types.rules.other_indoor import OTHER_INDOOR
from app.io_types.rules.outdoor import OUTDOOR
from app.io_types.rules.school import SCHOOL

# Priority order: a stay gets the first io type whose rule matches it.
# OFFICE and SCHOOL match nothing on their own (place labels only).
# OTHER_INDOOR matches every indoor stay, so HOME must come before it.
# OUTDOOR matches everything, so it must stay last.
IO_TYPES: List[IoType] = [
    HOME,
    OFFICE,
    SCHOOL,
    OTHER_INDOOR,
    OUTDOOR,
]

_BY_NAME: Dict[str, IoType] = {t.name: t for t in IO_TYPES}


def get(name: str) -> IoType:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise ValueError(f"unknown io type {name!r}; known: {sorted(_BY_NAME)}") from None


def classify(stay: Stay) -> IoType:
    for io_type in IO_TYPES:
        if io_type.matches(stay):
            return io_type
    raise ValueError("no io type matched the stay; the last entry in IO_TYPES must match everything")


def label_stays(stays: List[Stay], place_labels: Sequence[PlaceLabel] = ()) -> List[Stay]:
    """Give each stay its io type name, where it came from (io_source) and
    the position that type reports. Each visit to a labelled place comes
    back as one stay."""
    labelled = []
    for group, category in _visits(stays, place_labels):
        if category is None:
            (stay,) = group
            io_type, source = classify(stay), IoSource.INFERRED
        else:
            stay = Stay(start_utc=group[0].start_utc, end_utc=group[-1].end_utc,
                        pings=[p for s in group for p in s.pings])
            io_type, source = get(category), IoSource.LABEL
        lng, lat = io_type.position(stay)
        labelled.append(replace(stay, io=io_type.name, io_source=source.value, lng=lng, lat=lat))
    return labelled


def _visits(stays: List[Stay], labels: Sequence[PlaceLabel]) -> Iterator[Tuple[List[Stay], Optional[str]]]:
    """The stays in order, as (visit, category) for each visit to a labelled
    place and ([stay], None) for every other stay."""
    if not labels:
        for stay in stays:
            yield [stay], None
        return

    grown = [replace(label, radius_m=label.radius_m + cfg.PLACE_LABEL_DRIFT_MARGIN_M) for label in labels]
    strict = [match_place_label(s.pings, labels) for s in stays]
    near_cache: Dict[int, Optional[str]] = {}

    def near(k: int) -> Optional[str]:
        if k not in near_cache:
            near_cache[k] = match_place_label(stays[k].pings, grown)
        return near_cache[k]

    done = 0  # stays[:done] are yielded
    i = 0
    while i < len(stays):
        category = strict[i]
        if category is None:
            i += 1
            continue
        # stays[done:i] match no label, so any of them may join backwards.
        start = i
        while start > done and near(start - 1) == category:
            start -= 1
        end = i + 1
        while end < len(stays) and strict[end] in (None, category) and (
            strict[end] == category or near(end) == category
        ):
            end += 1

        for stay in stays[done:start]:
            yield [stay], None
        group = stays[start:end]
        if _is_visit(group):
            yield group, category
        else:
            for stay in group:
                yield [stay], None
        done = i = end
    for stay in stays[done:]:
        yield [stay], None


def _is_visit(group: List[Stay]) -> bool:
    duration = (group[-1].end_utc - group[0].start_utc).total_seconds()
    return duration >= cfg.STAY_INDOOR_MINUTES * 60 or any(classify(s).kind == IoKind.STATIONARY for s in group)
