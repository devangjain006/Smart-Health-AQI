# Smart City AQI + Health Risk Demo

Lightweight project that simulates a miniature air-quality sensor network, predicts dangerous spikes, and shares health advisories through a simple dashboard.

## Features

- FastAPI backend that streams simulated sensor readings (three sensors, random outages, anomaly flags).
- Auto-generated alerts when AQI crosses unhealthy thresholds.
- REST endpoints for latest AQI, historical timeline, sensor health, alerts, and live search.
- Static frontend (vanilla JS + Chart.js + Leaflet) that visualises AQI trends, lets you pin a "home" city, shows personalised advisories, and provides a map-enabled search that falls back to a curated Tier-1/2 + Madhya Pradesh city list when offline.

## Requirements

- Python 3.10+
- Pip (already bundled with Python 3.13 on Windows).

## Install & Run

```powershell
cd C:\Users\LENOVO\project
python -m pip install -r backend\requirements.txt
uvicorn backend.main:app --reload
```

Then open `http://127.0.0.1:8000` in your browser to view the dashboard. Keep the machine online so the live search can reach both OpenAQ and OpenStreetMap (Nominatim) APIs.

## Project Structure

- `backend/` – FastAPI app with the simulation loop and REST endpoints.
- `frontend/` – Static HTML/CSS/JS dashboard served by FastAPI.
- `data/cities.json` – Gazetteer covering Tier 1/2 India cities plus every Madhya Pradesh city (with coordinates + typical PM2.5 baselines) so location lookup still works offline.
- `README.md` – This guide.

## Next ideas

- Replace the simulator with real IoT readings or CSV imports.
- Persist readings in SQLite/Postgres and add authentication for operators.
- Push notifications (email/SMS) whenever a critical alert is generated.
- Cache live OpenAQ responses or layer in additional providers (AirNow, WAQI).

