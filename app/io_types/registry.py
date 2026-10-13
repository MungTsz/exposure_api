"""
The list of io types and how a stay is assigned one.

A stay first gets the first io type in IO_TYPES whose rule matches it. If
that io type is stationary and a place label matches the stay (see
place_labels.py), the label's category replaces it: a label the user put
on the map is more reliable than the rules. Moving stays never use labels.

To add an io type (e.g. Bus):
  1. Add its NAME, API_LABEL and rule thresholds to app/config/io_types.py.
  2. Add rules/<name>.py defining an IoType with its `matches(stay)` rule
     (use app/etl/geo.py for any distance/box checks) and the right kind
     (stationary or moving).
  3. Add it to IO_TYPES below, before any type it should win over.
"""

from dataclasses import replace
from typing import Dict, List, Sequence

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
    the position that type reports."""
    labelled = []
    for stay in stays:
        io_type, source = classify(stay), IoSource.INFERRED
        if io_type.kind == IoKind.STATIONARY:
            category = match_place_label(stay.pings, place_labels)
            if category is not None:
                io_type, source = get(category), IoSource.LABEL
        lng, lat = io_type.position(stay)
        labelled.append(replace(stay, io=io_type.name, io_source=source.value, lng=lng, lat=lat))
    return labelled
