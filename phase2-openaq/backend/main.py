from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

try:
    from . import db
except ImportError:  # supports `uvicorn main:app` from backend directory
    import db  # type: ignore

logger = logging.getLogger("aqi-app")
app = FastAPI(title="Smart Health AQI API", version="2.0.0", description="Database-backed air-quality monitoring API")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(","), allow_methods=["*"], allow_headers=["*"])

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"


def get_db():
    session = db.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    logger.info("Database tables are ready")


@app.get("/api/health")
def health(session: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        session.execute(select(1))
        return {"status": "ok", "database": "connected", "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        logger.exception("Database health check failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.get("/api/locations/search")
async def search_locations(query: str = Query(..., min_length=2, max_length=100), limit: int = Query(8, ge=1, le=20), session: Session = Depends(get_db)):
    """Global geocoding search. Search results are also persisted for later reuse."""
    headers = {"User-Agent": "SmartHealthAQI/1.0 (location search)"}
    params = {"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 1}
    try:
        async with httpx.AsyncClient(timeout=12, headers=headers) as client:
            response = await client.get(NOMINATIM_SEARCH_URL, params=params)
            response.raise_for_status()
            places = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Geocoding provider failed: %s", exc)
        raise HTTPException(status_code=502, detail="Location search provider is temporarily unavailable") from exc

    output = []
    for place in places:
        address = place.get("address") or {}
        lat, lon = float(place["lat"]), float(place["lon"])
        country_code = (address.get("country_code") or "").upper() or None
        locality = address.get("city") or address.get("town") or address.get("village") or address.get("municipality")
        source_id = str(place.get("place_id")) if place.get("place_id") is not None else None
        location = None
        if source_id:
            location = session.scalar(select(db.Location).where(db.Location.source == "nominatim", db.Location.source_location_id == source_id))
        if location is None:
            location = db.Location(name=place.get("name") or place.get("display_name", query)[:255], locality=locality,
                region=address.get("state") or address.get("region"), country=address.get("country"), country_code=country_code,
                latitude=lat, longitude=lon, source="nominatim", source_location_id=source_id)
            session.add(location)
        else:
            location.latitude, location.longitude = lat, lon
            location.locality, location.country = locality, address.get("country")
        output.append({"id": location.id, "name": place.get("display_name"), "city": locality,
            "state": address.get("state"), "country": address.get("country"), "country_code": country_code,
            "lat": lat, "lon": lon, "type": place.get("type")})
    session.commit()
    # Refresh generated database IDs after commit.
    for item in output:
        match = session.scalar(select(db.Location).where(db.Location.latitude == item["lat"], db.Location.longitude == item["lon"], db.Location.source == "nominatim"))
        if match:
            item["id"] = match.id
    return {"results": output, "provider": "OpenStreetMap Nominatim"}


@app.get("/api/locations")
def list_locations(limit: int = Query(100, ge=1, le=500), session: Session = Depends(get_db)):
    rows = session.scalars(select(db.Location).order_by(db.Location.name).limit(limit)).all()
    return {"locations": [{"id": x.id, "name": x.name, "city": x.locality, "country": x.country,
        "latitude": x.latitude, "longitude": x.longitude, "source": x.source} for x in rows]}


@app.get("/api/aqi/map")
def map_measurements(limit: int = Query(500, ge=1, le=2000), session: Session = Depends(get_db)):
    """Latest stored PM2.5 value per station; empty until a provider sync is configured."""
    rows = session.execute(select(db.Measurement, db.Station, db.Location)
        .join(db.Station, db.Measurement.station_id == db.Station.id)
        .join(db.Location, db.Station.location_id == db.Location.id)
        .where(db.Measurement.parameter == "pm25")
        .order_by(desc(db.Measurement.measured_at)).limit(limit)).all()
    seen, points = set(), []
    for measurement, station, location in rows:
        if station.id in seen:
            continue
        seen.add(station.id)
        points.append({"station_id": station.id, "station": station.name, "location_id": location.id,
            "location": location.name, "city": location.locality, "country": location.country,
            "latitude": station.latitude if station.latitude is not None else location.latitude,
            "longitude": station.longitude if station.longitude is not None else location.longitude,
            "pm25": measurement.value, "aqi": measurement.aqi_us, "unit": measurement.unit,
            "last_updated": measurement.measured_at.isoformat(), "source": measurement.source})
    return {"results": points, "count": len(points), "data_status": "available" if points else "no_measurements_yet"}



@app.get("/api/aqi/nearest")
def nearest_aqi(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
                radius_km: float = Query(50, ge=1, le=500), limit: int = Query(5, ge=1, le=20),
                session: Session = Depends(get_db)):
    """Return nearest stored PM2.5 station readings; never substitute sample city values."""
    rows = session.execute(select(db.Measurement, db.Station, db.Location)
        .join(db.Station, db.Measurement.station_id == db.Station.id)
        .join(db.Location, db.Station.location_id == db.Location.id)
        .where(db.Measurement.parameter == "pm25")
        .order_by(desc(db.Measurement.measured_at)).limit(2000)).all()
    import math
    nearest, seen = [], set()
    for measurement, station, location in rows:
        if station.id in seen:
            continue
        seen.add(station.id)
        slat = station.latitude if station.latitude is not None else location.latitude
        slon = station.longitude if station.longitude is not None else location.longitude
        dlat, dlon = math.radians(slat-lat), math.radians(slon-lon)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat))*math.cos(math.radians(slat))*math.sin(dlon/2)**2
        distance = 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        if distance <= radius_km:
            nearest.append((distance, {"location": location.name, "city": location.locality, "country": location.country,
                "latitude": slat, "longitude": slon, "pm25": measurement.value, "aqi": measurement.aqi_us,
                "last_updated": measurement.measured_at.isoformat(), "station_id": station.id, "distance_km": round(distance, 2)}))
    nearest.sort(key=lambda item: item[0])
    return {"results": [item[1] for item in nearest[:limit]], "data_status": "available" if nearest else "no_measurements_nearby"}


@app.get("/api/cities/sample")
def sample_cities():
    # Kept for frontend compatibility; no static or synthetic AQI values are returned.
    return {"cities": [], "data_status": "no_measurements_yet"}

@app.get("/api/aqi/history/{station_id}")
def station_history(station_id: int, limit: int = Query(100, ge=1, le=1000), session: Session = Depends(get_db)):
    station = session.get(db.Station, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")
    rows = session.scalars(select(db.Measurement).where(db.Measurement.station_id == station_id).order_by(desc(db.Measurement.measured_at)).limit(limit)).all()
    return {"station_id": station_id, "readings": [{"parameter": x.parameter, "value": x.value, "unit": x.unit,
        "timestamp": x.measured_at.isoformat(), "aqi": x.aqi_us, "source": x.source} for x in reversed(rows)]}


@app.get("/api/stations")
def list_stations(location_id: int | None = None, session: Session = Depends(get_db)):
    query = select(db.Station)
    if location_id is not None:
        query = query.where(db.Station.location_id == location_id)
    stations = session.scalars(query.order_by(db.Station.name)).all()
    return {"stations": [{"id": s.id, "location_id": s.location_id, "name": s.name,
        "source": s.source, "source_station_id": s.source_station_id, "is_active": s.is_active,
        "latitude": s.latitude, "longitude": s.longitude, "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None} for s in stations]}


@app.get("/api/alerts")
def get_alerts(limit: int = Query(50, ge=1, le=200), session: Session = Depends(get_db)):
    alerts = session.scalars(select(db.Alert).order_by(desc(db.Alert.created_at)).limit(limit)).all()
    return {"alerts": [{"id": a.id, "timestamp": a.created_at.isoformat(), "title": a.title,
        "description": a.description, "severity": a.severity, "is_acknowledged": a.is_acknowledged} for a in alerts]}


# Compatibility endpoints for the current dashboard. They intentionally return no fabricated sensor data.
@app.get("/api/aqi/latest")
def latest_aqi(session: Session = Depends(get_db)):
    row = session.scalar(select(db.Measurement).where(db.Measurement.parameter == "pm25").order_by(desc(db.Measurement.measured_at)))
    if row is None:
        return {"reading": None, "advisory": "No verified live AQI reading is stored yet.", "data_status": "no_measurements_yet"}
    return {"reading": {"timestamp": row.measured_at.isoformat(), "sensor_id": str(row.station_id), "pm25": row.value, "pm10": None, "aqi": row.aqi_us},
        "advisory": "AQI is calculated only when a valid measurement and supported AQI method are available."}


@app.get("/api/aqi/history")
def aqi_history(limit: int = Query(30, ge=1, le=500), session: Session = Depends(get_db)):
    rows = session.scalars(select(db.Measurement).where(db.Measurement.parameter == "pm25").order_by(desc(db.Measurement.measured_at)).limit(limit)).all()
    return {"readings": [{"timestamp": x.measured_at.isoformat(), "sensor_id": str(x.station_id), "pm25": x.value, "pm10": None, "aqi": x.aqi_us} for x in reversed(rows)]}


@app.get("/api/sensors/status")
def sensor_status(session: Session = Depends(get_db)):
    stations = session.scalars(select(db.Station).order_by(db.Station.id)).all()
    return {"sensors": [{"id": str(s.id), "location": s.name, "baseline_pm25": None, "baseline_pm10": None,
        "last_seen": s.last_seen_at.isoformat() if s.last_seen_at else None, "online": s.is_active,
        "health": "unknown"} for s in stations]}


@app.get("/api/geocode")
async def legacy_geocode(query: str = Query(..., min_length=2, max_length=100)):
    # Legacy route retained so the existing frontend does not break during the transition.
    headers = {"User-Agent": "SmartHealthAQI/1.0 (location search)"}
    async with httpx.AsyncClient(timeout=12, headers=headers) as client:
        try:
            response = await client.get(NOMINATIM_SEARCH_URL, params={"q": query, "format": "jsonv2", "limit": 5, "addressdetails": 1})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Location search provider unavailable") from exc
    results = []
    for p in response.json():
        address = p.get("address") or {}
        results.append({"name": p.get("display_name"), "city": address.get("city") or address.get("town") or address.get("village"),
            "state": address.get("state"), "country": address.get("country"), "lat": float(p["lat"]), "lon": float(p["lon"]), "type": p.get("type")})
    return {"results": results}



@app.get("/api/providers/openaq/status")
def openaq_status(session: Session = Depends(get_db)):
    source = session.scalar(select(db.ApiSource).where(db.ApiSource.name == "OpenAQ"))
    configured = bool(os.getenv("OPENAQ_API_KEY", "").strip())
    return {"provider": "OpenAQ v3", "configured": configured,
        "enabled": bool(source.is_enabled) if source else configured,
        "last_success_at": source.last_success_at.isoformat() if source and source.last_success_at else None,
        "last_error": source.last_error if source else None,
        "message": "API key configured" if configured else "Set OPENAQ_API_KEY in .env to enable live AQI ingestion"}


@app.post("/api/aqi/refresh/nearby")
async def refresh_nearby_aqi(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(25, ge=1, le=25), limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_db)):
    """Discover nearby OpenAQ stations, fetch their latest sensor values and persist them."""
    try:
        from .openaq import refresh_nearby
    except ImportError:
        from openaq import refresh_nearby  # type: ignore
    try:
        return await refresh_nearby(session, lat, lon, radius_km, limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        logger.exception("OpenAQ API request failed")
        raise HTTPException(status_code=502, detail=f"OpenAQ returned HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        logger.exception("OpenAQ network request failed")
        raise HTTPException(status_code=502, detail="Could not reach OpenAQ. Try again later.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/aqi/location")
def aqi_for_coordinates(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(25, ge=1, le=500), limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db)):
    """Return most recent stored measurements within a radius of a selected map location."""
    import math
    rows = session.execute(select(db.Measurement, db.Station, db.Location)
        .join(db.Station, db.Measurement.station_id == db.Station.id)
        .join(db.Location, db.Station.location_id == db.Location.id)
        .order_by(desc(db.Measurement.measured_at)).limit(5000)).all()
    latest: dict[tuple[int, str], tuple[db.Measurement, db.Station, db.Location, float]] = {}
    for measurement, station, location in rows:
        key = (station.id, measurement.parameter)
        if key in latest:
            continue
        slat = station.latitude if station.latitude is not None else location.latitude
        slon = station.longitude if station.longitude is not None else location.longitude
        dlat, dlon = math.radians(slat-lat), math.radians(slon-lon)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat))*math.cos(math.radians(slat))*math.sin(dlon/2)**2
        distance = 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0, 1-a)))
        if distance <= radius_km:
            latest[key] = (measurement, station, location, distance)
    grouped: dict[int, dict[str, Any]] = {}
    for (station_id, parameter), (measurement, station, location, distance) in latest.items():
        item = grouped.setdefault(station_id, {"station_id": station_id, "station": station.name,
            "location_id": location.id, "location": location.name, "city": location.locality,
            "country": location.country, "latitude": station.latitude or location.latitude,
            "longitude": station.longitude or location.longitude, "distance_km": round(distance, 2),
            "measurements": {}, "aqi_us": None, "last_updated": None, "source": "openaq"})
        item["measurements"][parameter] = {"value": measurement.value, "unit": measurement.unit,
            "timestamp": measurement.measured_at.isoformat(), "aqi_us": measurement.aqi_us}
        if measurement.aqi_us is not None:
            item["aqi_us"] = max(item["aqi_us"] or 0, measurement.aqi_us)
        if item["last_updated"] is None or measurement.measured_at.isoformat() > item["last_updated"]:
            item["last_updated"] = measurement.measured_at.isoformat()
    results = sorted(grouped.values(), key=lambda x: x["distance_km"])[:limit]
    return {"results": results, "count": len(results),
        "data_status": "available" if results else "no_measurements_nearby",
        "aqi_standard": "US EPA PM2.5/PM10 concentration breakpoints",
        "aqi_note": "Indicative sub-index from the latest reported concentration; not a regulatory 24-hour AQI. Use official local agency AQI for health-critical decisions."}


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
