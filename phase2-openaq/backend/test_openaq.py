from datetime import datetime, timezone

from backend.openaq import calculate_us_aqi, _utc_datetime


def test_pm25_aqi_good():
    assert calculate_us_aqi("pm25", 5.0, "µg/m³") == 28


def test_pm25_mg_conversion():
    assert calculate_us_aqi("pm25", 0.005, "mg/m³") == 28


def test_pm10_aqi():
    assert calculate_us_aqi("pm10", 54, "µg/m³") == 50
    assert calculate_us_aqi("pm10", 425, "µg/m³") == 301


def test_unknown_units_are_not_converted():
    assert calculate_us_aqi("pm25", 12, "ppm") is None


def test_gases_do_not_get_pm_breakpoints():
    assert calculate_us_aqi("no2", 20, "µg/m³") is None


def test_datetime_utc():
    value = _utc_datetime({"utc": "2026-11-19T12:00:00Z"})
    assert value.tzinfo == timezone.utc
