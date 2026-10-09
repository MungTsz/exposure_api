"""Indoor: a stay long enough that the person was most likely inside."""

from app.config import io_types as cfg
from app.etl.models import Stay
from app.io_types.base import IoKind, IoType


def _matches(stay: Stay) -> bool:
    return stay.duration_minutes >= cfg.STAY_INDOOR_MINUTES


INDOOR = IoType(
    name=cfg.INDOOR_NAME,
    api_label=cfg.INDOOR_API_LABEL,
    kind=IoKind.STATIONARY,
    matches=_matches,
)
