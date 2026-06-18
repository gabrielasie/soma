"""Synthetic data for DEMO_MODE.

When DEMO_MODE is truthy the app runs entirely on the seeded synthetic profile
produced here: no real Oura, calendar, weather, cycle, or saved-brief data is
read or written anywhere. Every real-data source module (oura_pull,
calendar_pull, weather_pull, cycle, clinic_data) checks is_demo() and routes to
these generators instead of touching the network or the real files on disk.

All generators are deterministic (fixed seeds) so the demo profile is stable.
"""

import math
import os
import random
from datetime import date, datetime, timedelta

_TRUTHY = {"1", "true", "yes", "on"}


def is_demo() -> bool:
    """True when DEMO_MODE is set to a truthy value in the environment."""
    return os.environ.get("DEMO_MODE", "").strip().lower() in _TRUTHY


# ── Oura biometrics ──────────────────────────────────────────────────

# A realistic, mostly-healthy profile. Bases are the long-run mean each
# signal reverts toward; the walk wanders around them day to day.
_SIGNAL_BASES = {
    "sleep_score": 84,
    "deep_sleep_score": 78,
    "rem_sleep_score": 82,
    "efficiency_score": 88,
    "total_sleep_score": 80,
    "readiness_score": 83,
    "hrv_balance": 80,
    "resting_heart_rate_score": 85,
    "recovery_index": 82,
    "body_temperature_score": 90,
    "activity_score": 81,
}


def _clamp(v, lo=40, hi=100):
    return max(lo, min(hi, round(v)))


def synthetic_oura(n_days: int = 29, end: date = None) -> dict:
    """Return n_days of synthetic Oura data keyed by date string, ending today.

    Mirrors the shape oura_pull produces (one dict per day with the same
    signal keys), so the brief, sparklines, patterns, and chat all work.
    """
    end = end or date.today()
    rng = random.Random(424242)  # fixed seed -> stable profile

    state = dict(_SIGNAL_BASES)
    series = {k: [] for k in _SIGNAL_BASES}
    steps, active_cal, total_cal, train_vol = [], [], [], []

    for i in range(n_days):
        for k, base in _SIGNAL_BASES.items():
            # Mean-reverting random walk for realistic autocorrelation.
            state[k] += rng.gauss(0, 3) + 0.25 * (base - state[k])
            # A mild multi-day dip ~1/3 through the window for visual interest.
            dip = -8 if (n_days // 3) <= i <= (n_days // 3 + 1) else 0
            series[k].append(_clamp(state[k] + dip))
        steps.append(int(_clamp(rng.gauss(9000, 2500), 1500, 18000)))
        active_cal.append(int(_clamp(rng.gauss(450, 150), 80, 1200)))
        total_cal.append(int(rng.gauss(2200, 200)))
        train_vol.append(round(max(0.0, rng.gauss(6.5, 2.5)), 1))

    days = {}
    for i in range(n_days):
        ds = str(end - timedelta(days=n_days - 1 - i))
        day = {"date": ds}
        for k in _SIGNAL_BASES:
            day[k] = series[k][i]
        day["steps"] = steps[i]
        day["active_calories"] = active_cal[i]
        day["total_calories"] = total_cal[i]
        day["training_volume"] = train_vol[i]
        days[ds] = day
    return days


# ── Calendar ─────────────────────────────────────────────────────────

def synthetic_events(target_date) -> list:
    """Return synthetic calendar events for a date, in calendar_pull's format."""
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)

    rng = random.Random(target_date.toordinal())
    if target_date.weekday() >= 5:  # weekend
        pool = [
            ("09:30", "10:30", "Long run"),
            ("12:30", "13:30", "Lunch with friends"),
        ]
    else:
        pool = [
            ("09:00", "09:30", "Team standup"),
            ("10:30", "11:30", "Design review"),
            ("13:00", "13:30", "1:1 with manager"),
            ("15:00", "16:00", "Project sync"),
        ]

    events = []
    for start, end, title in pool:
        if rng.random() < 0.15:  # occasionally drop one for day-to-day variety
            continue
        events.append({"start_time": start, "end_time": end,
                       "title": title, "all_day": False})
    return sorted(events, key=lambda e: e["start_time"])


# ── Weather ──────────────────────────────────────────────────────────

def _synth_day_hours(d: date) -> list:
    """24 hours of synthetic forecast in weather_pull's parsed format."""
    rng = random.Random(d.toordinal() ^ 0x5EED)
    ds = str(d)
    base_t = rng.uniform(15, 24)
    hours = []
    for h in range(24):
        temp = base_t + 5 * math.sin((h - 9) / 24 * 2 * math.pi)
        hours.append({
            "date": ds,
            "hour": h,
            "temp_c": round(temp, 1),
            "precip_prob": rng.choice([0, 0, 0, 10, 20, 30]),
            "cloud_cover": rng.randint(10, 70),
            "is_day": 7 <= h <= 19,
        })
    return hours


def synthetic_forecast(d: date = None) -> list:
    return _synth_day_hours(d or date.today())


def synthetic_forecast_2day() -> dict:
    today = date.today()
    return {
        "today": _synth_day_hours(today),
        "tomorrow": _synth_day_hours(today + timedelta(days=1)),
    }


# ── Cycle ────────────────────────────────────────────────────────────

def synthetic_cycle_log(today: date = None) -> list:
    """A short cycle history (3 completed + 1 ongoing) anchored to today.

    Places today around day 18 (luteal) so phase context is non-trivial.
    """
    today = today or date.today()
    current_start = today - timedelta(days=17)
    log = []
    for k in range(3, 0, -1):
        s = current_start - timedelta(days=28 * k)
        log.append({"start": str(s), "end": str(s + timedelta(days=27)), "length": 28})
    log.append({"start": str(current_start), "end": None, "length": None})
    return log
