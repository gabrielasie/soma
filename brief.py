"""Deterministic rule-based brief. The LLM narrates this; it does not decide."""

import statistics
from datetime import date

# Signals we track and their direction (higher is better unless noted).
SIGNALS = [
    "sleep_score",
    "deep_sleep_score",
    "rem_sleep_score",
    "efficiency_score",
    "readiness_score",
    "hrv_balance",
    "resting_heart_rate_score",
    "recovery_index",
    "body_temperature_score",
    "activity_score",
    "steps",
    "active_calories",
]


def _zscore(value, mean, stdev):
    """Compute z-score with graceful stdev=0 handling."""
    if value is None or mean is None:
        return None
    if stdev is None or stdev == 0:
        if mean == 0:
            return 0.0
        # Percentage deviation fallback: 10% off = ~1 z-score equivalent
        pct_dev = (value - mean) / abs(mean)
        return pct_dev * 10  # scale so 10% deviation ~ 1.0
    return (value - mean) / stdev


def _severity(z):
    """Map z-score to severity bucket."""
    if z is None:
        return "no_data"
    if z <= -1.5:
        return "low"
    if z <= -0.5:
        return "below_baseline"
    if z >= 1.5:
        return "high"
    if z >= 0.5:
        return "above_baseline"
    return "normal"


def _readiness_state(severities: dict) -> str:
    """Roll up individual severities to overall state."""
    vals = [v for v in severities.values() if v != "no_data"]
    if any(v == "low" for v in vals):
        return "red"
    if any(v == "below_baseline" for v in vals):
        return "yellow"
    return "green"


def compute_baselines(days: dict, today_str: str) -> dict:
    """Compute mean and stdev for each signal over the 13 days before today."""
    baselines = {}
    history_keys = sorted(k for k in days if k < today_str)[-13:]

    for signal in SIGNALS:
        values = [days[k].get(signal) for k in history_keys]
        values = [v for v in values if v is not None]
        if len(values) >= 3:
            baselines[signal] = {
                "mean": statistics.mean(values),
                "stdev": statistics.stdev(values) if len(values) >= 2 else 0,
                "n": len(values),
            }
        else:
            baselines[signal] = {"mean": None, "stdev": None, "n": len(values)}

    return baselines


def _generate_recommendations(state: str, severities: dict, today: dict) -> list:
    """Rule-based recommendations. No LLM involved."""
    recs = []

    # Light exposure is unconditional
    recs.append({
        "category": "light_exposure",
        "action": "Get 10+ minutes of direct sunlight within 1 hour of waking.",
        "reason": "Anchors circadian rhythm regardless of readiness state.",
    })

    if state == "red":
        recs.append({
            "category": "training",
            "action": "Skip intense training. Walk or do light mobility only.",
            "reason": "Multiple signals significantly below your baseline.",
        })
        recs.append({
            "category": "sleep",
            "action": "Target 8.5+ hours in bed tonight. No screens after 9pm.",
            "reason": "Recovery is the priority when signals are depleted.",
        })
        recs.append({
            "category": "nutrition",
            "action": "Front-load protein (40g+ before noon). Cut caffeine after 1pm.",
            "reason": "Support recovery without disrupting tonight's sleep.",
        })
    elif state == "yellow":
        recs.append({
            "category": "training",
            "action": "Train at moderate intensity. Cut volume by ~30% from plan.",
            "reason": "Some signals below baseline; not the day for PRs.",
        })
        recs.append({
            "category": "sleep",
            "action": "Aim for your normal bedtime. Avoid late meals.",
            "reason": "Protect baseline sleep to prevent sliding to red.",
        })
        recs.append({
            "category": "nutrition",
            "action": "Eat normally. Emphasize whole foods and hydration.",
            "reason": "No drastic changes needed, just consistency.",
        })
    else:  # green
        recs.append({
            "category": "training",
            "action": "Train as planned. Good day for intensity if programmed.",
            "reason": "All signals at or above your baseline.",
        })
        recs.append({
            "category": "sleep",
            "action": "Maintain your routine. You are sleeping well.",
            "reason": "Consistency compounds.",
        })
        recs.append({
            "category": "nutrition",
            "action": "Fuel for performance. Match intake to training demands.",
            "reason": "Green state means your body can handle the load.",
        })

    # Add specific callouts for notable deviations
    for signal, sev in severities.items():
        if sev == "low" and "sleep" in signal:
            recs.append({
                "category": "sleep_flag",
                "action": f"{signal.replace('_', ' ').title()} is significantly below your norm.",
                "reason": "This is dragging your overall readiness down.",
            })

    return recs


def build_brief(days: dict, today_str: str = None) -> dict:
    """Build the complete structured brief for today."""
    today_str = today_str or str(date.today())

    if today_str not in days:
        return {"error": f"No data for {today_str}", "date": today_str}

    today = days[today_str]
    baselines = compute_baselines(days, today_str)

    # Z-score each signal
    signals = {}
    severities = {}
    for signal in SIGNALS:
        val = today.get(signal)
        bl = baselines.get(signal, {})
        z = _zscore(val, bl.get("mean"), bl.get("stdev"))
        sev = _severity(z)
        signals[signal] = {
            "value": val,
            "baseline_mean": bl.get("mean"),
            "baseline_stdev": bl.get("stdev"),
            "z_score": round(z, 2) if z is not None else None,
            "severity": sev,
        }
        severities[signal] = sev

    state = _readiness_state(severities)
    recs = _generate_recommendations(state, severities, today)

    return {
        "date": today_str,
        "readiness_state": state,
        "signals": signals,
        "recommendations": recs,
        "baselines_days_used": baselines.get("sleep_score", {}).get("n", 0),
    }


if __name__ == "__main__":
    import json
    from oura_pull import load_cache
    days = load_cache()
    if not days:
        print("No cached data. Run oura_pull.py first.")
    else:
        today_str = str(date.today())
        # If today is missing, use the most recent day
        if today_str not in days:
            today_str = max(days.keys())
            print(f"No data for today, using most recent: {today_str}")
        brief = build_brief(days, today_str)
        print(json.dumps(brief, indent=2))
