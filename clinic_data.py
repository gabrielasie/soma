"""
Synthetic patient population for the clinician demo view.
Generates plausible 30-day biometric data for 4 synthetic patients,
plus blends in the real Oura user as Patient E.

When DEMO_MODE is on, the real user (Patient E) is never included: the
population is the 4 synthetic patients only, regardless of any data passed in.
"""

import random
import math
from datetime import date, timedelta

from demo import is_demo


def _seed_series(base, volatility, n, trend=0, seed=None):
    """Generate a plausible biometric time series."""
    rng = random.Random(seed)
    vals = []
    v = base
    for i in range(n):
        v = base + trend * i + rng.gauss(0, volatility)
        v = max(40, min(100, round(v)))
        vals.append(v)
    return vals


def _generate_dates(n=30):
    today = date.today()
    return [str(today - timedelta(days=n - 1 - i)) for i in range(n)]


PROTOCOLS = [
    {"name": "Protein 1.6 g/kg", "target": "≥ 1.6 g/kg daily"},
    {"name": "Sleep ≥ 7.5 h", "target": "≥ 7.5 hours per night"},
    {"name": "Zone 2 cardio 2×/wk", "target": "2 sessions per week"},
]


def _adherence(days_on, seed):
    """Generate protocol adherence percentages."""
    rng = random.Random(seed)
    # More days = generally better adherence (learning curve)
    base = min(70 + days_on * 0.3, 92)
    return [
        {"protocol": p["name"], "target": p["target"],
         "adherence_pct": min(100, max(30, round(base + rng.gauss(0, 8))))}
        for p in PROTOCOLS
    ]


def _build_synthetic(patient_id, name, age, days_on, state, flag, flag_detail,
                     cycle_day, cycle_phase, sleep_base, hrv_base, readiness_base,
                     hrv_trend=0, seed=0):
    dates = _generate_dates(30)
    sleep = _seed_series(sleep_base, 4, 30, seed=seed)
    hrv = _seed_series(hrv_base, 2.5, 30, trend=hrv_trend, seed=seed + 1)
    readiness = _seed_series(readiness_base, 3, 30, seed=seed + 2)
    deep = _seed_series(sleep_base - 2, 5, 30, seed=seed + 3)

    today_signals = {
        "sleep_score": sleep[-1],
        "hrv_balance": hrv[-1],
        "readiness_score": readiness[-1],
        "deep_sleep_score": deep[-1],
    }

    return {
        "id": patient_id,
        "name": name,
        "age": age,
        "days_on_soma": days_on,
        "state": state,
        "flag": flag,
        "flag_detail": flag_detail,
        "cycle": {"day": cycle_day, "phase": cycle_phase},
        "last_checkin": str(date.today() - timedelta(days=1)),
        "today_signals": today_signals,
        "history": {
            "dates": dates,
            "sleep_score": sleep,
            "hrv_balance": hrv,
            "readiness_score": readiness,
            "deep_sleep_score": deep,
        },
        "adherence": _adherence(days_on, seed + 10),
        "notes": [],
    }


def build_population(real_oura_data=None):
    """Build the patient population (4 synthetic; +Patient E unless DEMO_MODE)."""
    # DEMO_MODE: drop the real user entirely, no matter what was passed in.
    if is_demo():
        real_oura_data = None

    patients = []

    # Patient A: 38, green, 47 days, no flag
    patients.append(_build_synthetic(
        "patient-a", "Patient A", 38, 47, "green", False, None,
        cycle_day=14, cycle_phase="ovulatory",
        sleep_base=86, hrv_base=90, readiness_base=89, seed=101,
    ))

    # Patient B: 42, watch, 31 days, FLAGGED (HRV declined 22% over 14 days)
    patients.append(_build_synthetic(
        "patient-b", "Patient B", 42, 31, "watch", True,
        "HRV declined 22% over 14 days",
        cycle_day=5, cycle_phase="follicular",
        sleep_base=74, hrv_base=82, readiness_base=76, hrv_trend=-0.5, seed=202,
    ))

    # Patient C: 35, steady, 89 days, no flag
    patients.append(_build_synthetic(
        "patient-c", "Patient C", 35, 89, "steady", False, None,
        cycle_day=21, cycle_phase="luteal",
        sleep_base=82, hrv_base=86, readiness_base=84, seed=303,
    ))

    # Patient D: 44, green, 12 days, no flag (recently onboarded)
    patients.append(_build_synthetic(
        "patient-d", "Patient D", 44, 12, "green", False, None,
        cycle_day=9, cycle_phase="follicular",
        sleep_base=88, hrv_base=91, readiness_base=90, seed=404,
    ))

    # Patient E: real user data
    if real_oura_data:
        keys = sorted(real_oura_data.keys())[-30:]
        dates = keys
        sleep = [real_oura_data[k].get("sleep_score") for k in keys]
        hrv = [real_oura_data[k].get("hrv_balance") for k in keys]
        readiness = [real_oura_data[k].get("readiness_score") for k in keys]
        deep = [real_oura_data[k].get("deep_sleep_score") for k in keys]

        today = real_oura_data[keys[-1]]
        # Determine state from today's signals
        state = "green"
        for v in [today.get("deep_sleep_score"), today.get("hrv_balance"),
                  today.get("readiness_score"), today.get("sleep_score")]:
            if v is not None and v < 80:
                state = "steady"
            if v is not None and v < 70:
                state = "watch"

        patients.append({
            "id": "patient-e",
            "name": "Patient E",
            "age": 29,
            "days_on_soma": len(keys),
            "state": state,
            "flag": False,
            "flag_detail": None,
            "cycle": {"day": 23, "phase": "luteal"},
            "last_checkin": keys[-1],
            "today_signals": {
                "sleep_score": today.get("sleep_score"),
                "hrv_balance": today.get("hrv_balance"),
                "readiness_score": today.get("readiness_score"),
                "deep_sleep_score": today.get("deep_sleep_score"),
            },
            "history": {
                "dates": dates,
                "sleep_score": sleep,
                "hrv_balance": hrv,
                "readiness_score": readiness,
                "deep_sleep_score": deep,
            },
            "adherence": _adherence(len(keys), 505),
            "notes": [],
        })

    return patients


def get_patient(patient_id, real_oura_data=None):
    """Get a single patient by ID."""
    for p in build_population(real_oura_data):
        if p["id"] == patient_id:
            return p
    return None
