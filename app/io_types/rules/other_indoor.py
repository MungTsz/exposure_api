"""Other Indoor: an indoor stay that isn't Home. Must come after HOME in
IO_TYPES, since its rule only checks the duration."""

from app.config import io_types as cfg
from app.etl.models import Stay
from app.io_types.base import IoKind, IoType


def _matches(stay: Stay) -> bool:
    return stay.duration_minutes >= cfg.STAY_INDOOR_MINUTES


OTHER_INDOOR = IoType(
    name=cfg.OTHER_INDOOR_NAME,
    api_label=cfg.OTHER_INDOOR_API_LABEL,
    kind=IoKind.STATIONARY,
    matches=_matches,
)
