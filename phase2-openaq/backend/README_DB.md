# PostgreSQL database (Phase 1)

The backend now uses PostgreSQL by default and SQLAlchemy models for locations, monitoring stations, pollutant measurements, alerts, and API provider status. No random sensor readings are generated or returned as real measurements.

## Start PostgreSQL with Docker

From the project root:

```bash
docker compose up -d postgres
```

Copy `.env.example` to `.env` and set a strong local password in `DATABASE_URL`. The example Docker Compose credentials are for local development only.

## Install and run (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r backend\requirements.txt
Copy-Item .env.example .env
uvicorn backend.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation and `http://127.0.0.1:8000/api/health` to verify the database connection. Tables are created on first startup. For deployed environments, add Alembic migrations before schema changes.

## Tables

- `locations`: geocoded places and coordinates.
- `stations`: provider monitoring stations associated with locations.
- `measurements`: timestamped pollutant measurements with units, source, and optional US AQI value. A uniqueness constraint prevents duplicate station/parameter/time records.
- `alerts`: persistent pollution alerts.
- `api_sources`: external provider status and last error/success.

## Phase 1 endpoints

- `GET /api/health`
- `GET /api/locations/search?query=Jabalpur` (global Nominatim geocoding; results are saved)
- `GET /api/locations`
- `GET /api/stations`
- `GET /api/aqi/map` (latest stored PM2.5 value per station)
- `GET /api/aqi/history/{station_id}`
- `GET /api/alerts`

AQI map and history endpoints remain empty until Phase 2 connects an authenticated measurement provider and ingests real observations. The old random simulation and retired OpenAQ v2 integration have been removed.
