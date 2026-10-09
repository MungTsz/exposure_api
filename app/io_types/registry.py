"""
The list of io types and how a stay is assigned one.

To add an io type (e.g. Office, Bus):
  1. Add its NAME, API_LABEL and rule thresholds to app/config/io_types.py.
  2. Add rules/<name>.py defining an IoType with its `matches(stay)` rule
     (use app/etl/geo.py for any distance/box checks) and the right kind
     (stationary or moving).
  3. Add it to IO_TYPES below, before any type it should win over.
"""

from dataclasses import replace
from typing import Dict, List

from app.etl.models import Stay
from app.io_types.base import IoType
from app.io_types.rules.home import HOME
from app.io_types.rules.other_indoor import OTHER_INDOOR
from app.io_types.rules.outdoor import OUTDOOR

# Priority order: a stay gets the first io type whose rule matches it.
# OTHER_INDOOR matches every indoor stay, so HOME must come before it.
# OUTDOOR matches everything, so it must stay last.
IO_TYPES: List[IoType] = [
    HOME,
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


def label_stays(stays: List[Stay]) -> List[Stay]:
    """Give each stay its io type name and the position that type reports."""
    labelled = []
    for stay in stays:
        io_type = classify(stay)
        lng, lat = io_type.position(stay)
        labelled.append(replace(stay, io=io_type.name, lng=lng, lat=lat))
    return labelled
