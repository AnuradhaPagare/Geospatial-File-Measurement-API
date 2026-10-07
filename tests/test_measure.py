import pytest
from pyproj import CRS, Geod
from shapely.geometry import (GeometryCollection, LineString, MultiPolygon, Point,
                              Polygon, box)

from app.services.measure import (EMPTY, FAILED, MEASURED, NOT_REQUIRED, UNSUPPORTED,
                                  measure_geometry, utm_epsg_for)

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")


@pytest.mark.parametrize("lon,lat,epsg", [
    (74.7, 19.1, 32643),
    (-0.1, 51.5, 32630),
    (151.2, -33.8, 32756),
    (10, 85, 32661),
    (10, -85, 32761),
    (180, 0, 32660),
])
def test_utm_zone_selection(lon, lat, epsg):
    assert utm_epsg_for(lon, lat) == epsg


def test_polygon_area_matches_geodesic():
    poly = box(74.70, 19.10, 74.71, 19.11)
    m = measure_geometry(poly, WGS84)
    expected_area, expected_perimeter = (abs(v) for v in GEOD.geometry_area_perimeter(poly))
    assert m["status"] == MEASURED
    assert m["projected_crs"] == "EPSG:32643"
    assert m["area_m2"] == pytest.approx(expected_area, rel=1e-3)
    assert m["perimeter_m"] == pytest.approx(expected_perimeter, rel=1e-3)
    assert m["area_ha"] == pytest.approx(m["area_m2"] / 10_000, rel=1e-6)


def test_degrees_are_not_used_directly():
    m = measure_geometry(box(74.70, 19.10, 74.71, 19.11), WGS84)
    assert m["area_m2"] > 1_000_000  # ~1.2 km^2, not 0.0001 "square degrees"


def test_linestring_length_matches_geodesic():
    line = LineString([(74.70, 19.10), (74.75, 19.12), (74.80, 19.10)])
    m = measure_geometry(line, WGS84)
    assert m["length_m"] == pytest.approx(GEOD.geometry_length(line), rel=1e-3)


def test_multipolygon_and_z_coordinates():
    a = Polygon([(74.70, 19.10, 5), (74.71, 19.10, 5), (74.71, 19.11, 5), (74.70, 19.11, 5)])
    b = box(74.80, 19.10, 74.81, 19.11)
    m = measure_geometry(MultiPolygon([a, b]), WGS84)
    assert m["status"] == MEASURED and m["area_m2"] > 2_000_000


def test_projected_source_crs():
    utm = CRS.from_epsg(32643)
    m = measure_geometry(box(500000, 2100000, 500100, 2100100), utm)
    assert m["area_m2"] == pytest.approx(10_000, rel=1e-3)


def test_point_not_required():
    assert measure_geometry(Point(74.7, 19.1), WGS84)["status"] == NOT_REQUIRED


def test_empty_and_none():
    assert measure_geometry(None, WGS84)["status"] == EMPTY
    assert measure_geometry(Polygon(), WGS84)["status"] == EMPTY


def test_unsupported_geometry_is_graceful():
    gc = GeometryCollection([Point(74.7, 19.1), LineString([(74.7, 19.1), (74.8, 19.2)])])
    m = measure_geometry(gc, WGS84)
    assert m["status"] == UNSUPPORTED and m["warnings"]


def test_invalid_polygon_is_flagged():
    bowtie = Polygon([(74.70, 19.10), (74.71, 19.11), (74.71, 19.10), (74.70, 19.11)])
    m = measure_geometry(bowtie, WGS84)
    assert m["status"] in (MEASURED, FAILED)
    if m["status"] == MEASURED:
        assert m["is_valid"] is False and m["warnings"]
