"""Geometry measurement.

Strategy: never measure in degrees. Each geometry is reprojected to the UTM zone
(or UPS near the poles) that contains its centroid, and measured there in metres.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from pyproj import CRS, Transformer
from shapely import force_2d
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString", "LinearRing"}
POINT_TYPES = {"Point", "MultiPoint"}

MEASURED = "MEASURED"
NOT_REQUIRED = "NOT_REQUIRED"
UNSUPPORTED = "UNSUPPORTED"
EMPTY = "EMPTY"
FAILED = "FAILED"


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the WGS84 UTM zone for a lon/lat (UPS polar zones beyond 84N / 80S)."""
    if lat >= 84:
        return 32661  # UPS North
    if lat <= -80:
        return 32761  # UPS South
    zone = min(max(int((lon + 180) // 6) + 1, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=256)
def _transformer(src: CRS, dst_epsg: int) -> Transformer:
    return Transformer.from_crs(src, CRS.from_epsg(dst_epsg), always_xy=True)


def _centroid_lonlat(geom: BaseGeometry, src: CRS) -> tuple[float, float]:
    c = geom.centroid
    lon, lat = _transformer(src, 4326).transform(c.x, c.y)
    return lon, lat


def _result(status: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": status,
        "area_m2": None,
        "area_ha": None,
        "perimeter_m": None,
        "length_m": None,
        "projected_crs": None,
        "is_valid": None,
        "warnings": [],
    }
    base.update(kw)
    return base


def measure_geometry(geom: BaseGeometry | None, src_crs: CRS) -> dict[str, Any]:
    """Measure one geometry. Never raises: problems are reported in the result."""
    if geom is None or geom.is_empty:
        return _result(EMPTY, warnings=["Feature has no geometry"])

    gtype = geom.geom_type
    if gtype in POINT_TYPES:
        return _result(NOT_REQUIRED)
    if gtype not in AREA_TYPES | LENGTH_TYPES:
        return _result(
            UNSUPPORTED, warnings=[f"Measurement is not supported for geometry type {gtype}"]
        )

    try:
        geom2d = force_2d(geom)  # KML often carries a Z value we don't need
        lon, lat = _centroid_lonlat(geom2d, src_crs)
        epsg = utm_epsg_for(lon, lat)
        projected = shapely_transform(_transformer(src_crs, epsg).transform, geom2d)

        warnings: list[str] = []
        valid = bool(geom2d.is_valid)
        if not valid:
            warnings.append("Geometry is invalid (e.g. self-intersecting); result may be unreliable")

        if gtype in AREA_TYPES:
            area, perimeter = projected.area, projected.length
            values = (area, perimeter)
            out = {"area_m2": round(area, 4), "area_ha": round(area / 10_000, 6),
                   "perimeter_m": round(perimeter, 4)}
        else:
            length = projected.length
            values = (length,)
            out = {"length_m": round(length, 4)}

        if not all(math.isfinite(v) for v in values):
            raise ValueError("Projection produced non-finite coordinates")

        return _result(MEASURED, projected_crs=f"EPSG:{epsg}", is_valid=valid,
                       warnings=warnings, **out)
    except Exception as exc:  # one bad feature must not fail the whole file
        return _result(FAILED, warnings=[f"Could not measure geometry: {exc}"])
