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


def _to_min(t):
    """Convert HH:MM string to minutes since midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _to_hm(m):
    """Convert minutes since midnight to HH:MM."""
    return f"{m // 60:02d}:{m % 60:02d}"


def _conditions_label(forecast):
    """Derive a single conditions string from forecast."""
    if not forecast:
        return "unknown"
    clouds = [e.get("cloud_cover", 50) for e in forecast]
    precips = [e.get("precip_prob", 0) for e in forecast]
    avg_cloud = sum(clouds) / len(clouds) if clouds else 50
    if any(p >= 60 for p in precips):
        return "rainy"
    if avg_cloud < 30:
        return "sunny"
    if avg_cloud < 70:
        return "partly cloudy"
    return "overcast"


def _find_free_block(free_blocks, range_start, range_end, min_dur=20, longest=False):
    """Find a free block within [range_start, range_end] of at least min_dur minutes."""
    rs = _to_min(range_start)
    re = _to_min(range_end)
    candidates = []
    for fb in (free_blocks or []):
        fs = _to_min(fb["start"])
        fe = _to_min(fb["end"])
        overlap_start = max(fs, rs)
        overlap_end = min(fe, re)
        if overlap_end - overlap_start >= min_dur:
            candidates.append({
                "start": _to_hm(overlap_start),
                "end": _to_hm(overlap_end),
                "duration_min": overlap_end - overlap_start,
            })
    if not candidates:
        return None
    if longest:
        return max(candidates, key=lambda b: b["duration_min"])
    return candidates[0]


def _generate_recommendations(state: str, severities: dict, today: dict,
                              events=None, free_blocks=None, forecast=None) -> list:
    """Rule-based recommendations. Calendar-aware when calendar/weather available."""
    recs = []

    # Pre-compute weather helpers
    conditions = _conditions_label(forecast)

    # Build per-hour weather lookup
    weather_by_hour = {}
    if forecast:
        for entry in forecast:
            weather_by_hour[entry["hour"]] = entry

    # Daylight hours from forecast
    daylight_hours = set()
    if forecast:
        for entry in forecast:
            if entry.get("is_day"):
                daylight_hours.add(entry["hour"])

    # ---- LIGHT ----
    light_action = "Get 15-20 min direct outdoor light within 90 min of waking."
    light_context = None
    if free_blocks is not None and forecast:
        # Find first daylight free block >= 20 min where precip_prob < 60
        found_light = False
        for fb in free_blocks:
            fb_start_min = _to_min(fb["start"])
            fb_end_min = _to_min(fb["end"])
            if fb["duration_min"] < 20:
                continue
            # Check that block has daylight hours
            block_start_hour = fb_start_min // 60
            block_end_hour = min(fb_end_min // 60, 23)
            has_daylight = any(h in daylight_hours for h in range(block_start_hour, block_end_hour + 1))
            if not has_daylight:
                continue
            # Check precip for the block hours
            block_hours = range(block_start_hour, block_end_hour + 1)
            precip_ok = all(weather_by_hour.get(h, {}).get("precip_prob", 0) < 60 for h in block_hours)
            if not precip_ok:
                continue
            # Found a suitable block
            duration = min(fb["duration_min"], 25)
            temp = weather_by_hour.get(block_start_hour, {}).get("temp_c")
            temp_str = f"{temp:.0f}\u00B0C, " if temp is not None else ""
            light_action = f"Walk at {fb['start']}. {duration} min, {temp_str}{conditions}."
            light_context = "Picked your first free block with daylight"
            found_light = True
            break

        if not found_light:
            # Fallback: indoor light by first free morning block
            morning_block = _find_free_block(free_blocks, "07:00", "12:00", min_dur=15)
            if morning_block:
                light_action = f"No outdoor light window today. 15 min by a south-facing window at {morning_block['start']}."
            else:
                light_action = "No outdoor light window today. 15 min by a south-facing window when you can."
            light_context = "All daylight time booked"
    elif free_blocks is not None and not forecast:
        # Calendar but no weather
        morning_block = _find_free_block(free_blocks, "07:00", "10:00", min_dur=20)
        if morning_block:
            light_action = f"Walk at {morning_block['start']}. 20 min of outdoor light."
            light_context = "Picked your first free morning block"
        else:
            light_action = "No outdoor light window today. 15 min by a south-facing window when you can."
            light_context = "All daylight time booked"

    recs.append({
        "category": "light_exposure",
        "action": light_action,
        "reason": "Anchors circadian rhythm regardless of readiness state.",
        "context_note": light_context,
    })

    # ---- TRAINING ----
    training_block = _find_free_block(free_blocks, "16:00", "19:00", min_dur=20, longest=True)

    if state == "green":
        if training_block and training_block["duration_min"] >= 60:
            light_action_tr = f"Lift at {training_block['start']}. {training_block['duration_min']} min available."
            ctx = f"Free {training_block['start']}-{training_block['end']}"
        elif training_block:
            light_action_tr = "Hard training won't fit today. Push to tomorrow."
            ctx = f"Only {training_block['duration_min']} min free in 16:00-19:00"
        elif free_blocks is not None:
            light_action_tr = "Hard training won't fit today. Push to tomorrow."
            ctx = "No free block in 16:00-19:00"
        else:
            light_action_tr = "Train as planned. Good day for intensity if programmed."
            ctx = None
        recs.append({
            "category": "training",
            "action": light_action_tr,
            "reason": "All signals at or above your baseline.",
            "context_note": ctx,
        })
    elif state == "yellow":
        if training_block and training_block["duration_min"] >= 30:
            action = f"Easy session at {training_block['start']}. {training_block['duration_min']} min available."
            ctx = f"Free {training_block['start']}-{training_block['end']}"
        elif training_block:
            action = "Hard training won't fit today. Push to tomorrow."
            ctx = f"Only {training_block['duration_min']} min free in 16:00-19:00"
        elif free_blocks is not None:
            action = "Hard training won't fit today. Push to tomorrow."
            ctx = "No free block in 16:00-19:00"
        else:
            action = "Train at moderate intensity. Cut volume by ~30% from plan."
            ctx = None
        recs.append({
            "category": "training",
            "action": action,
            "reason": "Some signals below baseline; not the day for PRs.",
            "context_note": ctx,
        })
    else:  # red
        if training_block:
            walk_dur = min(training_block["duration_min"], 30)
            action = f"Walk at {training_block['start']}. {walk_dur} min, easy pace."
            ctx = f"Free {training_block['start']}-{training_block['end']}"
        elif free_blocks is not None:
            # Try any block
            any_block = _find_free_block(free_blocks, "07:00", "22:00", min_dur=20)
            if any_block:
                walk_dur = min(any_block["duration_min"], 30)
                action = f"Walk at {any_block['start']}. {walk_dur} min, easy pace."
                ctx = f"Free {any_block['start']}-{any_block['end']}"
            else:
                action = "Skip intense training. Walk or do light mobility only."
                ctx = "No suitable free block"
        else:
            action = "Skip intense training. Walk or do light mobility only."
            ctx = None
        recs.append({
            "category": "training",
            "action": action,
            "reason": "Multiple signals significantly below your baseline.",
            "context_note": ctx,
        })

    # ---- NUTRITION ----
    lunch_block = _find_free_block(free_blocks, "12:00", "14:00", min_dur=25)

    if free_blocks is not None:
        if lunch_block:
            nutrition_action = f"Lunch at {lunch_block['start']}. Target 35g protein in your {lunch_block['duration_min']} min window."
            nutrition_ctx = "Lunch window protected"
        else:
            # Find the events that sandwich the lunch period
            lunch_events = []
            if events:
                for ev in events:
                    if ev.get("all_day"):
                        continue
                    if ev.get("start_time") and ev.get("end_time"):
                        if ev["start_time"] < "14:00" and ev["end_time"] > "12:00":
                            lunch_events.append(ev)
            if len(lunch_events) >= 2:
                first_ev = lunch_events[0].get("title", "an event")
                next_ev = lunch_events[1].get("title", "an event")
                nutrition_action = f"No lunch break today. Pack 35g protein for between {first_ev} and {next_ev}."
            elif len(lunch_events) == 1:
                ev_title = lunch_events[0].get("title", "an event")
                nutrition_action = f"No lunch break today. Pack 35g protein for before or after {ev_title}."
            else:
                nutrition_action = "No lunch break today. Pack 35g protein for whenever you can eat."
            nutrition_ctx = "Lunch break missing today"
    elif state == "red":
        nutrition_action = "Front-load protein (40g+ before noon). Cut caffeine after 1pm."
        nutrition_ctx = None
    elif state == "green":
        nutrition_action = "Fuel for performance. Match intake to training demands."
        nutrition_ctx = None
    else:
        nutrition_action = "Eat normally. Emphasize whole foods and hydration."
        nutrition_ctx = None

    recs.append({
        "category": "nutrition",
        "action": nutrition_action,
        "reason": "Support recovery without disrupting tonight's sleep." if state == "red" else "Green state means your body can handle the load." if state == "green" else "No drastic changes needed, just consistency.",
        "context_note": nutrition_ctx,
    })

    # ---- SLEEP ----
    if state == "red":
        sleep_action = "Lights out at 22:30. Target 8.5+ hours in bed tonight."
        sleep_ctx = None
    elif state == "yellow":
        sleep_action = "Lights out at 22:30. Aim for your normal bedtime. Avoid late meals."
        sleep_ctx = None
    else:
        sleep_action = "Lights out at 22:30. Maintain your routine. You are sleeping well."
        sleep_ctx = None

    # Check for events running past 21:30
    if events:
        for ev in events:
            if ev.get("all_day"):
                continue
            if ev.get("end_time") and ev["end_time"] > "21:30":
                end_min = _to_min(ev["end_time"])
                push_min = end_min + 30
                push_time = _to_hm(push_min)
                title = ev.get("title", "an event")
                sleep_action = (f"You have {title} until {ev['end_time']}. "
                               f"Push lights out to {push_time}, but get back to 22:30 tomorrow.")
                sleep_ctx = f"Late event: {title} until {ev['end_time']}"
                break

    recs.append({
        "category": "sleep",
        "action": sleep_action,
        "reason": "Recovery is the priority." if state == "red" else "Consistency compounds.",
        "context_note": sleep_ctx,
    })

    # Add specific callouts for notable deviations
    for signal, sev in severities.items():
        if sev == "low" and "sleep" in signal:
            recs.append({
                "category": "sleep_flag",
                "action": f"{signal.replace('_', ' ').title()} is significantly below your norm.",
                "reason": "This is dragging your overall readiness down.",
                "context_note": None,
            })

    return recs


def _time_in_range(time_str, range_start, range_end):
    """Check if HH:MM time_str falls within [range_start, range_end)."""
    return range_start <= time_str < range_end


def build_brief(days: dict, today_str: str = None,
                events=None, free_blocks=None, forecast=None) -> dict:
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
    recs = _generate_recommendations(state, severities, today,
                                     events=events, free_blocks=free_blocks,
                                     forecast=forecast)

    return {
        "date": today_str,
        "readiness_state": state,
        "signals": signals,
        "recommendations": recs,
        "baselines_days_used": baselines.get("sleep_score", {}).get("n", 0),
    }


def summarize_signals(signals: dict) -> str:
    """Return a short string like 'Sleep down, HRV up' from signals dict."""
    parts = []
    key_signals = ["sleep_score", "readiness_score", "hrv_balance", "resting_heart_rate_score"]
    for sig_name in key_signals:
        sig = signals.get(sig_name)
        if not sig:
            continue
        sev = sig.get("severity", "no_data")
        label = sig_name.replace("_score", "").replace("_balance", "").replace("_", " ")
        label = label.strip().title()
        if label.lower() == "hrv":
            label = "HRV"
        if "Resting Heart Rate" in label:
            label = "RHR"
        if sev in ("low", "below_baseline"):
            parts.append(f"{label} down")
        elif sev in ("high", "above_baseline"):
            parts.append(f"{label} up")
    if not parts:
        return "All signals normal"
    return ", ".join(parts[:3])


def build_brief_for_date(days: dict, target_date: str,
                         events=None, free_blocks=None, forecast=None) -> dict:
    """Build a brief for a specific date (historical or today). Alias for build_brief."""
    return build_brief(days, target_date, events=events, free_blocks=free_blocks, forecast=forecast)


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
