from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    feature_count: int
    crs: str | None
    status: str
    created_at: datetime
    error: str | None = None


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    layer: str | None
    geometry_type: str | None
    crs: str | None
    geometry: dict[str, Any] | None
    properties: dict[str, Any]


class FeaturesResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    features: list[FeatureOut]


class MeasurementOut(BaseModel):
    feature_id: int
    layer: str | None
    geometry_type: str | None
    status: str  # MEASURED | NOT_REQUIRED | UNSUPPORTED | EMPTY | FAILED
    area_m2: float | None = None
    area_ha: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    projected_crs: str | None = None
    is_valid: bool | None = None
    warnings: list[str] = []


class MeasurementSummary(BaseModel):
    total_area_m2: float
    total_area_ha: float
    total_length_m: float
    by_status: dict[str, int]
    by_geometry_type: dict[str, int]


class MeasurementsResponse(BaseModel):
    file_id: str
    crs: str | None
    total: int
    limit: int
    offset: int
    summary: MeasurementSummary
    measurements: list[MeasurementOut]
