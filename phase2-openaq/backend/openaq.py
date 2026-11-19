from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db

logger = logging.getLogger("aqi-app.openaq")
BASE_URL = os.getenv("OPENAQ_BASE_URL", "https://api.openaq.org/v3").rstrip("/")

# US EPA AQI breakpoints. PM2.5 uses the 2024 revised breakpoints.
PM25_BREAKPOINTS = [
    (0.0, 9.0, 0, 50), (9.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200), (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 400), (325.5, 500.4, 401, 500),
]
PM10_BREAKPOINTS = [
    (0, 54, 0, 50), (55, 154, 51, 100), (155, 254, 101, 150),
    (255, 354, 151, 200), (355, 424, 201, 300),
    (425, 604, 301, 500),
]


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, dict):
        value = value.get("utc") or value.get("local")
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _aqi_from_breakpoints(value: float, breakpoints: list[tuple[float, float, int, int]], truncate_digits: int = 0) -> int | None:
    if value < 0:
        return None
    factor = 10 ** truncate_digits
    concentration = int(value * factor) / factor
    for c_low, c_high, i_low, i_high in breakpoints:
        if c_low <= concentration <= c_high:
            result = (i_high - i_low) / (c_high - c_low) * (concentration - c_low) + i_low
            return max(0, min(500, int(result + 0.5)))
    return 500 if concentration > breakpoints[-1][1] else None


def calculate_us_aqi(parameter: str, value: float, unit: str | None) -> int | None:
    """Calculate a US EPA sub-index only for supported mass-concentration units.

    OpenAQ generally reports PM in µg/m³. Unknown or incompatible units return None
    rather than silently producing a misleading AQI.
    """
    p = parameter.lower().replace(".", "").replace("_", "")
    u = (unit or "").lower().replace("μ", "µ").replace("³", "3").replace(" ", "")
    normalized = float(value)
    if u in {"mg/m3", "mg/m^3"}:
        normalized *= 1000
    elif u not in {"µg/m3", "ug/m3", "µg/m^3", "ug/m^3", ""}:
        return None
    if p in {"pm25", "pm2.5", "pm2_5"}:
        return _aqi_from_breakpoints(normalized, PM25_BREAKPOINTS, 1)
    if p in {"pm10"}:
        return _aqi_from_breakpoints(normalized, PM10_BREAKPOINTS, 0)
    return None


def _parameter_name(sensor: dict[str, Any]) -> tuple[str | None, str | None]:
    parameter = sensor.get("parameter") or {}
    name = parameter.get("name") or sensor.get("parameterName")
    unit = parameter.get("units") or sensor.get("units")
    if not name:
        return None, unit
    name = str(name).lower().replace(".", "").replace("_", "")
    aliases = {"pm25": "pm25", "pm2.5": "pm25", "pm2_5": "pm25", "pm10": "pm10",
               "no2": "no2", "o3": "o3", "so2": "so2", "co": "co", "bc": "bc"}
    return aliases.get(name), unit


class OpenAQClient:
    def __init__(self, api_key: str | None = None, timeout: float = 20.0):
        self.api_key = api_key if api_key is not None else os.getenv("OPENAQ_API_KEY", "")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("OPENAQ_API_KEY is not configured. Add your OpenAQ API key to .env and restart the backend.")
        return {"X-API-Key": self.api_key, "Accept": "application/json"}

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await client.get(f"{BASE_URL}{path}", params=params)
        if response.status_code == 401:
            raise RuntimeError("OpenAQ rejected the API key (401). Check OPENAQ_API_KEY in .env.")
        if response.status_code == 429:
            raise RuntimeError("OpenAQ rate limit reached (429). Wait before retrying.")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("OpenAQ returned an unexpected response format.")
        return payload

    async def discover_nearby(self, lat: float, lon: float, radius_km: float = 25, limit: int = 100) -> list[dict[str, Any]]:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Latitude or longitude is out of range.")
        if not (1 <= radius_km <= 25):
            raise ValueError("OpenAQ discovery radius must be between 1 and 25 km.")
        headers = self._headers()
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            payload = await self._get(client, "/locations", {
                "coordinates": f"{lat:.4f},{lon:.4f}",
                "radius": int(radius_km * 1000), "limit": min(max(limit, 1), 100), "page": 1,
            })
            return payload.get("results") or []

    async def fetch_location_latest(self, location_id: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        headers = self._headers()
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            location_payload = await self._get(client, f"/locations/{location_id}")
            sensors_payload = await self._get(client, f"/locations/{location_id}/sensors", {"limit": 100, "page": 1})
            latest_payload = await self._get(client, f"/locations/{location_id}/latest", {"limit": 100, "page": 1})
        location_results = location_payload.get("results") or []
        location = location_results[0] if location_results else {}
        return location, sensors_payload.get("results") or [], latest_payload.get("results") or []


def _coords(obj: dict[str, Any]) -> tuple[float | None, float | None]:
    coords = obj.get("coordinates") or {}
    lat = coords.get("latitude")
    lon = coords.get("longitude")
    if lat is None or lon is None:
        return None, None
    return float(lat), float(lon)


def _upsert_location(session: Session, raw: dict[str, Any]) -> db.Location:
    external_id = str(raw.get("id"))
    location = session.scalar(select(db.Location).where(db.Location.source == "openaq", db.Location.source_location_id == external_id))
    lat, lon = _coords(raw)
    if lat is None or lon is None:
        raise ValueError(f"OpenAQ location {external_id} has no coordinates")
    country = raw.get("country") or {}
    if location is None:
        location = db.Location(name=(raw.get("name") or f"OpenAQ location {external_id}")[:255],
            locality=raw.get("locality"), region=None, country=country.get("name"),
            country_code=country.get("code"), latitude=lat, longitude=lon,
            source="openaq", source_location_id=external_id)
        session.add(location)
    else:
        location.name = (raw.get("name") or location.name)[:255]
        location.locality = raw.get("locality") or location.locality
        location.country = country.get("name") or location.country
        location.country_code = country.get("code") or location.country_code
        location.latitude, location.longitude = lat, lon
    session.flush()
    return location


def _persist_location_data(session: Session, raw_location: dict[str, Any], sensors: list[dict[str, Any]], latest: list[dict[str, Any]]) -> dict[str, int]:
    location = _upsert_location(session, raw_location)
    sensor_by_id: dict[str, dict[str, Any]] = {str(s.get("id")): s for s in sensors if s.get("id") is not None}
    station_external_id = str(raw_location.get("id"))
    station = session.scalar(select(db.Station).where(db.Station.source == "openaq", db.Station.source_station_id == station_external_id))
    lat, lon = _coords(raw_location)
    if station is None:
        station = db.Station(location_id=location.id, name=(raw_location.get("name") or location.name)[:255],
            source="openaq", source_station_id=station_external_id, latitude=lat, longitude=lon, is_active=True)
        session.add(station)
    else:
        station.location_id = location.id
        station.name = (raw_location.get("name") or station.name)[:255]
        station.latitude, station.longitude, station.is_active = lat, lon, True
    session.flush()
    inserted = 0
    for item in latest:
        sensor_id = str(item.get("sensorsId"))
        sensor = sensor_by_id.get(sensor_id, {})
        parameter, unit = _parameter_name(sensor)
        if not parameter or item.get("value") is None:
            continue
        value = float(item["value"])
        measured_at = _utc_datetime(item.get("datetime"))
        existing = session.scalar(select(db.Measurement).where(
            db.Measurement.station_id == station.id,
            db.Measurement.parameter == parameter,
            db.Measurement.measured_at == measured_at,
        ))
        if existing:
            existing.value, existing.unit = value, unit
            existing.aqi_us = calculate_us_aqi(parameter, value, unit)
            existing.fetched_at = datetime.now(timezone.utc)
            continue
        session.add(db.Measurement(station_id=station.id, parameter=parameter, value=value,
            unit=unit, measured_at=measured_at, fetched_at=datetime.now(timezone.utc),
            source="openaq", aqi_us=calculate_us_aqi(parameter, value, unit)))
        inserted += 1
    station.last_seen_at = datetime.now(timezone.utc) if latest else station.last_seen_at
    return {"location_id": location.id, "station_id": station.id, "measurements_saved": inserted, "measurements_returned": len(latest)}


async def refresh_nearby(session: Session, lat: float, lon: float, radius_km: float = 25, limit: int = 100) -> dict[str, Any]:
    client = OpenAQClient()
    discovered = await client.discover_nearby(lat, lon, radius_km, limit)
    summaries: list[dict[str, int]] = []
    failures: list[dict[str, str]] = []
    for item in discovered:
        external_id = item.get("id")
        if external_id is None:
            continue
        try:
            full_location, sensors, latest = await client.fetch_location_latest(int(external_id))
            raw_location = full_location or item
            summaries.append(_persist_location_data(session, raw_location, sensors, latest))
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.warning("Could not sync OpenAQ location %s: %s", external_id, exc)
            failures.append({"location_id": str(external_id), "error": str(exc)[:300]})
    session.commit()
    source = session.scalar(select(db.ApiSource).where(db.ApiSource.name == "OpenAQ"))
    if source is None:
        source = db.ApiSource(name="OpenAQ", base_url=BASE_URL, is_enabled=True)
        session.add(source)
    source.base_url = BASE_URL
    source.last_success_at = datetime.now(timezone.utc) if summaries else source.last_success_at
    source.last_error = (f"{len(failures)} location(s) failed; first: {failures[0]['error']}" if failures else None)
    session.commit()
    return {"provider": "OpenAQ v3", "discovered_locations": len(discovered),
        "locations_synced": len(summaries), "measurements_saved": sum(x["measurements_saved"] for x in summaries),
        "failures": failures, "results": summaries,
        "data_status": "available" if summaries else "no_monitoring_stations_found"}
