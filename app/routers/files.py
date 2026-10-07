import logging
import tempfile
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config, models
from app.db import get_db
from app.schemas import (FeatureOut, FeaturesResponse, FileOut, MeasurementOut,
                         MeasurementsResponse, MeasurementSummary)
from app.services.errors import FileProcessingError
from app.services.processor import process_file

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])

CHUNK = 1024 * 1024


def _save_upload(upload: UploadFile, dest: Path) -> None:
    """Stream to disk, aborting as soon as the size limit is exceeded."""
    written = 0
    with open(dest, "wb") as out:
        while chunk := upload.file.read(CHUNK):
            written += len(chunk)
            if written > config.MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"File exceeds {config.MAX_UPLOAD_BYTES // 2**20} MB limit")
            out.write(chunk)
    if written == 0:
        raise HTTPException(400, "Uploaded file is empty")


def _get_file(db: Session, file_id: str) -> models.UploadedFile:
    record = db.get(models.UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, "File not found")
    return record


def _require_completed(record: models.UploadedFile) -> None:
    if record.status != models.COMPLETED:
        raise HTTPException(409, f"File is not ready (status: {record.status})")


@router.post("/", response_model=FileOut, status_code=201,
             responses={415: {"description": "Unsupported file type"},
                        422: {"description": "File could not be processed"}})
def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a `.zip` (containing a Shapefile) or a `.kml`; it is processed synchronously."""
    filename = Path(file.filename or "").name
    ext = Path(filename).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Only .zip (Shapefile) and .kml files are supported")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{ext}"
        _save_upload(file, path)

        record = models.UploadedFile(filename=filename, status=models.PROCESSING)
        db.add(record)
        db.commit()

        try:
            parsed, measurements = process_file(path)
        except FileProcessingError as exc:
            return _fail(db, record, str(exc), 422)
        except Exception:
            log.exception("Unexpected error while processing %s", record.id)
            return _fail(db, record, "Unexpected error while processing file", 500)

    db.add_all(
        models.Feature(
            file_id=record.id, index=f.index, layer=f.layer, geometry_type=f.geometry_type,
            geometry=f.geometry, crs=f.crs_label, properties=f.properties, measurement=m,
        )
        for f, m in zip(parsed.features, measurements)
    )
    record.crs = parsed.crs_label
    record.feature_count = len(parsed.features)
    record.status = models.COMPLETED
    db.commit()
    return record


def _fail(db: Session, record: models.UploadedFile, message: str, status_code: int):
    record.status = models.FAILED
    record.error = message
    db.commit()
    body = FileOut.model_validate(record).model_dump(mode="json")
    return JSONResponse(body, status_code=status_code)


@router.get("/{file_id}/", response_model=FileOut)
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _get_file(db, file_id)


@router.get("/{file_id}/features/", response_model=FeaturesResponse)
def get_features(file_id: str,
                 limit: int = Query(100, ge=1, le=1000),
                 offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db)):
    """Features with geometry (GeoJSON, original CRS), CRS and properties."""
    record = _get_file(db, file_id)
    _require_completed(record)
    rows = db.scalars(
        select(models.Feature).where(models.Feature.file_id == file_id)
        .order_by(models.Feature.index).limit(limit).offset(offset)
    ).all()
    return FeaturesResponse(file_id=file_id, total=record.feature_count, limit=limit,
                            offset=offset, features=[FeatureOut.model_validate(r) for r in rows])


@router.get("/{file_id}/measurements/", response_model=MeasurementsResponse)
def get_measurements(file_id: str,
                     geometry_type: str | None = Query(None, description="e.g. Polygon"),
                     limit: int = Query(1000, ge=1, le=10000),
                     offset: int = Query(0, ge=0),
                     db: Session = Depends(get_db)):
    record = _get_file(db, file_id)
    _require_completed(record)

    # Summary covers the whole file, independent of pagination / filtering.
    summary_rows = db.execute(
        select(models.Feature.geometry_type, models.Feature.measurement)
        .where(models.Feature.file_id == file_id)
    ).all()
    total_area = sum(m.get("area_m2") or 0 for _, m in summary_rows)
    total_length = sum(m.get("length_m") or 0 for _, m in summary_rows)
    summary = MeasurementSummary(
        total_area_m2=round(total_area, 4),
        total_area_ha=round(total_area / 10_000, 6),
        total_length_m=round(total_length, 4),
        by_status=dict(Counter(m.get("status") for _, m in summary_rows)),
        by_geometry_type=dict(Counter(g or "None" for g, _ in summary_rows)),
    )

    query = select(models.Feature).where(models.Feature.file_id == file_id)
    if geometry_type:
        query = query.where(models.Feature.geometry_type == geometry_type)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(models.Feature.index).limit(limit).offset(offset)).all()

    return MeasurementsResponse(
        file_id=file_id, crs=record.crs, total=total, limit=limit, offset=offset,
        summary=summary,
        measurements=[
            MeasurementOut(feature_id=r.index, layer=r.layer, geometry_type=r.geometry_type,
                           **r.measurement)
            for r in rows
        ],
    )
