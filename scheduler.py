"""Deterministic day planner. Maps recommendations to time blocks using free slots."""


def _to_min(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _to_hm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def _find_block(free_blocks, range_start, range_end, min_dur=20, longest=False):
    """Find a free block within [range_start, range_end] of at least min_dur minutes.
    If longest=True, return the longest such block instead of the first."""
    rs = _to_min(range_start)
    re = _to_min(range_end)

    candidates = []
    for fb in free_blocks:
        fs = _to_min(fb["start"])
        fe = _to_min(fb["end"])
        # Overlap with range
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


def _block_in_daylight(block, daylight_windows):
    """Check if a block overlaps with any daylight window."""
    if not block or not daylight_windows:
        return False
    bs = _to_min(block["start"])
    for dw_start, dw_end in daylight_windows:
        if bs >= dw_start * 60 and bs < dw_end * 60:
            return True
    return False


def plan_day(brief, events, free_blocks, forecast) -> list:
    """Build a day plan from recommendations, events, free blocks, and forecast.
    Returns list of {start, end, title, category, source, weather_note} blocks."""
    blocks = []
    state = brief.get("readiness_state", "green")
    recs = brief.get("recommendations", [])

    # Index recs by category
    rec_by_cat = {}
    for r in recs:
        rec_by_cat.setdefault(r["category"], r)

    # Weather context
    weather_note_default = ""
    if forecast:
        from weather_pull import weather_summary, daylight_windows
        ws = weather_summary(forecast)
        dl_windows = daylight_windows(forecast)
        conditions = (ws.get("conditions") or "").replace("_", " ")

        # Morning weather for light block
        morning_hours = [e for e in forecast if 6 <= e["hour"] <= 10]
        morning_temp = None
        morning_precip = False
        if morning_hours:
            morning_temp = morning_hours[len(morning_hours)//2].get("temp_c")
            morning_precip = any(e.get("precip_prob", 0) >= 60 for e in morning_hours)

        # Afternoon weather for training block
        afternoon_hours = [e for e in forecast if 14 <= e["hour"] <= 19]
        afternoon_temp = None
        if afternoon_hours:
            afternoon_temp = afternoon_hours[len(afternoon_hours)//2].get("temp_c")
    else:
        ws = None
        dl_windows = []
        morning_temp = None
        morning_precip = False
        afternoon_temp = None
        conditions = ""

    # 1. LIGHT recommendation
    light_rec = rec_by_cat.get("light_exposure")
    if light_rec:
        # Find earliest morning free block in daylight with low precip
        light_block = _find_block(free_blocks, "07:00", "10:00", min_dur=20)
        wn = ""
        if light_block and morning_temp is not None:
            wn = f"{morning_temp:.0f}\u00B0C, {conditions}"
        elif morning_precip:
            wn = "raining, indoor substitution"

        if light_block:
            end_min = min(_to_min(light_block["start"]) + 20, _to_min(light_block["end"]))
            blocks.append({
                "start": light_block["start"],
                "end": _to_hm(end_min),
                "title": light_rec["action"],
                "category": "light_exposure",
                "source": "light",
                "weather_note": wn,
            })
        else:
            blocks.append({
                "start": None, "end": None,
                "title": "UNSCHEDULED: " + light_rec["action"],
                "category": "light_exposure",
                "source": "light",
                "weather_note": wn,
            })

    # 2. TRAINING recommendation
    training_rec = rec_by_cat.get("training")
    if training_rec:
        wn = ""
        if state == "green":
            tb = _find_block(free_blocks, "14:00", "19:00", min_dur=30, longest=True)
            if tb:
                dur = min(tb["duration_min"], 60)
                end_min = _to_min(tb["start"]) + dur
                if afternoon_temp is not None:
                    wn = f"{afternoon_temp:.0f}\u00B0C, {conditions}"
                blocks.append({
                    "start": tb["start"],
                    "end": _to_hm(end_min),
                    "title": training_rec["action"],
                    "category": "training",
                    "source": "training",
                    "weather_note": wn,
                })
            else:
                blocks.append({
                    "start": None, "end": None,
                    "title": "UNSCHEDULED: " + training_rec["action"],
                    "category": "training",
                    "source": "training",
                    "weather_note": "",
                })
        elif state == "yellow":
            tb = _find_block(free_blocks, "12:00", "19:00", min_dur=30)
            if tb:
                end_min = _to_min(tb["start"]) + 30
                blocks.append({
                    "start": tb["start"],
                    "end": _to_hm(end_min),
                    "title": training_rec["action"],
                    "category": "training",
                    "source": "training",
                    "weather_note": "",
                })
            else:
                blocks.append({
                    "start": None, "end": None,
                    "title": "UNSCHEDULED: " + training_rec["action"],
                    "category": "training",
                    "source": "training",
                    "weather_note": "",
                })
        else:  # red
            tb = _find_block(free_blocks, "07:00", "19:00", min_dur=30)
            if tb:
                in_daylight = _block_in_daylight(tb, dl_windows)
                if in_daylight and afternoon_temp is not None:
                    wn = f"{afternoon_temp:.0f}\u00B0C, {conditions}"
                end_min = _to_min(tb["start"]) + 30
                blocks.append({
                    "start": tb["start"],
                    "end": _to_hm(end_min),
                    "title": training_rec["action"],
                    "category": "training",
                    "source": "training",
                    "weather_note": wn,
                })
            else:
                blocks.append({
                    "start": None, "end": None,
                    "title": "UNSCHEDULED: " + training_rec["action"],
                    "category": "training",
                    "source": "training",
                    "weather_note": "",
                })

    # 3. NUTRITION: breakfast + dinner
    nutrition_rec = rec_by_cat.get("nutrition")
    if nutrition_rec:
        breakfast = _find_block(free_blocks, "07:30", "09:00", min_dur=20)
        if breakfast:
            end_min = _to_min(breakfast["start"]) + 30
            blocks.append({
                "start": breakfast["start"],
                "end": _to_hm(min(end_min, _to_min(breakfast["end"]))),
                "title": "Breakfast. " + nutrition_rec["action"],
                "category": "nutrition",
                "source": "nutrition",
                "weather_note": "",
            })

        dinner = _find_block(free_blocks, "18:00", "19:30", min_dur=30)
        if dinner:
            blocks.append({
                "start": dinner["start"],
                "end": _to_hm(min(_to_min(dinner["start"]) + 30, _to_min(dinner["end"]))),
                "title": "Dinner. Last meal of the day.",
                "category": "nutrition",
                "source": "nutrition",
                "weather_note": "",
            })

    # 4. SLEEP: wind-down + lights out
    sleep_rec = rec_by_cat.get("sleep")
    if sleep_rec:
        blocks.append({
            "start": "22:00",
            "end": "22:30",
            "title": "Wind-down. Screens off. Dim lights.",
            "category": "sleep",
            "source": "sleep",
            "weather_note": "",
        })
        blocks.append({
            "start": "22:30",
            "end": "22:35",
            "title": "Lights out.",
            "category": "sleep",
            "source": "sleep",
            "weather_note": "",
        })

    # Sort by start time (None goes last)
    blocks.sort(key=lambda b: b["start"] or "99:99")
    return blocks
