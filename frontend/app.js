const formatter = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

const chartCtx = document.getElementById("aqi-chart").getContext("2d");
const chartData = {
  labels: [],
  datasets: [
    {
      label: "AQI",
      data: [],
      borderColor: "#087e78",
      backgroundColor: "rgba(8, 126, 120, 0.12)",
      fill: true,
      tension: 0.35,
    },
  ],
};

const aqiChart = new Chart(chartCtx, {
  type: "line",
  data: chartData,
  options: {
    responsive: true,
    animation: false,
    plugins: {
      legend: { display: false },
    },
    scales: {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: { color: "#85939c", maxTicksLimit: 7 },
      },
      y: {
        beginAtZero: true,
        suggestedMax: 250,
        grid: { color: "rgba(225, 233, 235, 0.8)" },
        border: { display: false },
        ticks: { color: "#85939c", precision: 0 },
      },
    },
  },
});

const locationInput = document.getElementById("location-input");
const locationButton = document.getElementById("location-button");
const locationResults = document.getElementById("location-results");
const changeHomeBtn = document.getElementById("change-home");
const timelineHint = document.getElementById("timeline-hint");
let mapInstance;
let markerLayer;

if (document.getElementById("map")) {
  mapInstance = L.map("map").setView([20.5937, 78.9629], 4);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(mapInstance);
  markerLayer = L.layerGroup().addTo(mapInstance);
}

const HOME_KEY = "smartAQI.home";
const HOME_HISTORY_KEY = "smartAQI.history";
let homeCity = null;
let homeHistory = [];
let latestHomeReading = null;
let sampleCities = [];

try {
  homeCity = JSON.parse(localStorage.getItem(HOME_KEY));
  homeHistory = JSON.parse(localStorage.getItem(HOME_HISTORY_KEY)) || [];
} catch {
  homeCity = null;
  homeHistory = [];
}
if (homeCity) {
  homeCity.lat = Number(homeCity.lat);
  homeCity.lon = Number(homeCity.lon);
}

function persistHomeHistory() {
  localStorage.setItem(HOME_HISTORY_KEY, JSON.stringify(homeHistory));
}

function statusForAqi(aqi) {
  if (aqi <= 50)
    return { label: "Best", badge: "good", advisory: "Air is clean and safe." };
  if (aqi <= 100)
    return {
      label: "Okay",
      badge: "moderate",
      advisory: "Moderate AQI. Sensitive groups take it easy.",
    };
  if (aqi <= 150)
    return {
      label: "Caution",
      badge: "warning",
      advisory: "Limit long outdoor exposure, especially for kids.",
    };
  if (aqi <= 200)
    return {
      label: "Poor",
      badge: "unhealthy",
      advisory: "Wear a mask and minimize outdoor activity.",
    };
  return {
    label: "Hazardous",
    badge: "hazard",
    advisory: "Stay indoors and follow emergency health advisories.",
  };
}

async function fetchJSON(url) {
  const response = await fetch(url);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const detail = await response.json();
      if (detail?.detail) {
        message += `: ${detail.detail}`;
      }
    } catch {
      /* ignore parse errors */
    }
    throw new Error(message);
  }
  return response.json();
}

function updateHomeCard(reading) {
  const title = document.getElementById("home-title");
  const advisory = document.getElementById("home-advisory");
  const status = document.getElementById("home-status");
  const label = homeCity?.label || "Set your home city";
  title.textContent = homeCity ? label : "Set your home city";
  changeHomeBtn.hidden = !homeCity;

  if (reading) {
    const aqiStatus = statusForAqi(reading.aqi);
    document.getElementById("home-aqi").textContent = reading.aqi;
    status.textContent = aqiStatus.label;
    status.className = `aqi-status ${aqiStatus.badge}`;
    document.getElementById("home-city").textContent = label;
    document.getElementById("home-pm25").textContent = `${reading.pm25} µg/m³`;
    document.getElementById("home-pm10").textContent = reading.pm10
      ? `${reading.pm10} µg/m³`
      : "n/a";
    document.getElementById("home-updated").textContent = formatter.format(
      new Date(reading.timestamp),
    );
    advisory.textContent = aqiStatus.advisory;
  } else {
    document.getElementById("home-aqi").textContent = "--";
    status.textContent = "Awaiting location";
    status.className = "aqi-status";
    document.getElementById("home-city").textContent = "--";
    document.getElementById("home-pm25").textContent = "--";
    document.getElementById("home-pm10").textContent = "--";
    document.getElementById("home-updated").textContent = "--";
    advisory.textContent = "Search for a city below and set it as home.";
  }
}

function updateTimelineChart(readings, label = "AQI") {
  if (!readings?.length) {
    timelineHint.textContent = homeCity
      ? "Waiting for home data..."
      : "Set a home city to start tracking.";
    aqiChart.data.labels = [];
    aqiChart.data.datasets[0].data = [];
    aqiChart.update("none");
    return;
  }
  timelineHint.textContent = `Showing last ${readings.length} samples`;
  aqiChart.data.labels = readings.map((entry) =>
    formatter.format(new Date(entry.timestamp)),
  );
  aqiChart.data.datasets[0].label = label;
  aqiChart.data.datasets[0].data = readings.map((entry) => entry.aqi);
  aqiChart.update("none");
}

function updateAdvicePanel() {
  const box = document.getElementById("alerts-advice");
  if (!latestHomeReading) {
    box.textContent = "Add a home city to receive personalized advice.";
    return;
  }
  const status = statusForAqi(latestHomeReading.aqi);
  const goOut =
    latestHomeReading.aqi <= 100
      ? "Low risk — outdoor activities are safe."
      : latestHomeReading.aqi <= 150
        ? "Use masks if you go out and keep trips short."
        : "Avoid outdoor activity unless necessary.";
  box.innerHTML = `
    <strong>${status.label} air (${latestHomeReading.aqi} AQI)</strong><br/>
    ${status.advisory}<br/>
    ${goOut}
  `;
}

function renderRiskGrid() {
  const container = document.getElementById("risk-grid");
  if (!container) return;
  const cards = [];
  if (latestHomeReading && homeCity) {
    cards.push({
      city: homeCity.label,
      aqi: latestHomeReading.aqi,
      status: statusForAqi(latestHomeReading.aqi),
    });
  }
  sampleCities.forEach((city) => {
    cards.push({
      city: city.city || city.location,
      aqi: city.aqi,
      status: {
        label: city.label,
        badge: city.badge || "moderate",
        advisory: city.message,
      },
    });
  });

  if (!cards.length) {
    container.innerHTML = '<p class="muted">Collecting data...</p>';
    return;
  }

  container.innerHTML = cards
    .slice(0, 4)
    .map(
      (card) => `
        <div class="risk-card">
          <h3>${card.city}</h3>
          <span class="badge ${card.status.badge}">${card.status.label}</span>
          <strong>${card.aqi} AQI</strong>
          <p class="muted">${card.status.advisory}</p>
        </div>
      `,
    )
    .join("");
}

function addHomeHistory(entry) {
  homeHistory.push(entry);
  if (homeHistory.length > 60) {
    homeHistory.shift();
  }
  persistHomeHistory();
  updateTimelineChart(homeHistory, homeCity?.label || "AQI");
}

async function fetchHomeMetrics() {
  if (!homeCity) {
    updateHomeCard(null);
    updateTimelineChart([]);
    updateAdvicePanel();
    renderRiskGrid();
    return;
  }
  try {
    const data = await fetchJSON(
      `/api/aqi/nearest?lat=${homeCity.lat}&lon=${homeCity.lon}`,
    );
    if (!data.results?.length) return;
    const reading = data.results[0];
    const entry = {
      timestamp: new Date().toISOString(),
      aqi: reading.aqi,
      pm25: reading.pm25,
      pm10: reading.pm10 || null,
      location: reading.location,
    };
    latestHomeReading = entry;
    updateHomeCard(entry);
    addHomeHistory(entry);
    updateAdvicePanel();
    renderRiskGrid();
  } catch (error) {
    console.error(error);
  }
}

function clearHome() {
  homeCity = null;
  homeHistory = [];
  localStorage.removeItem(HOME_KEY);
  localStorage.removeItem(HOME_HISTORY_KEY);
  latestHomeReading = null;
  updateHomeCard(null);
  updateTimelineChart([]);
  updateAdvicePanel();
  renderRiskGrid();
}

function setHomeCity(city) {
  homeCity = {
    label: city.label,
    lat: Number(city.lat),
    lon: Number(city.lon),
  };
  localStorage.setItem(HOME_KEY, JSON.stringify(homeCity));
  homeHistory = [];
  persistHomeHistory();
  fetchHomeMetrics();
  locationResults.innerHTML = `<li class="muted">Set ${city.label} as home. Fetching live AQI...</li>`;
}

function renderGeocodeSuggestions(places) {
  if (!locationResults) return;
  if (!places.length) {
    locationResults.innerHTML =
      '<li class="muted">No matching locations. Try a nearby city.</li>';
    return;
  }
  locationResults.innerHTML = places
    .map(
      (place) => `
        <li class="location-item" data-lat="${place.lat}" data-lon="${place.lon}" data-label="${place.name}">
          <h3>${place.city || place.name.split(",")[0]}</h3>
          <span>${place.state || ""} ${place.country || ""}</span>
          <div class="actions">
            <button class="btn-view" data-action="view">View live AQI</button>
            <button class="btn-home" data-action="home">Set as home</button>
          </div>
        </li>
      `,
    )
    .join("");
  if (markerLayer) markerLayer.clearLayers();
  if (mapInstance) {
    mapInstance.setView([places[0].lat, places[0].lon], 6);
  }
}

function renderStationResults(results, label) {
  if (!locationResults) return;
  if (!results.length) {
    locationResults.innerHTML =
      '<li class="muted">No monitoring stations found near that location.</li>';
    return;
  }
  locationResults.innerHTML = results
    .map(
      (item) => `
        <li class="location-item" data-lat="${item.latitude}" data-lon="${item.longitude}" data-label="${item.location || label}">
          <h3>${item.location || label}</h3>
          <span>${item.city || "Unknown city"}, ${item.country || ""}</span>
          <span>PM2.5: <strong>${item.pm25} µg/m³</strong> · AQI ${item.aqi}</span>
          <span>Updated: ${
            item.last_updated ? new Date(item.last_updated).toLocaleString() : "n/a"
          }</span>
          <div class="actions">
            <button class="btn-home" data-action="home">Set as home</button>
          </div>
        </li>
      `,
    )
    .join("");

  if (!markerLayer || !mapInstance) return;
  markerLayer.clearLayers();
  const bounds = [];
  results.forEach((item) => {
    if (typeof item.latitude !== "number" || typeof item.longitude !== "number") {
      return;
    }
    bounds.push([item.latitude, item.longitude]);
    L.circleMarker([item.latitude, item.longitude], {
      radius: 9,
      color: colorForAqi(item.aqi),
      fillColor: colorForAqi(item.aqi),
      fillOpacity: 0.8,
      weight: 2,
    })
      .bindPopup(
        `<strong>${item.location}</strong><br/>AQI ${item.aqi}<br/>PM2.5 ${item.pm25} µg/m³`,
      )
      .addTo(markerLayer);
  });
  if (bounds.length) {
    mapInstance.fitBounds(bounds, { padding: [20, 20] });
  }
}

async function handleLocationSearch() {
  if (!locationInput || !locationResults) return;
  const query = locationInput.value.trim();
  if (!query) {
    locationResults.innerHTML =
      '<li class="muted">Enter a city or station name first.</li>';
    return;
  }
  locationResults.innerHTML =
    '<li class="muted">Searching for place suggestions...</li>';
  try {
    const data = await fetchJSON(
      `/api/geocode?query=${encodeURIComponent(query)}`,
    );
    renderGeocodeSuggestions(data.results || []);
  } catch (error) {
    console.error(error);
    locationResults.innerHTML = `<li class="muted">${error.message}</li>`;
  }
}

async function fetchNearestAqi(lat, lon, label) {
  locationResults.innerHTML = `<li class="muted">Fetching AQI near ${label}...</li>`;
  try {
    const data = await fetchJSON(
      `/api/aqi/nearest?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`,
    );
    renderStationResults(data.results || [], label);
  } catch (error) {
    console.error(error);
    locationResults.innerHTML = `<li class="muted">${error.message}</li>`;
  }
}

function colorForAqi(aqi) {
  if (aqi <= 50) return "#2ecc71";
  if (aqi <= 100) return "#f1c40f";
  if (aqi <= 150) return "#e67e22";
  if (aqi <= 200) return "#e74c3c";
  if (aqi <= 300) return "#8e44ad";
  return "#7f1d1d";
}

function renderNetworkAlerts(data) {
  const list = document.getElementById("alert-list");
  const alerts = data.alerts;
  if (!alerts.length) {
    list.innerHTML = "<li>No spikes detected across the network.</li>";
    return;
  }
  list.innerHTML = alerts
    .map(
      (alert) => `
        <li class="alert-item ${alert.severity}">
          <time>${formatter.format(new Date(alert.timestamp))}</time>
          <strong>${alert.title}</strong>
          <p>${alert.description}</p>
        </li>
      `,
    )
    .join("");
}

async function refreshNetworkData() {
  try {
    const [latest, alerts, history] = await Promise.all([
      fetchJSON("/api/aqi/latest"),
      fetchJSON("/api/alerts"),
      fetchJSON("/api/aqi/history?limit=30"),
    ]);
    if (!homeCity) {
      updateHomeCard({
        aqi: latest.reading.aqi,
        pm25: latest.reading.pm25,
        pm10: latest.reading.pm10,
        timestamp: latest.reading.timestamp,
      });
    }
    renderNetworkAlerts(alerts);
    if (!homeHistory.length) {
      updateTimelineChart(history.readings, "Network AQI");
    }
  } catch (error) {
    console.error(error);
  }
}

async function refreshSampleCities() {
  try {
    const data = await fetchJSON("/api/cities/sample?count=4");
    sampleCities = data.cities || [];
    renderRiskGrid();
  } catch (error) {
    console.error(error);
  }
}

if (locationButton) {
  locationButton.addEventListener("click", handleLocationSearch);
}

if (locationInput) {
  locationInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      handleLocationSearch();
    }
  });
}

if (locationResults) {
  locationResults.addEventListener("click", (event) => {
    const action = event.target.dataset.action;
    if (!action) return;
    const parent = event.target.closest("li[data-lat]");
    if (!parent) return;
    const { lat, lon, label } = parent.dataset;
    if (action === "view") {
      fetchNearestAqi(lat, lon, label);
    } else if (action === "home") {
      setHomeCity({ lat, lon, label });
    }
  });
}

if (changeHomeBtn) {
  changeHomeBtn.addEventListener("click", clearHome);
}

updateHomeCard(latestHomeReading);
updateTimelineChart(homeHistory, homeCity?.label || "AQI");
updateAdvicePanel();
renderRiskGrid();

refreshNetworkData();
refreshSampleCities();
fetchHomeMetrics();

setInterval(refreshNetworkData, 5000);
setInterval(refreshSampleCities, 60000);
setInterval(fetchHomeMetrics, 15000);
