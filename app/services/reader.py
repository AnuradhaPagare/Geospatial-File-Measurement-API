"""Reads Shapefile / KML into a flat list of features (GeoJSON + shapely geometry)."""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import pyogrio
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app import config
from app.services.errors import FileProcessingError


@dataclass
class ParsedFeature:
    index: int
    layer: str
    geometry_type: str | None
    geometry: dict[str, Any] | None  # GeoJSON in the file's own CRS
    shape: BaseGeometry | None
    properties: dict[str, Any]
    crs: CRS
    crs_label: str


@dataclass
class ParsedFile:
    features: list[ParsedFeature] = field(default_factory=list)

    @property
    def crs_label(self) -> str | None:
        labels = {f.crs_label for f in self.features}
        if not labels:
            return None
        return labels.pop() if len(labels) == 1 else "MIXED"


def crs_label(crs: CRS) -> str:
    auth = crs.to_authority(min_confidence=70)
    return f"{auth[0]}:{auth[1]}" if auth else (crs.name or "UNKNOWN")


def safe_extract_zip(zip_path: Path, dest: Path) -> None:
    """Extract a zip while guarding against zip-bombs and path traversal."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise FileProcessingError("File is not a valid zip archive") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > config.MAX_ZIP_MEMBERS:
            raise FileProcessingError("Zip archive contains too many files")
        if sum(i.file_size for i in infos) > config.MAX_UNZIPPED_BYTES:
            raise FileProcessingError("Zip archive is too large when uncompressed")

        root = dest.resolve()
        for info in infos:
            if info.is_dir() or info.filename.startswith("__MACOSX/"):
                continue
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(root):
                raise FileProcessingError("Zip archive contains an unsafe path")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                out.write(src.read())


def find_datasets(directory: Path) -> list[Path]:
    shp = sorted(p for p in directory.rglob("*") if p.suffix.lower() == ".shp"
                 and not p.name.startswith("._"))
    if not shp:
        raise FileProcessingError("Zip archive does not contain a .shp file")
    return shp


def read_datasets(paths: list[Path]) -> ParsedFile:
    parsed = ParsedFile()
    index = 0
    for path in paths:
        is_kml = path.suffix.lower() == ".kml"
        try:
            layers = [str(row[0]) for row in pyogrio.list_layers(path)]
        except Exception as exc:
            raise FileProcessingError(f"Could not open {path.name}: {exc}") from exc

        for layer in layers:
            try:
                gdf = gpd.read_file(path, layer=layer, engine="pyogrio")
            except Exception as exc:
                raise FileProcessingError(f"Could not read layer '{layer}': {exc}") from exc
            if gdf.empty:
                continue

            if gdf.crs is None:
                if is_kml:
                    gdf = gdf.set_crs(4326)  # KML is always WGS84 lon/lat by specification
                else:
                    raise FileProcessingError(
                        f"'{path.name}' has no CRS (missing .prj file); refusing to guess"
                    )
            crs = CRS.from_user_input(gdf.crs)
            label = crs_label(crs)

            collection = json.loads(gdf.to_json(drop_id=True))
            for shape, gj in zip(gdf.geometry, collection["features"]):
                geom = gj.get("geometry")
                parsed.features.append(
                    ParsedFeature(
                        index=index,
                        layer=layer,
                        geometry_type=geom["type"] if geom else None,
                        geometry=geom,
                        shape=shape if shape is not None else None,
                        properties={k: v for k, v in (gj.get("properties") or {}).items()
                                    if v is not None},
                        crs=crs,
                        crs_label=label,
                    )
                )
                index += 1
    return parsed
