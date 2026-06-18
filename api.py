"""Soma API. Serves brief and chat endpoints, plus static frontend."""

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import anthropic
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from brief import build_brief, build_brief_for_date, summarize_signals, SIGNALS
from clinic_data import build_population, get_patient
from demo import is_demo, synthetic_oura
from oura_pull import pull, load_cache
from soma import MODEL, SYSTEM_PROMPT, narrate_brief

app = FastAPI(title="Soma")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache keyed by date string, TTL 30 min (calendar can change)
_brief_cache: TTLCache = TTLCache(maxsize=8, ttl=30 * 60)
_week_cache: TTLCache = TTLCache(maxsize=2, ttl=30 * 60)
# Synthesized brief audio, keyed by date (full narration and greeting use
# distinct keys). Avoids re-billing ElevenLabs on repeated playback.
# Same TTL as the brief it narrates.
_audio_cache: TTLCache = TTLCache(maxsize=8, ttl=30 * 60)

_client = None


def _briefs_dir() -> Path:
    """Briefs persistence dir. Isolated from real briefs when in DEMO_MODE."""
    return Path(__file__).parent / "data" / ("briefs_demo" if is_demo() else "briefs")


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _pull_data() -> dict:
    # DEMO_MODE: synthetic profile only; no Oura token required or used.
    if is_demo():
        return synthetic_oura()
    token = os.environ.get("OURA_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="OURA_TOKEN not set")
    try:
        return pull(token)
    except Exception:
        data = load_cache()
        if not data:
            raise HTTPException(status_code=502, detail="Oura API unreachable and no cache")
        return data


SPARKLINE_SIGNALS = ["sleep_score", "readiness_score", "hrv_balance", "resting_heart_rate_score"]


def _build_history(data: dict, target: str) -> dict:
    """Extract 14 days of values for sparkline signals, oldest first."""
    all_keys = sorted(k for k in data if k <= target)[-14:]
    history = {}
    for sig in SPARKLINE_SIGNALS:
        history[sig] = [data[k].get(sig) for k in all_keys]
    return history


def _pull_calendar():
    """Pull today's calendar events. Returns (events, free_blocks, error_flag)."""
    try:
        from calendar_pull import get_todays_events, find_free_blocks
        events = get_todays_events()
        free_blocks = find_free_blocks(events)
        return events, free_blocks, False
    except Exception:
        return [], [], True


def _pull_calendar_for_date(target_date):
    """Pull calendar events for a specific date. Returns (events, free_blocks, error_flag)."""
    try:
        from calendar_pull import get_events_for_date, find_free_blocks
        events = get_events_for_date(target_date)
        free_blocks = find_free_blocks(events)
        return events, free_blocks, False
    except Exception:
        return [], [], True


def _pull_weather():
    """Pull weather forecast. Returns (forecast, summary, error_flag)."""
    try:
        from weather_pull import get_hourly_forecast, weather_summary
        forecast = get_hourly_forecast()
        summary = weather_summary(forecast)
        return forecast, summary, False
    except Exception:
        return None, None, True


def _save_brief_to_disk(date_str: str, brief_data: dict):
    """Persist brief JSON to data/briefs/ for historical lookups."""
    briefs_dir = _briefs_dir()
    briefs_dir.mkdir(parents=True, exist_ok=True)
    path = briefs_dir / f"{date_str}.json"
    with open(path, "w") as f:
        json.dump(brief_data, f, indent=2)


def _load_brief_from_disk(date_str: str):
    """Load a previously cached brief from disk."""
    path = _briefs_dir() / f"{date_str}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def _compute_tomorrow_projection(data: dict, today_brief: dict,
                                  today_events: list, today_plan: list) -> dict:
    """Deterministic tomorrow projection: recovery outlook + schedule outlook."""
    state = today_brief.get("readiness_state", "green")
    signals = today_brief.get("signals", {})

    # Check if today has hard training in plan
    has_hard_training = False
    has_light_training = True
    for bl in (today_plan or []):
        if bl.get("source") == "training":
            action = (bl.get("title") or "").lower()
            if "lift" in action or "intensity" in action:
                has_hard_training = True
                has_light_training = False
            elif "walk" in action or "easy" in action or "light" in action:
                has_light_training = True

    # Recovery outlook
    recovery = ""

    # Check for declining signals over last 3 days
    today_str = today_brief.get("date", str(date.today()))
    recent_keys = sorted(k for k in data if k <= today_str)[-3:]
    declining_signals = []
    for sig_name in ["sleep_score", "readiness_score", "hrv_balance"]:
        vals = [data[k].get(sig_name) for k in recent_keys if data[k].get(sig_name) is not None]
        if len(vals) >= 3 and vals[-1] < vals[-2] < vals[-3]:
            label = sig_name.replace("_score", "").replace("_", " ").replace("hrv balance", "HRV").title()
            declining_signals.append(label)

    decline_prefix = ""
    if declining_signals:
        decline_prefix = f"Watch out: {declining_signals[0]} has been declining. "

    if state == "red":
        if has_hard_training:
            recovery = "Tomorrow starts in yellow at best. Sleep is the lever tonight."
        else:
            recovery = "Tomorrow could recover to yellow if you hit 22:30 lights out."
    elif state == "yellow":
        recovery = "Tomorrow likely returns to green with consistent sleep."
    else:
        recovery = "Tomorrow likely green if you maintain rhythm."

    recovery = decline_prefix + recovery

    # Schedule outlook: tomorrow's calendar + weather
    schedule = ""
    try:
        tomorrow_date = date.today() + timedelta(days=1)
        from calendar_pull import get_events_for_date, find_free_blocks
        tmrw_events = get_events_for_date(tomorrow_date)
        tmrw_free = find_free_blocks(tmrw_events)

        # Tomorrow's weather
        tmrw_forecast = None
        try:
            from weather_pull import get_2day_forecast
            forecast_2d = get_2day_forecast()
            tmrw_forecast = forecast_2d.get("tomorrow", [])
        except Exception:
            pass

        if tmrw_forecast:
            # Daylight hours from forecast
            daylight_hours = {e["hour"] for e in tmrw_forecast if e.get("is_day")}
            weather_by_hour = {e["hour"]: e for e in tmrw_forecast}

            # Find first daylight free block with low precip
            found = False
            for fb in tmrw_free:
                fb_start_h = int(fb["start"].split(":")[0])
                fb_end_h = int(fb["end"].split(":")[0])
                if fb["duration_min"] < 20:
                    continue
                has_daylight = any(h in daylight_hours for h in range(fb_start_h, fb_end_h + 1))
                if not has_daylight:
                    continue
                precip_ok = all(weather_by_hour.get(h, {}).get("precip_prob", 0) < 60
                               for h in range(fb_start_h, fb_end_h + 1))
                if not precip_ok:
                    continue
                temp = weather_by_hour.get(fb_start_h, {}).get("temp_c")
                from weather_pull import weather_summary
                tmrw_ws = weather_summary(tmrw_forecast)
                cond = (tmrw_ws.get("conditions") or "").replace("_", " ")
                temp_str = f"{temp:.0f}\u00B0C, " if temp is not None else ""
                schedule = f"Tomorrow's outdoor window: {fb['start']}-{fb['end']}, {temp_str}{cond}."
                found = True
                break

            if not found:
                schedule = "Tomorrow's calendar is full through daylight; plan light differently."
        elif tmrw_free:
            # No weather, but have calendar
            for fb in tmrw_free:
                fb_start_h = int(fb["start"].split(":")[0])
                if 7 <= fb_start_h <= 17 and fb["duration_min"] >= 20:
                    schedule = f"Tomorrow's outdoor window: {fb['start']}-{fb['end']}."
                    break
            if not schedule:
                schedule = "Tomorrow's calendar is full through daylight; plan light differently."
        else:
            schedule = "Tomorrow's calendar is full through daylight; plan light differently."
    except Exception:
        schedule = ""

    return {
        "recovery_outlook": recovery,
        "schedule_outlook": schedule,
    }


def _get_brief() -> dict:
    today_str = str(date.today())

    if today_str in _brief_cache:
        return _brief_cache[today_str]

    data = _pull_data()
    target = today_str if today_str in data else max(data.keys())

    # Pull calendar and weather (graceful failures)
    events, free_blocks, calendar_error = _pull_calendar()
    forecast, w_summary, weather_error = _pull_weather()

    # Build brief with context
    brief = build_brief(data, target,
                        events=events if not calendar_error else None,
                        free_blocks=free_blocks if not calendar_error else None,
                        forecast=forecast if not weather_error else None)

    if "error" in brief:
        raise HTTPException(status_code=500, detail=brief["error"])

    # Build plan
    plan = []
    try:
        from scheduler import plan_day
        plan = plan_day(brief, events, free_blocks, forecast)
    except Exception:
        pass

    # Add cycle context to brief for narration
    try:
        from cycle import get_current_phase, get_phase_label
        phase = get_current_phase()
        if phase:
            brief["cycle"] = {
                "phase": phase["phase"],
                "day_of_cycle": phase["day_of_cycle"],
                "label": get_phase_label(phase),
            }
    except Exception:
        pass

    client = _get_client()
    narration = narrate_brief(client, brief, data)

    # Tomorrow projection
    tomorrow_projection = None
    try:
        tomorrow_projection = _compute_tomorrow_projection(data, brief, events, plan)
    except Exception:
        pass

    # Cycle context
    cycle_info = None
    try:
        from cycle import get_current_phase, get_phase_label, detect_personal_patterns
        phase = get_current_phase()
        if phase:
            cycle_info = {
                "phase": phase["phase"],
                "day_of_cycle": phase["day_of_cycle"],
                "label": get_phase_label(phase),
                "predicted_next": phase["predicted_next_start"],
                "pattern_observation": detect_personal_patterns(),
            }
    except Exception:
        pass

    result = {
        "date": brief["date"],
        "readiness_state": brief["readiness_state"],
        "narration": narration,
        "signals": brief["signals"],
        "recommendations": brief["recommendations"],
        "history": _build_history(data, target),
        "events": events,
        "free_blocks": free_blocks,
        "plan": plan,
        "weather_summary": w_summary,
        "calendar_error": calendar_error,
        "weather_error": weather_error,
        "tomorrow_projection": tomorrow_projection,
        "cycle": cycle_info,
        "demo_mode": is_demo(),
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }

    _brief_cache[today_str] = result
    _save_brief_to_disk(today_str, result)
    return result


def _build_week_day(date_str: str, data: dict, is_today: bool) -> dict:
    """Build a single day entry for the week view."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = d.strftime("%A")

    # For today, reuse the full brief
    if is_today:
        try:
            full = _get_brief()
            return {
                "date": date_str,
                "weekday": weekday,
                "readiness_state": full["readiness_state"],
                "verdict": full["narration"].split(".")[0] + "." if full.get("narration") else "",
                "signals_summary": summarize_signals(full.get("signals", {})),
                "events": full.get("events", []),
                "plan": full.get("plan", []),
                "weather_summary": full.get("weather_summary"),
            }
        except Exception:
            pass

    # Try disk cache first
    cached = _load_brief_from_disk(date_str)
    if cached:
        return {
            "date": date_str,
            "weekday": weekday,
            "readiness_state": cached.get("readiness_state", "green"),
            "verdict": (cached.get("narration", "").split(".")[0] + ".") if cached.get("narration") else "",
            "signals_summary": summarize_signals(cached.get("signals", {})),
            "events": cached.get("events", []),
            "plan": cached.get("plan", []),
            "weather_summary": cached.get("weather_summary"),
        }

    # Compute from Oura data (no calendar/weather for historical days)
    if date_str in data:
        brief = build_brief_for_date(data, date_str)
        if "error" not in brief:
            # Save minimal version to disk
            _save_brief_to_disk(date_str, brief)
            return {
                "date": date_str,
                "weekday": weekday,
                "readiness_state": brief["readiness_state"],
                "verdict": "",
                "signals_summary": summarize_signals(brief.get("signals", {})),
                "events": [],
                "plan": [],
                "weather_summary": None,
            }

    # No data available
    return {
        "date": date_str,
        "weekday": weekday,
        "readiness_state": "pending",
        "verdict": "",
        "signals_summary": "",
        "events": [],
        "plan": [],
        "weather_summary": None,
    }


@app.get("/api/brief")
def get_brief():
    return _get_brief()


@app.get("/api/brief/audio")
def get_brief_audio():
    """Return the morning brief narration as spoken MP3 audio (ElevenLabs)."""
    brief = _get_brief()
    narration = brief.get("narration")
    if not narration:
        raise HTTPException(status_code=404, detail="No narration available")

    cache_key = brief.get("date")
    audio = _audio_cache.get(cache_key)
    if audio is None:
        from tts import synthesize_speech, TTSError
        try:
            audio = synthesize_speech(narration)
        except TTSError as e:
            raise HTTPException(status_code=502, detail=str(e))
        _audio_cache[cache_key] = audio

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f'inline; filename="soma-brief-{cache_key}.mp3"',
            "Cache-Control": "no-store",
        },
    )


def _greeting_text(narration: str) -> str:
    """First sentence of the narration — the short hero 'greeting' line.

    Mirrors the frontend's hero-verdict logic so the spoken greeting matches
    the text shown at the top of the page.
    """
    m = re.match(r"[^.!?]+[.!?]", narration or "")
    return m.group(0) if m else (narration or "")[:80]


@app.get("/api/brief/greeting/audio")
def get_brief_greeting_audio():
    """Return just the greeting line (hero verdict) as spoken MP3 audio.

    Played automatically when the page loads. Synthesizes only the first
    sentence of the narration rather than the full brief.
    """
    brief = _get_brief()
    narration = brief.get("narration")
    if not narration:
        raise HTTPException(status_code=404, detail="No narration available")

    text = _greeting_text(narration)
    cache_key = f"greeting:{brief.get('date')}"
    audio = _audio_cache.get(cache_key)
    if audio is None:
        from tts import synthesize_speech, TTSError
        try:
            audio = synthesize_speech(text)
        except TTSError as e:
            raise HTTPException(status_code=502, detail=str(e))
        _audio_cache[cache_key] = audio

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f'inline; filename="soma-greeting-{brief.get("date")}.mp3"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/week")
def get_week():
    today_str = str(date.today())

    if today_str in _week_cache:
        return _week_cache[today_str]

    data = _pull_data()
    today = date.today()

    # Monday through Sunday of the current week
    monday = today - timedelta(days=today.weekday())
    days_list = []
    for i in range(7):
        d = monday + timedelta(days=i)
        d_str = str(d)
        is_today = (d == today)
        is_future = (d > today)

        if is_future:
            weekday = d.strftime("%A")
            days_list.append({
                "date": d_str,
                "weekday": weekday,
                "readiness_state": "pending",
                "verdict": "",
                "signals_summary": "",
                "events": [],
                "plan": [],
                "weather_summary": None,
            })
        else:
            days_list.append(_build_week_day(d_str, data, is_today))

    result = {"days": days_list}
    _week_cache[today_str] = result
    return result


CHAT_SYSTEM_PROMPT = (
    "You are Soma, a clinical longevity assistant. You answer questions about the "
    "user's biometric data using only the data provided in this prompt. You cite "
    "specific numbers. You never recommend supplements, medications, or training "
    "prescriptions. When asked about cycle effects, you describe what this user's "
    "data shows, never what is generally true of women. If the data doesn't support "
    "an answer, you say so. Keep responses under 80 words."
)


def _build_chat_context(brief: dict, data: dict) -> str:
    """Build structured context from all available data sources."""
    today_str = brief["date"]

    # 14-day signal history table
    keys = sorted(k for k in data if k <= today_str)[-14:]
    history_lines = []
    for k in keys:
        d = data[k]
        history_lines.append(
            f"  {k}: sleep={d.get('sleep_score')} deep={d.get('deep_sleep_score')} "
            f"hrv={d.get('hrv_balance')} readiness={d.get('readiness_score')} "
            f"efficiency={d.get('efficiency_score')} steps={d.get('steps')}"
        )

    # Today's signals with baselines
    signals_detail = []
    for sig_name, sig in brief.get("signals", {}).items():
        if sig.get("value") is None:
            continue
        bl = round(sig["baseline_mean"], 1) if sig.get("baseline_mean") else "—"
        z = sig.get("z_score", "—")
        sev = sig.get("severity", "—")
        signals_detail.append(f"  {sig_name}: today={sig['value']}, baseline={bl}, z={z}, status={sev}")

    # Assemble context
    sections = [
        f"DATE: {today_str}",
        f"VERDICT: {brief['readiness_state']}",
        "",
        "TODAY'S SIGNALS (vs 13-day baseline):",
        *signals_detail,
        "",
        "14-DAY HISTORY:",
        *history_lines,
    ]

    # Calendar
    events = brief.get("events") or []
    if events:
        event_lines = [f"  {e.get('start_time','?')}–{e.get('end_time','?')} {e.get('title','(untitled)')}"
                       for e in events if not e.get("all_day")]
        sections += ["", "TODAY'S CALENDAR:", *event_lines]

    # Weather
    ws = brief.get("weather_summary")
    if ws:
        sections += ["", f"WEATHER: {ws.get('temp_low','')}–{ws.get('temp_high','')}°C, "
                     f"{ws.get('conditions','')}, {ws.get('precip_chance_max',0)}% precip chance"]

    # Cycle
    cycle = brief.get("cycle")
    if cycle:
        sections += ["", f"CYCLE: {cycle.get('label','—')}"]
        if cycle.get("pattern_observation"):
            sections.append(f"  Pattern: {cycle['pattern_observation']}")

    # Tomorrow
    tmrw = brief.get("tomorrow_projection")
    if tmrw:
        sections += ["", "TOMORROW PROJECTION:",
                     f"  {tmrw.get('recovery_outlook','')} {tmrw.get('schedule_outlook','')}"]

    # Narration (the LLM-generated brief)
    sections += ["", "TODAY'S BRIEF:", brief.get("narration", "")]

    return "\n".join(sections)


class ChatRequest(BaseModel):
    question: str


@app.post("/api/chat")
def chat(req: ChatRequest):
    cached = _get_brief()
    data = _pull_data()
    client = _get_client()

    context = _build_chat_context(cached, data)

    messages = [
        {"role": "user", "content": f"Here is my complete biometric context:\n\n{context}"},
        {"role": "assistant", "content": "I have your data. What would you like to know?"},
        {"role": "user", "content": req.question},
    ]

    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=CHAT_SYSTEM_PROMPT,
        messages=messages,
    )

    return {"answer": response.content[0].text}


@app.get("/api/patterns")
def get_patterns():
    """Analyze 28-day Oura data for statistically significant patterns."""
    data = _pull_data()
    keys = sorted(data.keys())[-28:]
    if len(keys) < 7:
        return {"observations": [], "date_range": None, "days_analyzed": len(keys)}

    from patterns import analyze_patterns
    observations = analyze_patterns(data, keys)

    return {
        "observations": observations,
        "date_range": {"start": keys[0], "end": keys[-1]},
        "days_analyzed": len(keys),
    }


@app.get("/api/export/clinical-record")
async def export_clinical_record():
    """Generate and return the clinical record PDF."""
    data = _pull_data()

    # Gather cycle info
    cycle_info = None
    try:
        from cycle import get_current_phase, detect_personal_patterns
        phase = get_current_phase()
        if phase:
            cycle_info = {
                "phase": phase["phase"],
                "day_of_cycle": phase["day_of_cycle"],
                "average_length": phase["average_length"],
                "predicted_next_start": phase["predicted_next_start"],
                "pattern_observation": detect_personal_patterns(),
            }
    except Exception:
        pass

    from pdf_export import generate_pdf
    pdf_bytes = await generate_pdf(data, cycle_info)

    filename = f"soma-clinical-record-{date.today()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/patterns")
def patterns_page():
    return FileResponse("static/patterns.html")


# ── Clinic endpoints ────────────────────────────────────────────────

@app.get("/api/clinic/patients")
def clinic_patients():
    """Return the full patient population for the clinic dashboard."""
    data = _pull_data()
    patients = build_population(data)
    # Summary stats
    states = {"green": 0, "steady": 0, "watch": 0, "red": 0}
    flagged = 0
    for p in patients:
        states[p["state"]] = states.get(p["state"], 0) + 1
        if p["flag"]:
            flagged += 1
    return {
        "patients": patients,
        "total": len(patients),
        "flagged": flagged,
        "states": states,
        "date": str(date.today()),
        "demo_mode": is_demo(),
    }


@app.get("/api/clinic/patient/{patient_id}")
def clinic_patient_detail(patient_id: str):
    """Return a single patient's full data."""
    data = _pull_data()
    patient = get_patient(patient_id, data)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


class BriefRequest(BaseModel):
    focus: str = ""


@app.post("/api/clinic/patient/{patient_id}/brief")
def clinic_generate_brief(patient_id: str, req: BriefRequest):
    """Generate a pre-visit clinical brief for a patient using Claude."""
    data = _pull_data()
    patient = get_patient(patient_id, data)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Build clinical context
    signals = patient["today_signals"]
    history = patient["history"]
    adherence = patient["adherence"]

    context_lines = [
        f"PATIENT: {patient['name']}, age {patient['age']}",
        f"Days on Soma: {patient['days_on_soma']}",
        f"Current state: {patient['state']}",
        f"Cycle: day {patient['cycle']['day']}, {patient['cycle']['phase']} phase",
        "",
        "TODAY'S SIGNALS:",
        f"  Sleep score: {signals.get('sleep_score')}",
        f"  HRV balance: {signals.get('hrv_balance')}",
        f"  Readiness score: {signals.get('readiness_score')}",
        f"  Deep sleep score: {signals.get('deep_sleep_score')}",
        "",
        "PROTOCOL ADHERENCE:",
    ]
    for a in adherence:
        context_lines.append(f"  {a['protocol']}: {a['adherence_pct']}% (target: {a['target']})")

    if patient["flag"]:
        context_lines += ["", f"FLAG: {patient['flag_detail']}"]

    # Last 7 days of history
    context_lines += ["", "LAST 7 DAYS:"]
    dates = history["dates"][-7:]
    for i, d in enumerate(dates):
        idx = len(history["dates"]) - 7 + i
        if idx < 0:
            continue
        context_lines.append(
            f"  {d}: sleep={history['sleep_score'][idx]} hrv={history['hrv_balance'][idx]} "
            f"readiness={history['readiness_score'][idx]} deep={history['deep_sleep_score'][idx]}"
        )

    context = "\n".join(context_lines)

    system = (
        "You are a clinical longevity assistant writing a pre-visit brief for a clinician. "
        "Structure your brief as:\n"
        "1. ONE-LINE SUMMARY: A single sentence capturing the patient's current state.\n"
        "2. KEY OBSERVATIONS: 2-3 bullet points about notable trends or flags.\n"
        "3. SUGGESTED DISCUSSION POINTS: 2-3 items the clinician might raise in the visit.\n\n"
        "Be specific with numbers. Do not recommend medications or supplements. "
        "Keep the entire brief under 200 words."
    )

    if req.focus:
        system += f"\n\nThe clinician wants to focus on: {req.focus}"

    client = _get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": context}],
    )

    return {"brief": response.content[0].text}


@app.get("/clinic/patient/{patient_id}")
def clinic_patient_page(patient_id: str):
    return FileResponse("static/clinic-patient.html")


@app.get("/clinic")
def clinic_page():
    return FileResponse("static/clinic.html")


# Static files last so API routes take priority
app.mount("/", StaticFiles(directory="static", html=True), name="static")
