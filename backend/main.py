from __future__ import annotations

import asyncio
import json
import logging
import math
import random
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Deque, Dict, List, TypedDict

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


class Sensor(TypedDict):
    id: str
    location: str
    baseline_pm25: float
    baseline_pm10: float
    last_seen: datetime
    online: bool
    health: str


class Reading(TypedDict):
    timestamp: datetime
    sensor_id: str
    pm25: float
    pm10: float
    aqi: int


class Alert(TypedDict):
    timestamp: datetime
    title: str
    description: str
    severity: str


class LiveLocation(TypedDict, total=False):
    location: str
    city: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    pm25: float
    aqi: int
    last_updated: str | None


logger = logging.getLogger("aqi-app")

app = FastAPI(title="Smart AQI + Health Risk Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SENSORS: List[Sensor] = [
    {
        "id": "sensor-east",
        "location": "East Garden",
        "baseline_pm25": 35.0,
        "baseline_pm10": 50.0,
        "last_seen": datetime.utcnow(),
        "online": True,
        "health": "good",
    },
    {
        "id": "sensor-downtown",
        "location": "Downtown",
        "baseline_pm25": 42.0,
        "baseline_pm10": 60.0,
        "last_seen": datetime.utcnow(),
        "online": True,
        "health": "good",
    },
    {
        "id": "sensor-campus",
        "location": "Tech Campus",
        "baseline_pm25": 30.0,
        "baseline_pm10": 45.0,
        "last_seen": datetime.utcnow(),
        "online": True,
        "health": "good",
    },
]

READINGS: Deque[Reading] = deque(maxlen=500)
ALERTS: Deque[Alert] = deque(maxlen=20)
OPENAQ_LATEST_URL = "https://api.openaq.org/v2/latest"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
BASE_DIR = Path(__file__).resolve().parent
CITIES_FILE = BASE_DIR.parent / "data" / "cities.json"
try:
    CITY_GAZETTEER: List[Dict] = json.loads(CITIES_FILE.read_text(encoding="utf-8"))
except FileNotFoundError:
    CITY_GAZETTEER = []

try:
    # optional DB integration
    from . import db as db_module  # type: ignore
except Exception:
    db_module = None


def calculate_aqi(pm25: float) -> int:
    """Rough AQI approximation from PM2.5."""
    if pm25 <= 12:
        return int(pm25 * 4)
    if pm25 <= 35.4:
        return int(50 + (pm25 - 12) * (50 / 23.4))
    if pm25 <= 55.4:
        return int(100 + (pm25 - 35.4) * (50 / 20))
    if pm25 <= 150.4:
        return int(150 + (pm25 - 55.4) * (50 / 95))
    return 300


FALLBACK_LOCATIONS: List[LiveLocation] = [
    {
        "location": city["name"],
        "city": city.get("city"),
        "country": city.get("country", "India"),
        "latitude": city.get("lat"),
        "longitude": city.get("lon"),
        "pm25": city.get("pm25", 70),
        "aqi": calculate_aqi(city.get("pm25", 70)),
        "last_updated": None,
    }
    for city in CITY_GAZETTEER
]


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0  # km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c


def search_city_cache(query: str) -> List[Dict]:
    needle = query.lower()
    matches = [
        {
            "name": city["name"],
            "city": city.get("city"),
            "state": city.get("state"),
            "country": city.get("country"),
            "lat": city.get("lat"),
            "lon": city.get("lon"),
            "type": "city",
        }
        for city in CITY_GAZETTEER
        if needle in city["name"].lower()
        or needle in (city.get("city") or "").lower()
        or needle in (city.get("state") or "").lower()
    ]
    return matches[:5]


def status_from_aqi(aqi: int) -> Dict[str, str]:
    if aqi <= 50:
        return {"label": "Best", "badge": "good", "message": "Air is clean and safe."}
    if aqi <= 100:
        return {"label": "Okay", "badge": "moderate", "message": "Moderate risk for sensitive groups."}
    if aqi <= 150:
        return {"label": "Caution", "badge": "warning", "message": "Sensitive groups should limit outdoor time."}
    if aqi <= 200:
        return {"label": "Poor", "badge": "unhealthy", "message": "Consider masks and minimize outdoor activity."}
    return {"label": "Hazardous", "badge": "hazard", "message": "Avoid outdoor exposure; follow advisories."}


def advisory_from_aqi(aqi: int) -> str:
    if aqi <= 50:
        return "Air quality is good. Outdoor activities are safe."
    if aqi <= 100:
        return "Moderate AQI. Sensitive groups should limit exertion."
    if aqi <= 150:
        return "Unhealthy for sensitive groups. Consider wearing a mask."
    if aqi <= 200:
        return "Unhealthy level detected. Reduce prolonged outdoor time."
    if aqi <= 300:
        return "Very unhealthy. Stay indoors and use air purifiers if possible."
    return "Hazardous conditions. Follow emergency health advisories."


def push_alert(reading: Reading) -> None:
    severity = None
    if reading["aqi"] >= 250:
        severity = "critical"
    elif reading["aqi"] >= 200:
        severity = "high"
    elif reading["aqi"] >= 150:
        severity = "medium"

    if not severity:
        return

    ALERTS.appendleft(
        {
            "timestamp": reading["timestamp"],
            "title": f"AQI spike near {reading['sensor_id']}",
            "description": advisory_from_aqi(reading["aqi"]),
            "severity": severity,
        }
    )
    # persist alert to DB if available
    if db_module is not None:
        try:
            db_module.save_alert(
                reading["timestamp"],
                f"AQI spike near {reading['sensor_id']}",
                advisory_from_aqi(reading["aqi"]),
                severity,
            )
        except Exception:
            logger.exception("Failed to persist alert")


def update_sensor_health(sensor: Sensor, reading: Reading) -> None:
    last_entries = [r for r in list(READINGS) if r["sensor_id"] == sensor["id"]][-5:]
    sensor["last_seen"] = reading["timestamp"]
    sensor["online"] = True

    if len(last_entries) < 2:
        sensor["health"] = "good"
        return

    delta = abs(last_entries[-1]["pm25"] - last_entries[-2]["pm25"])
    if delta > 40:
        sensor["health"] = "attention"
    else:
        sensor["health"] = "good"


def generate_reading(sensor: Sensor) -> Reading:
    pm25 = max(
        5.0,
        random.gauss(sensor["baseline_pm25"], 8),
    )
    pm10 = max(
        10.0,
        random.gauss(sensor["baseline_pm10"], 12),
    )

    reading: Reading = {
        "timestamp": datetime.utcnow(),
        "sensor_id": sensor["id"],
        "pm25": round(pm25, 2),
        "pm10": round(pm10, 2),
        "aqi": int(calculate_aqi(pm25)),
    }
    # persist reading to DB if available
    if db_module is not None:
        try:
            db_module.save_reading(
                reading["timestamp"], reading["sensor_id"], reading["pm25"], reading["pm10"], reading["aqi"]
            )
        except Exception:
            logger.exception("Failed to persist reading")
    return reading


async def simulation_loop() -> None:
    while True:
        for sensor in SENSORS:
            # 10% chance sensor skips a reading to mimic downtime.
            if random.random() < 0.1:
                continue
            reading = generate_reading(sensor)
            READINGS.append(reading)
            update_sensor_health(sensor, reading)
            push_alert(reading)

        now = datetime.utcnow()
        for sensor in SENSORS:
            if now - sensor["last_seen"] > timedelta(seconds=45):
                sensor["online"] = False
                sensor["health"] = "offline"

        await asyncio.sleep(5)


@app.on_event("startup")
async def startup_event() -> None:
    for sensor in SENSORS:
        READINGS.append(generate_reading(sensor))
    # initialize DB (if available) and start simulation
    if db_module is not None:
        try:
            db_module.init_db()
        except Exception:
            logger.exception("DB init failed")
    asyncio.create_task(simulation_loop())


async def fetch_live_locations(location: str) -> List[LiveLocation]:
    params = {
        "limit": 5,
        "page": 1,
        "sort": "desc",
        "order_by": "lastUpdated",
        "parameter": "pm25",
        "search": location,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(OPENAQ_LATEST_URL, params=params)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("OpenAQ request failed (%s): %s", location, exc)
        return [
            loc
            for loc in FALLBACK_LOCATIONS
            if location.lower() in (loc.get("location") or "").lower()
            or location.lower() in (loc.get("city") or "").lower()
        ]

    payload = response.json()
    results: List[LiveLocation] = []
    for entry in payload.get("results", []):
        measurement = next(
            (m for m in entry.get("measurements", []) if m.get("parameter") == "pm25"),
            None,
        )
        if not measurement:
            continue
        pm25 = float(measurement.get("value", 0))
        coords = entry.get("coordinates") or {}
        results.append(
            {
                "location": entry.get("location"),
                "city": entry.get("city"),
                "country": entry.get("country"),
                "latitude": coords.get("latitude"),
                "longitude": coords.get("longitude"),
                "pm25": round(pm25, 2),
                "aqi": calculate_aqi(pm25),
                "last_updated": measurement.get("lastUpdated"),
            }
        )

    if results:
        return results

    return [
        loc
        for loc in FALLBACK_LOCATIONS
        if location.lower() in (loc.get("location") or "").lower()
        or location.lower() in (loc.get("city") or "").lower()
    ]


async def geocode_locations(query: str) -> List[Dict]:
    headers = {
        "User-Agent": "SmartAQI-Demo/0.1 (contact: student@example.com)",
    }
    params = {
        "q": query,
        "format": "json",
        "limit": 5,
        "addressdetails": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            response = await client.get(NOMINATIM_SEARCH_URL, params=params)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Geocode request failed: %s", exc)
        return search_city_cache(query)

    results = []
    for place in response.json():
        lat = place.get("lat")
        lon = place.get("lon")
        if not lat or not lon:
            continue
        title = place.get("display_name", "")
        city = place.get("address", {}).get("city") or place.get("address", {}).get(
            "town"
        )
        state = place.get("address", {}).get("state")
        country = place.get("address", {}).get("country")
        results.append(
            {
                "name": title,
                "city": city,
                "state": state,
                "country": country,
                "lat": float(lat),
                "lon": float(lon),
                "type": place.get("type"),
            }
        )
    if results:
        return results

    return search_city_cache(query)


async def fetch_nearest_aqi(lat: float, lon: float) -> List[LiveLocation]:
    params = {
        "coordinates": f"{lat},{lon}",
        "radius": 50000,
        "order_by": "distance",
        "sort": "asc",
        "parameter": "pm25",
        "limit": 5,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(OPENAQ_LATEST_URL, params=params)
        response.raise_for_status()

    results: List[LiveLocation] = []
    for entry in response.json().get("results", []):
        measurement = next(
            (m for m in entry.get("measurements", []) if m.get("parameter") == "pm25"),
            None,
        )
        if not measurement:
            continue
        coords = entry.get("coordinates") or {}
        pm25 = float(measurement.get("value", 0))
        results.append(
            {
                "location": entry.get("location"),
                "city": entry.get("city"),
                "country": entry.get("country"),
                "latitude": coords.get("latitude"),
                "longitude": coords.get("longitude"),
                "pm25": round(pm25, 2),
                "aqi": calculate_aqi(pm25),
                "last_updated": measurement.get("lastUpdated"),
            }
        )

    return results


def summarize_city(city: LiveLocation) -> Dict[str, object]:
    summary = status_from_aqi(int(city["aqi"]))
    return {
        "location": city.get("location"),
        "city": city.get("city"),
        "country": city.get("country"),
        "latitude": city.get("latitude"),
        "longitude": city.get("longitude"),
        "aqi": city["aqi"],
        "pm25": city["pm25"],
        **summary,
    }


@app.get("/api/aqi/latest")
def get_latest() -> Dict[str, object]:
    if not READINGS:
        for sensor in SENSORS:
            READINGS.append(generate_reading(sensor))
    latest = READINGS[-1]
    return {
        "reading": latest,
        "advisory": advisory_from_aqi(latest["aqi"]),
    }


@app.get("/api/aqi/history")
def get_history(limit: int = 30) -> Dict[str, List[Reading]]:
    limit = max(5, min(limit, 100))
    history = list(READINGS)[-limit:]
    return {"readings": history}


@app.get("/api/sensors/status")
def get_sensors() -> Dict[str, List[Sensor]]:
    return {"sensors": SENSORS}


@app.get("/api/alerts")
def get_alerts() -> Dict[str, List[Alert]]:
    return {"alerts": list(ALERTS)}


@app.get("/api/geocode")
async def geocode_endpoint(
    query: str = Query(..., min_length=2, max_length=80, description="Place name"),
) -> Dict[str, List[Dict]]:
    results = await geocode_locations(query)
    if not results:
        raise HTTPException(status_code=404, detail="No matching locations found.")
    return {"results": results}


@app.get("/api/cities/sample")
def sample_cities(count: int = 3) -> Dict[str, List[Dict]]:
    if not FALLBACK_LOCATIONS:
        raise HTTPException(status_code=503, detail="City dataset unavailable.")
    count = max(1, min(count, len(FALLBACK_LOCATIONS)))
    selected = random.sample(FALLBACK_LOCATIONS, count)
    return {"cities": [summarize_city(city) for city in selected]}


@app.get("/api/aqi/nearest")
async def nearest_aqi(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> Dict[str, List[LiveLocation]]:
    try:
        results = await fetch_nearest_aqi(lat, lon)
    except httpx.HTTPError as exc:
        logger.warning("Nearest AQI fetch failed: %s", exc)
        results = []

    if results:
        return {"results": results}

    # fall back to closest known location
    fallback_sorted = sorted(
        FALLBACK_LOCATIONS,
        key=lambda loc: haversine_distance(
            lat,
            lon,
            float(loc.get("latitude") or 0),
            float(loc.get("longitude") or 0),
        ),
    )
    if fallback_sorted:
        return {"results": fallback_sorted[:1]}

    raise HTTPException(status_code=502, detail="Unable to fetch AQI data.")


@app.get("/api/aqi/search")
async def search_live_aqi(location: str = Query(..., min_length=2, max_length=60)) -> Dict[str, List[LiveLocation]]:
    try:
        results = await fetch_live_locations(location)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Upstream API error: {exc.__class__.__name__}"
        ) from exc

    if not results:
        raise HTTPException(status_code=404, detail="No monitoring stations found.")

    return {"results": results}


app.mount(
    "/",
    StaticFiles(directory="frontend", html=True),
    name="frontend",
)

