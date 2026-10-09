"""Builds expo_calx request rows from hourly slices."""

from typing import List

from app.config.constants import DELTA_T_DECIMALS, EXPO_CALX_IO_KEY, HK_TIME_FORMAT
from app.etl.models import HourlySlice
from app.io_types import registry


def to_request_rows(pid: str, slices: List[HourlySlice]) -> List[list]:
    """One expo_calx row per slice, in the same order: the API has no row
    id, so results are matched back to slices by position.

    Row shape: [t, pid, lng, lat, {"IO": api_label}, delta_t_hours]. The io
    name is translated to the io type's api_label (the two can differ)
    here only; the name stays everywhere else (DB, chart).
    """
    return [
        [
            s.start_time_hk.strftime(HK_TIME_FORMAT),
            pid,
            s.lng,
            s.lat,
            {EXPO_CALX_IO_KEY: registry.get(s.io).api_label},
            round(s.delta_t_hours, DELTA_T_DECIMALS),
        ]
        for s in slices
    ]
