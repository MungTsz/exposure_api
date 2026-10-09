"""Outdoor: the fallback for any stay no other io type claims."""

from app.config import io_types as cfg
from app.etl.models import Stay
from app.io_types.base import IoKind, IoType


def _matches(stay: Stay) -> bool:
    return True


OUTDOOR = IoType(
    name=cfg.OUTDOOR_NAME,
    api_label=cfg.OUTDOOR_API_LABEL,
    kind=IoKind.MOVING,
    matches=_matches,
)
