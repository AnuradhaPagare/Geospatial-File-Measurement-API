# Geospatial File Measurement API

A FastAPI service that accepts a **Shapefile (`.zip`)** or **KML** file, extracts every feature
(ID, geometry type, geometry, CRS, properties) and computes **area / perimeter / length** in metres,
always after projecting to a suitable metric CRS — never in degrees.

## Setup

Requires Python 3.10+ (wheels for geopandas/pyogrio/pyproj bundle GDAL and PROJ, so no system GDAL is needed).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

- Interactive docs: http://localhost:8000/docs
- Tests: `pytest -q`
- Docker: `docker build -t geo-api . && docker run -p 8000:8000 geo-api`

Configuration (environment variables): `DATABASE_URL` (default `sqlite:///./data/geo.db`),
`MAX_UPLOAD_MB` (50), `MAX_UNZIPPED_MB` (300), `MAX_ZIP_MEMBERS` (500).

## API

### `POST /api/files/` — upload and process
Multipart field `file`: `.kml` or `.zip` containing a Shapefile (`.shp/.shx/.dbf/.prj`).

```bash
curl -F "file=@survey.kml" http://localhost:8000/api/files/
```
```json
{"id": "9f2c…", "filename": "survey.kml", "feature_count": 3, "crs": "EPSG:4326",
 "status": "COMPLETED", "created_at": "2026-10-07T10:00:00Z", "error": null}
```
| Code | Meaning |
|------|---------|
| 201 | Processed (`status: COMPLETED`) |
| 400 / 413 | Empty file / over size limit |
| 415 | Extension not `.zip` / `.kml` |
| 422 | Unreadable file (bad zip, no `.shp`, missing `.prj`, no features). A `FAILED` record is kept; body has `id` and `error`. |

### `GET /api/files/{id}/` — file info
Same shape as the upload response. `404` if unknown.

### `GET /api/files/{id}/measurements/` — measurements
Query params: `geometry_type` (e.g. `Polygon`), `limit` (default 1000), `offset`.
`409` if the file is not `COMPLETED`.
```json
{
  "file_id": "9f2c…", "crs": "EPSG:4326", "total": 3, "limit": 1000, "offset": 0,
  "summary": {"total_area_m2": 1167231.4, "total_area_ha": 116.72, "total_length_m": 9741.2,
              "by_status": {"MEASURED": 2, "NOT_REQUIRED": 1},
              "by_geometry_type": {"Polygon": 1, "LineString": 1, "Point": 1}},
  "measurements": [
    {"feature_id": 0, "layer": "sites", "geometry_type": "Polygon", "status": "MEASURED",
     "area_m2": 1167231.4, "area_ha": 116.72, "perimeter_m": 4322.8, "length_m": null,
     "projected_crs": "EPSG:32643", "is_valid": true, "warnings": []},
    {"feature_id": 2, "layer": "sites", "geometry_type": "Point", "status": "NOT_REQUIRED",
     "area_m2": null, "length_m": null, "projected_crs": null, "warnings": []}
  ]
}
```
Measurement `status`: `MEASURED`, `NOT_REQUIRED` (points), `UNSUPPORTED` (e.g. GeometryCollection),
`EMPTY` (null geometry), `FAILED` (projection error). Non-measurable features never fail the request.

### `GET /api/files/{id}/features/` — extracted features
Paginated (`limit`, `offset`). Each item: `index`, `layer`, `geometry_type`, `crs`, `geometry` (GeoJSON in the
file's original CRS) and `properties`.

## Architecture

```
app/
  main.py            FastAPI app, lifespan (creates tables)
  config.py, db.py   settings, SQLAlchemy engine/session
  models.py          UploadedFile, Feature tables
  schemas.py         Pydantic response models
  routers/files.py   HTTP layer only: validation, persistence, status codes
  services/
    reader.py        zip extraction (safe) + Shapefile/KML -> features
    measure.py       CRS selection, reprojection, area/length
    processor.py     orchestrates reader + measure
```

**File-processing flow**
1. Router validates the extension and streams the upload to a temp dir (size-capped).
2. An `UploadedFile` row is created with `PROCESSING`.
3. `.zip`: safely extracted (zip-slip, member-count and uncompressed-size checks) and every `.shp` found is read.
   `.kml`: read directly; each KML folder/document becomes a layer.
4. GeoPandas (pyogrio/GDAL) reads each layer. Each row becomes a feature with a global 0-based `index`,
   layer name, GeoJSON geometry, CRS label and properties (nulls dropped, NaN/timestamps made JSON-safe).
5. Features and measurements are stored in one transaction; the file becomes `COMPLETED` (or `FAILED` with a message).

**Measurement flow** (`measure_geometry`)
Point → `NOT_REQUIRED` · Polygon/MultiPolygon → area + perimeter · LineString/MultiLineString → length ·
anything else / empty → `UNSUPPORTED` / `EMPTY`. Z values are dropped (`force_2d`). Invalid polygons are measured
but flagged with `is_valid: false` and a warning. Every call is wrapped so one bad feature can't fail the file.

**CRS handling**
- KML is WGS84 lon/lat by spec → `EPSG:4326`. Shapefiles use their `.prj`; if it is missing the upload is
  rejected (422) instead of guessing.
- For each geometry the centroid is converted to lon/lat and the matching **WGS84 UTM zone** (EPSG:326xx north /
  327xx south, UPS 32661/32761 beyond 84°N / 80°S) is chosen. The geometry is transformed with
  `pyproj` (`always_xy=True`) and measured in metres. Transformers are cached.
- This is applied even to already-projected inputs (e.g. Web Mercator or a state-plane CRS in feet), so units and
  distortion are consistent. The used CRS is returned as `projected_crs`.
- Stored geometries stay in the original CRS.

## Design decisions

| Decision | Why / alternatives |
|---|---|
| **FastAPI** | Typed schemas and free OpenAPI docs; lighter than Django+DRF for a pure API with no admin/auth needs. |
| **Per-feature UTM** | Simple, accurate (~0.04% scale error inside a zone) and deterministic. *Alternatives:* geodesic calculation with `pyproj.Geod` (accurate for any size, but harder to extend to other measures); a single equal-area CRS (e.g. EPSG:6933) for areas (global, but length distortion); one CRS per file (fails for files spanning zones). |
| **GeoPandas + pyogrio** | One reader for Shapefile and KML (GDAL), handles encodings, dates and CRS parsing. Cost: heavier dependency than `pyshp` + a hand-written KML parser, but far fewer edge cases. |
| **Synchronous processing** | Keeps the API simple; status field already models async states. FastAPI runs the sync endpoint in a threadpool so the event loop isn't blocked. |
| **SQLite + SQLAlchemy, features in JSON columns** | Zero-setup persistence that works with the required `GET` endpoints; swapping `DATABASE_URL` to Postgres works unchanged. Measurements are precomputed at upload so reads are cheap. |
| **Reject Shapefile without `.prj`** | Assuming 4326 silently yields wrong areas, which is worse than an error. |
| **Original files not retained** | Only parsed data is stored; avoids storage/retention concerns. |

## Learnings

- KML carries Z coordinates and mixed geometry types in one folder; both must be handled before measuring.
- Shapefile `.prj` files are ESRI WKT, so resolving them to an EPSG code needs a confidence threshold in pyproj.
- "Don't crash on odd geometry" is easiest when measurement returns a status object rather than raising.
- Zip uploads need explicit defences (zip-slip, decompression bombs).

## Known limitations & future scope

- **Large / antimeridian-crossing / multi-zone geometries** lose accuracy in a single UTM zone; a geodesic
  (`Geod`) path or equal-area CRS for large features would fix this.
- Move processing to a background worker (Celery/RQ) with `202 Accepted` + polling for big files.
- PostGIS storage with spatial indexes, bbox queries, and generated measurements in SQL.
- More formats (GeoJSON, GeoPackage, KMZ), a `?crs=` override for `.prj`-less shapefiles, unit options (acres, km²).
- Auth, rate limiting, file retention/cleanup, Alembic migrations, CI pipeline, and streaming reads for very large files.
- Optionally auto-repair invalid geometries (`make_valid`) behind a flag.
