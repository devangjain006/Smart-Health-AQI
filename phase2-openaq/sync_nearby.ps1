param(
  [Parameter(Mandatory=$true)][double]$Latitude,
  [Parameter(Mandatory=$true)][double]$Longitude,
  [double]$RadiusKm = 25
)
$uri = "http://127.0.0.1:8000/api/aqi/refresh/nearby?lat=$Latitude&lon=$Longitude&radius_km=$RadiusKm"
Invoke-RestMethod -Method Post -Uri $uri | ConvertTo-Json -Depth 8
