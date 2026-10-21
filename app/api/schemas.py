"""Request and response bodies for the HTTP API."""

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import AwareDatetime, BaseModel, Field

from app.config.constants import PLACE_LABEL_ID_MAX_LENGTH, PLACE_LABEL_MAX_RADIUS_M, PLACE_LABEL_MIN_RADIUS_M
from app.config.io_types import PLACE_LABEL_CATEGORIES


class IngestRequest(BaseModel):
    pid: str
    # Raw location records; see app/pipeline/parsers for the accepted formats.
    # Only each record's time and position are read.
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
    # "label" if a place label decided io, "inferred" if the decision tree did.
    io_source: str
    # Which branch of the decision tree decided io (constants.Reason).
    reason: Optional[str] = None
    # "stay" (one point for a whole stay) or "route" (one point of a route).
    kind: Optional[str] = None
    delta_t_hours: float
    exposure_value: Optional[float] = None
    lng: Optional[float] = None
    lat: Optional[float] = None
    # Route points: direction towards the next point, 0 = north, clockwise.
    heading_deg: Optional[float] = None
    compass: Optional[str] = None
    # Vehicle points: the road the point is on.
    road_name: Optional[str] = None
    # False = outside Hong Kong: not scored by expo_calx, exposure_value is null.
    in_hk: bool


class DayDeleted(BaseModel):
    pid: str
    date: date  # HK local
    rows_deleted: int  # 0 if the day had no data
