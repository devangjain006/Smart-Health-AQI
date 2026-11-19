# Smart Health AQI

A full-stack air-quality monitoring project using FastAPI, SQLAlchemy and PostgreSQL.

## Implemented phases

### Phase 1 — PostgreSQL foundation
- PostgreSQL database with SQLAlchemy models for locations, stations, measurements, alerts and API source status.
- Global place search through OpenStreetMap Nominatim; searches are persisted.
- Database-backed station, map, history and alert endpoints.
- No synthetic AQI values are used as live measurements.

### Phase 2 — OpenAQ API v3 ingestion
- API key authentication through the `X-API-Key` header.
- Nearby station discovery using coordinates and a radius (maximum 25 km per OpenAQ request).
- Reads OpenAQ location metadata, sensor metadata and latest measurements.
- Persists stations and measurements in PostgreSQL, with duplicate protection.
- Calculates indicative US EPA PM2.5 and PM10 concentration sub-indices when the unit is supported. Because the ingestion endpoint stores the latest reported sample rather than a verified 24-hour average, these are not regulatory 24-hour AQI values. Other pollutants are stored but not assigned PM breakpoints.
- Provider status endpoint and explicit refresh endpoint.

**Coverage note:** OpenAQ aggregates publicly available data; it does not contain monitoring data for every place on Earth. A global place may resolve through geocoding but have no nearby monitoring station. Such locations return an explicit no-data status.

## Setup (Windows PowerShell)

1. Install Docker Desktop, then start PostgreSQL from the project root:
   ```powershell
   docker compose up -d postgres
   ```
2. Create a Python environment and install dependencies:
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   py -m pip install -r backend\requirements.txt
   Copy-Item .env.example .env
   ```
3. Edit `.env`: set a strong local database password consistently with `docker-compose.yml`, and add your OpenAQ API key to `OPENAQ_API_KEY`. Never commit `.env`.
4. Start the API from the project root:
   ```powershell
   uvicorn backend.main:app --reload
   ```
5. Open `http://127.0.0.1:8000` and API docs at `http://127.0.0.1:8000/docs`.

## Free Render demo deployment

The repository root includes a Render Blueprint for this project. In Render, create a new Blueprint from this repository and deploy the `smart-health-aqi-openaq` web service on the free plan. The service uses SQLite under `/tmp` to avoid a paid database; Render's free filesystem is ephemeral, so saved locations and measurements can be lost when the service restarts or redeploys.

To enable OpenAQ ingestion, add `OPENAQ_API_KEY` as a secret environment variable in the Render service settings. Do not commit the key. Without it, the dashboard and API remain available, but live OpenAQ refreshes are disabled. The service health endpoint is `/api/health`.

## Phase 2 API endpoints

- `GET /api/providers/openaq/status` — checks whether an API key is configured and reports last provider sync.
- `POST /api/aqi/refresh/nearby?lat=28.6139&lon=77.2090&radius_km=25` — discovers nearby OpenAQ locations and stores latest readings.
- `GET /api/aqi/location?lat=28.6139&lon=77.2090&radius_km=25` — returns stored latest measurements grouped by station.
- Existing `GET /api/aqi/map`, `/api/aqi/nearest`, `/api/aqi/history/{station_id}`, `/api/stations` and `/api/alerts` remain available.

The refresh endpoint is deliberately explicit in Phase 2. It does not continuously poll in the background; scheduled refresh and map-wide discovery are planned for a later phase.

## AQI methodology

The stored `aqi_us` value is an indicative pollutant sub-index, not a composite index across every pollutant and not an official regulatory AQI. The current ingestion stores the latest reported sample, while regulatory PM AQI calculations are based on 24-hour concentrations. PM2.5 uses the revised US EPA 2024 concentration breakpoints and truncation to 0.1 µg/m³; PM10 uses US EPA concentration breakpoints and integer truncation. Unknown or incompatible units produce `null` AQI rather than a guessed conversion. For health-critical decisions, use the relevant local environmental agency's official AQI.
