"""Home: an indoor stay that starts in the early-morning HK hours."""

from app.config import io_types as cfg
from app.etl.models import Stay
from app.etl.time_split import to_hk
from app.io_types.base import IoKind, IoType


def _matches(stay: Stay) -> bool:
    if stay.duration_minutes < cfg.STAY_INDOOR_MINUTES:
        return False
    return cfg.HOME_START_HOUR_HK <= to_hk(stay.start_utc).hour < cfg.HOME_END_HOUR_HK


HOME = IoType(
    name=cfg.HOME_NAME,
    api_label=cfg.HOME_API_LABEL,
    kind=IoKind.STATIONARY,
    matches=_matches,
)
