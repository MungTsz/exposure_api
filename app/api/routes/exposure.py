"""GET /exposure/hourly/{pid}: finished rows for the app's chart and map."""

from typing import List

from fastapi import APIRouter

from app.api.schemas import HourlyRow
from app.db import exposure_repository

router = APIRouter(tags=["exposure"])


@router.get("/exposure/hourly/{pid}", response_model=List[HourlyRow])
def get_hourly(pid: str) -> List[HourlyRow]:
    """Returns one entry per (hour, io, stay) - more than one row can share an
    hour_start_hk+io if multiple separate stays landed in that hour. Sum
    delta_t_hours/exposure_value across rows sharing an hour_start_hk+io for
    the stacked bar chart; use each row's own lng/lat (in start_time_hk
    order) to draw the route. Rows with in_hk false are outside Hong Kong:
    they have no exposure_value and should be drawn differently."""
    return [HourlyRow(**r) for r in exposure_repository.get_hourly(pid)]
