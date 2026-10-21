"""Builds expo_calx request rows from hourly slices."""

from typing import List, Sequence

from app.config.constants import DELTA_T_DECIMALS, EXPO_CALX_IO_KEY, HK_TIME_FORMAT
from app.config.io_types import API_LABELS
from app.pipeline.models import HourlySlice


def to_request_rows(pid: str, slices: Sequence[HourlySlice]) -> List[list]:
    """One expo_calx row per slice, in the same order: the API has no row
    id, so results are matched back to slices by position.

    Row shape: [t, pid, lng, lat, {"IO": api_label}, delta_t_hours]. The io
    name is translated to its expo_calx label here only; the name stays
    everywhere else (DB, chart).
    """
    return [
        [
            s.start_time_hk.strftime(HK_TIME_FORMAT),
            pid,
            s.point.lng,
            s.point.lat,
            {EXPO_CALX_IO_KEY: API_LABELS[s.point.io]},
            round(s.delta_t_hours, DELTA_T_DECIMALS),
        ]
        for s in slices
    ]
