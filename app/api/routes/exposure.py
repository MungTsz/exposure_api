"""GET /exposure/hourly/{pid}: finished rows for the app's chart and map.
DELETE /exposure/hourly/{pid}?date=: remove one HK-local day of them."""

from datetime import date
from typing import List

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import DayDeleted, HourlyRow
from app.db import exposure_repository
from app.services import day_service

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


@router.delete(
    "/exposure/hourly/{pid}",
    response_model=DayDeleted,
    responses={409: {"description": "an upload with pings on that day is still pending/processing; nothing changed"}},
)
def delete_day(pid: str, day: date = Query(..., alias="date", description="HK-local day, YYYY-MM-DD")) -> DayDeleted:
    """Delete every row of `pid` whose start_time_hk is on that HK-local day,
    and cut the day's pings out of its stored uploads so a later place label
    change can't bring the day back. Idempotent: a day with no data returns
    rows_deleted 0. Upload the day again afterwards to re-import it."""
    try:
        rows_deleted = day_service.delete_day(pid, day)
    except day_service.DayBusyError as e:
        raise HTTPException(409, str(e)) from e
    return DayDeleted(pid=pid, date=day, rows_deleted=rows_deleted)
