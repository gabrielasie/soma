"""Fetch last 14 days of Oura data and cache locally."""

import json
import os
from datetime import date, timedelta
from pathlib import Path

import requests

BASE = "https://api.ouraring.com"
CACHE_PATH = Path(__file__).parent / "data" / "cache.json"

ENDPOINTS = {
    "sleep": "/v2/usercollection/daily_sleep",
    "readiness": "/v2/usercollection/daily_readiness",
    "activity": "/v2/usercollection/daily_activity",
}

# Fields we extract from each endpoint's response items.
FIELD_MAP = {
    "sleep": {
        "score": "sleep_score",
        "contributors": {
            "deep_sleep": "deep_sleep_score",
            "rem_sleep": "rem_sleep_score",
            "efficiency": "efficiency_score",
            "total_sleep": "total_sleep_score",
        },
    },
    "readiness": {
        "score": "readiness_score",
        "contributors": {
            "hrv_balance": "hrv_balance",
            "resting_heart_rate": "resting_heart_rate_score",
            "recovery_index": "recovery_index",
            "body_temperature": "body_temperature_score",
        },
    },
    "activity": {
        "score": "activity_score",
        "steps": "steps",
        "active_calories": "active_calories",
        "total_calories": "total_calories",
        "equivalent_walking_distance": "training_volume",
    },
}


def _extract_sleep(item: dict) -> dict:
    out = {}
    out["sleep_score"] = item.get("score")
    contributors = item.get("contributors", {})
    out["deep_sleep_score"] = contributors.get("deep_sleep")
    out["rem_sleep_score"] = contributors.get("rem_sleep")
    out["efficiency_score"] = contributors.get("efficiency")
    out["total_sleep_score"] = contributors.get("total_sleep")
    # Compute total sleep hours from timestamp fields if available
    ts = item.get("timestamp")
    if ts is None:
        # Try to get total_sleep_duration from the raw data
        pass
    return out


def _extract_readiness(item: dict) -> dict:
    out = {}
    out["readiness_score"] = item.get("score")
    contributors = item.get("contributors", {})
    out["hrv_balance"] = contributors.get("hrv_balance")
    out["resting_heart_rate_score"] = contributors.get("resting_heart_rate")
    out["recovery_index"] = contributors.get("recovery_index")
    out["body_temperature_score"] = contributors.get("body_temperature")
    return out


def _extract_activity(item: dict) -> dict:
    out = {}
    out["activity_score"] = item.get("score")
    out["steps"] = item.get("steps")
    out["active_calories"] = item.get("active_calories")
    out["training_volume"] = item.get("equivalent_walking_distance")
    return out


EXTRACTORS = {
    "sleep": _extract_sleep,
    "readiness": _extract_readiness,
    "activity": _extract_activity,
}


def pull(token: str = None) -> dict:
    """Fetch 14 days of Oura data. Returns dict keyed by date string."""
    token = token or os.environ["OURA_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}

    end = date.today()
    start = end - timedelta(days=14)
    params = {"start_date": str(start), "end_date": str(end)}

    days = {}

    for category, endpoint in ENDPOINTS.items():
        url = BASE + endpoint
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        extractor = EXTRACTORS[category]
        for item in data:
            day_key = item.get("day")
            if not day_key:
                continue
            if day_key not in days:
                days[day_key] = {"date": day_key}
            days[day_key].update(extractor(item))

    # Cache
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(days, f, indent=2)

    return days


def load_cache() -> dict:
    """Load cached data if available."""
    if CACHE_PATH.exists():
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    data = pull()
    print(f"Pulled {len(data)} days of data.")
    for day_key in sorted(data.keys()):
        d = data[day_key]
        print(f"  {day_key}: sleep={d.get('sleep_score')} readiness={d.get('readiness_score')} activity={d.get('activity_score')}")
