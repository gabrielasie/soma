"""Fetch hourly forecast from Open-Meteo. No API key needed."""

import json
import os
import time
from pathlib import Path

import requests

from demo import is_demo, synthetic_forecast, synthetic_forecast_2day

CACHE_PATH = Path(__file__).parent / "data" / "weather_cache.json"
CACHE_PATH_2DAY = Path(__file__).parent / "data" / "weather_cache_2day.json"
CACHE_TTL = 3600  # 1 hour


def _parse_hourly(data: dict) -> list:
    """Parse Open-Meteo hourly response into list of dicts."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    precip = hourly.get("precipitation_probability", [])
    cloud = hourly.get("cloud_cover", [])
    is_day = hourly.get("is_day", [])

    forecast = []
    for i, t in enumerate(times):
        hour = int(t.split("T")[1].split(":")[0])
        forecast.append({
            "date": t.split("T")[0],
            "hour": hour,
            "temp_c": temps[i] if i < len(temps) else None,
            "precip_prob": precip[i] if i < len(precip) else 0,
            "cloud_cover": cloud[i] if i < len(cloud) else 0,
            "is_day": bool(is_day[i]) if i < len(is_day) else False,
        })
    return forecast


def get_hourly_forecast(lat=41.7, lon=-86.24) -> list:
    """Fetch today's hourly forecast. Returns list of dicts with hour, temp_c, etc."""
    # DEMO_MODE: synthetic forecast only; no location is sent to Open-Meteo.
    if is_demo():
        return synthetic_forecast()
    # Check cache
    if CACHE_PATH.exists():
        mtime = os.path.getmtime(CACHE_PATH)
        if time.time() - mtime < CACHE_TTL:
            with open(CACHE_PATH) as f:
                return json.load(f)

    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,precipitation_probability,cloud_cover,is_day",
            "timezone": "America/New_York",
            "forecast_days": 1,
        },
        timeout=10,
    )
    resp.raise_for_status()
    raw = resp.json()
    forecast = _parse_hourly(raw)

    # Cache
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(forecast, f, indent=2)

    return forecast


def get_2day_forecast(lat=41.7, lon=-86.24) -> dict:
    """Fetch 2-day forecast. Returns {"today": [...], "tomorrow": [...]}."""
    # DEMO_MODE: synthetic forecast only; no location is sent to Open-Meteo.
    if is_demo():
        return synthetic_forecast_2day()
    if CACHE_PATH_2DAY.exists():
        mtime = os.path.getmtime(CACHE_PATH_2DAY)
        if time.time() - mtime < CACHE_TTL:
            with open(CACHE_PATH_2DAY) as f:
                return json.load(f)

    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,precipitation_probability,cloud_cover,is_day",
            "timezone": "America/New_York",
            "forecast_days": 2,
        },
        timeout=10,
    )
    resp.raise_for_status()
    raw = resp.json()
    all_hours = _parse_hourly(raw)

    # Split by date
    from datetime import date as _date
    today_str = str(_date.today())
    today_hours = [h for h in all_hours if h.get("date") == today_str]
    tomorrow_hours = [h for h in all_hours if h.get("date") != today_str]

    result = {"today": today_hours, "tomorrow": tomorrow_hours}

    CACHE_PATH_2DAY.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH_2DAY, "w") as f:
        json.dump(result, f, indent=2)

    return result


def daylight_windows(forecast: list) -> list:
    """Returns list of (start_hour, end_hour) tuples for consecutive daylight hours."""
    windows = []
    start = None
    for entry in forecast:
        if entry["is_day"]:
            if start is None:
                start = entry["hour"]
        else:
            if start is not None:
                windows.append((start, entry["hour"]))
                start = None
    if start is not None:
        windows.append((start, 24))
    return windows


def weather_summary(forecast: list) -> dict:
    """Summarize today's weather into temp_high, temp_low, precip_chance_max, conditions."""
    temps = [e["temp_c"] for e in forecast if e["temp_c"] is not None]
    precips = [e["precip_prob"] for e in forecast if e["precip_prob"] is not None]
    clouds = [e["cloud_cover"] for e in forecast if e["cloud_cover"] is not None]

    temp_high = max(temps) if temps else None
    temp_low = min(temps) if temps else None
    precip_max = max(precips) if precips else 0
    avg_cloud = sum(clouds) / len(clouds) if clouds else 50

    if any(p >= 60 for p in precips):
        conditions = "rainy"
    elif avg_cloud < 30:
        conditions = "sunny"
    elif avg_cloud < 70:
        conditions = "partly_cloudy"
    else:
        conditions = "overcast"

    return {
        "temp_high": round(temp_high, 1) if temp_high is not None else None,
        "temp_low": round(temp_low, 1) if temp_low is not None else None,
        "precip_chance_max": precip_max,
        "conditions": conditions,
    }
