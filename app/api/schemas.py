"""Request and response bodies for the HTTP API."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import AwareDatetime, BaseModel, Field

from app.config.constants import PLACE_LABEL_ID_MAX_LENGTH, PLACE_LABEL_MAX_RADIUS_M, PLACE_LABEL_MIN_RADIUS_M
from app.config.io_types import PLACE_LABEL_CATEGORIES


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


class PlaceLabelIn(BaseModel):
    contributor_id: str = Field(min_length=1, max_length=PLACE_LABEL_ID_MAX_LENGTH)
    category: Literal[PLACE_LABEL_CATEGORIES]
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: float = Field(ge=PLACE_LABEL_MIN_RADIUS_M, le=PLACE_LABEL_MAX_RADIUS_M)
    updated_at: AwareDatetime


class PlaceLabelOut(BaseModel):
    label_id: str
    contributor_id: str
    category: str
    lat: float
    lng: float
    radius_m: float
    updated_at: datetime  # UTC


class HourlyRow(BaseModel):
    hour_start_hk: str
    start_time_hk: str
    io: str
    # "label" if a place label decided io, "inferred" if the io type rules did.
    io_source: str
    delta_t_hours: float
    exposure_value: Optional[float] = None
    lng: Optional[float] = None
    lat: Optional[float] = None
    # False = outside Hong Kong: not scored by expo_calx, exposure_value is null.
    in_hk: bool
