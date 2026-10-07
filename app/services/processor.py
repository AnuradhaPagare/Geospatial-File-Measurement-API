"""Orchestrates: upload on disk -> parsed features -> measured features."""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.services.errors import FileProcessingError
from app.services.measure import measure_geometry
from app.services.reader import (ParsedFile, find_datasets, read_datasets,
                                 safe_extract_zip)


def process_file(upload_path: Path) -> tuple[ParsedFile, list[dict]]:
    """Returns the parsed file and a measurement dict per feature (same order)."""
    suffix = upload_path.suffix.lower()
    if suffix == ".kml":
        parsed = read_datasets([upload_path])
    elif suffix == ".zip":
        with tempfile.TemporaryDirectory() as tmp:
            extract_dir = Path(tmp)
            safe_extract_zip(upload_path, extract_dir)
            parsed = read_datasets(find_datasets(extract_dir))
    else:
        raise FileProcessingError(f"Unsupported file type '{suffix}'")

    if not parsed.features:
        raise FileProcessingError("File contains no features")

    measurements = [measure_geometry(f.shape, f.crs) for f in parsed.features]
    return parsed, measurements
