"""School: only assigned when a place label matches the stay (see
app/io_types/place_labels.py); it has no rule of its own."""

from app.config import io_types as cfg
from app.etl.models import Stay
from app.io_types.base import IoKind, IoType


def _matches(stay: Stay) -> bool:
    return False


SCHOOL = IoType(
    name=cfg.SCHOOL_NAME,
    api_label=cfg.SCHOOL_API_LABEL,
    kind=IoKind.STATIONARY,
    matches=_matches,
)
