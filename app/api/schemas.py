"""Request and response bodies for the HTTP API."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class IngestRequest(BaseModel):
    pid: str
    # Raw location records; see app/etl/parsers for the accepted formats.
    features: List[Dict[str, Any]]


class IngestQueued(BaseModel):
    status: str
    batch_id: int


class BatchStatusOut(BaseModel):
    id: int
    pid: str
    status: str  # see constants.BatchStatus
    error_detail: Optional[str] = None
    hours_processed: Optional[int] = None
    rows: Optional[int] = None  # rows stored, inside and outside HK
    created_at: str
    updated_at: str


class HourlyRow(BaseModel):
    hour_start_hk: str
    start_time_hk: str
    io: str
    delta_t_hours: float
    exposure_value: Optional[float] = None
    lng: Optional[float] = None
    lat: Optional[float] = None
    # False = outside Hong Kong: not scored by expo_calx, exposure_value is null.
    in_hk: bool
