import io
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from pyproj import Geod
from shapely.geometry import LineString, Point, box

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder><name>sites</name>
<Placemark><name>plot</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
74.70,19.10,0 74.71,19.10,0 74.71,19.11,0 74.70,19.11,0 74.70,19.10,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>road</name><LineString><coordinates>
74.70,19.10,0 74.75,19.12,0 74.80,19.10,0
</coordinates></LineString></Placemark>
<Placemark><name>well</name><Point><coordinates>74.72,19.105,0</coordinates></Point></Placemark>
</Folder></Document></kml>"""


def _zip_shapefiles(layers: dict, drop_prj: bool = False) -> bytes:
    """layers: {name: GeoDataFrame}. One Shapefile per entry, all in one zip."""
    with tempfile.TemporaryDirectory() as tmp:
        for name, gdf in layers.items():
            gdf.to_file(Path(tmp) / f"{name}.shp")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for p in sorted(Path(tmp).iterdir()):
                if drop_prj and p.suffix == ".prj":
                    continue
                zf.write(p, p.name)
        return buf.getvalue()


def _upload(client, name, data, ctype="application/octet-stream"):
    return client.post("/api/files/", files={"file": (name, data, ctype)})


def test_kml_end_to_end(client):
    r = _upload(client, "survey.kml", KML.encode())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "COMPLETED"
    assert body["feature_count"] == 3
    assert body["crs"] == "EPSG:4326"

    info = client.get(f"/api/files/{body['id']}/").json()
    assert info["filename"] == "survey.kml"

    m = client.get(f"/api/files/{body['id']}/measurements/").json()
    by_type = {x["geometry_type"]: x for x in m["measurements"]}
    expected = abs(Geod(ellps="WGS84").geometry_area_perimeter(box(74.70, 19.10, 74.71, 19.11))[0])
    assert by_type["Polygon"]["area_m2"] == pytest.approx(expected, rel=1e-3)
    assert by_type["LineString"]["length_m"] > 5000
    assert by_type["Point"]["status"] == "NOT_REQUIRED"
    assert m["summary"]["by_status"]["MEASURED"] == 2

    only_polys = client.get(f"/api/files/{body['id']}/measurements/?geometry_type=Polygon").json()
    assert only_polys["total"] == 1

    feats = client.get(f"/api/files/{body['id']}/features/").json()
    assert feats["total"] == 3
    assert feats["features"][0]["crs"] == "EPSG:4326"
    assert feats["features"][0]["geometry"]["type"] == "Polygon"


def test_projected_shapefile(client):
    polys = gpd.GeoDataFrame({"name": ["a"]}, geometry=[box(500000, 2100000, 500100, 2100100)],
                             crs="EPSG:32643")
    lines = gpd.GeoDataFrame({"name": ["b"]},
                             geometry=[LineString([(500000, 2100000), (500300, 2100400)])],
                             crs="EPSG:32643")
    # file names sort alphabetically, so a_polys is read before b_lines
    r = _upload(client, "plots.zip", _zip_shapefiles({"a_polys": polys, "b_lines": lines}))
    assert r.status_code == 201, r.text
    assert r.json()["feature_count"] == 2
    assert r.json()["crs"] == "EPSG:32643"
    m = client.get(f"/api/files/{r.json()['id']}/measurements/").json()["measurements"]
    assert m[0]["area_m2"] == pytest.approx(10_000, rel=1e-3)
    assert m[1]["length_m"] == pytest.approx(500, rel=1e-3)


def test_geographic_shapefile_with_points(client):
    polys = gpd.GeoDataFrame({"n": [1]}, geometry=[box(74.7, 19.1, 74.71, 19.11)], crs="EPSG:4326")
    pts = gpd.GeoDataFrame({"n": [2]}, geometry=[Point(74.7, 19.1)], crs="EPSG:4326")
    r = _upload(client, "geo.zip", _zip_shapefiles({"a_polys": polys, "b_points": pts}))
    assert r.status_code == 201, r.text
    m = client.get(f"/api/files/{r.json()['id']}/measurements/").json()["measurements"]
    assert m[0]["area_m2"] > 1_000_000
    assert m[1]["status"] == "NOT_REQUIRED"


def test_shapefile_without_prj_is_rejected(client):
    gdf = gpd.GeoDataFrame({"n": [1]}, geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    r = _upload(client, "noprj.zip", _zip_shapefiles({"plots": gdf}, drop_prj=True))
    assert r.status_code == 422
    assert r.json()["status"] == "FAILED"
    assert client.get(f"/api/files/{r.json()['id']}/measurements/").status_code == 409
    
def test_unsupported_extension(client):
    assert _upload(client, "data.geojson", b"{}").status_code == 415


def test_corrupt_zip(client):
    r = _upload(client, "bad.zip", b"definitely not a zip")
    assert r.status_code == 422


def test_zip_slip_is_blocked(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.shp", b"x")
    assert _upload(client, "evil.zip", buf.getvalue()).status_code == 422


def test_unknown_file_404(client):
    assert client.get("/api/files/nope/").status_code == 404
