"""Cycle tracking. Calendar-based phase estimation. Context only, no prescriptions."""

import json
import statistics
from datetime import date, timedelta
from pathlib import Path

from demo import is_demo, synthetic_cycle_log

CYCLE_LOG = Path(__file__).parent / "data" / "cycle_log.json"

PHASES = {
    "menstrual": (1, 5),
    "follicular": (6, 13),
    "ovulatory": (14, 16),
    "luteal": None,       # day 17 to (length - 4)
    "late_luteal": None,   # last 4 days
}

DEFAULT_LENGTH = 28


def _load_log():
    # DEMO_MODE: synthetic cycle history only, never read the real period log.
    if is_demo():
        return synthetic_cycle_log()
    if CYCLE_LOG.exists():
        with open(CYCLE_LOG) as f:
            return json.load(f)
    return []


def _save_log(log):
    # DEMO_MODE: never persist; the real period log must not be written.
    if is_demo():
        return
    CYCLE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(CYCLE_LOG, "w") as f:
        json.dump(log, f, indent=2)


def log_period_start(date_str=None):
    """Append a new cycle start to the log."""
    date_str = date_str or str(date.today())
    log = _load_log()

    # Close previous cycle if it has no end
    if log and not log[-1].get("end"):
        prev_start = date.fromisoformat(log[-1]["start"])
        new_start = date.fromisoformat(date_str)
        log[-1]["end"] = str(new_start - timedelta(days=1))
        log[-1]["length"] = (new_start - prev_start).days

    log.append({"start": date_str, "end": None, "length": None})
    _save_log(log)
    return log


def log_period_end(date_str=None):
    """Close the most recent cycle by setting end date."""
    date_str = date_str or str(date.today())
    log = _load_log()
    if not log:
        return log

    last = log[-1]
    last["end"] = date_str
    start = date.fromisoformat(last["start"])
    end = date.fromisoformat(date_str)
    last["length"] = (end - start).days + 1
    _save_log(log)
    return log


def _average_length(log):
    """Rolling average of completed cycle lengths."""
    lengths = [c["length"] for c in log if c.get("length") and c["length"] > 0]
    if len(lengths) >= 2:
        return round(statistics.mean(lengths[-6:]))
    return DEFAULT_LENGTH


def get_current_phase(today=None):
    """Return current cycle phase info or None if no log."""
    log = _load_log()
    if not log:
        return None

    today = today or date.today()
    if isinstance(today, str):
        today = date.fromisoformat(today)

    # Find the most recent cycle start on or before today
    current = None
    for entry in reversed(log):
        start = date.fromisoformat(entry["start"])
        if start <= today:
            current = entry
            break

    if not current:
        return None

    start = date.fromisoformat(current["start"])
    day_of_cycle = (today - start).days + 1
    avg_length = _average_length(log)

    # Determine phase
    if day_of_cycle <= 5:
        phase = "menstrual"
    elif day_of_cycle <= 13:
        phase = "follicular"
    elif day_of_cycle <= 16:
        phase = "ovulatory"
    elif day_of_cycle > avg_length - 4:
        phase = "late_luteal"
    else:
        phase = "luteal"

    predicted_next = start + timedelta(days=avg_length)

    return {
        "day_of_cycle": day_of_cycle,
        "phase": phase,
        "average_length": avg_length,
        "predicted_next_start": str(predicted_next),
    }


def get_phase_label(phase_info):
    """Return display label like 'DAY 18 LUTEAL'."""
    if not phase_info:
        return None
    day = phase_info["day_of_cycle"]
    phase = phase_info["phase"].replace("_", " ").upper()
    return f"DAY {day} {phase}"


def detect_personal_patterns(cycle_log=None, briefs_dir=None):
    """Detect correlations between cycle day and biometric signals.
    Returns a pattern observation string or None."""
    if cycle_log is None:
        cycle_log = _load_log()

    completed = [c for c in cycle_log if c.get("length") and c["length"] > 0]
    if len(completed) < 2:
        return None

    if briefs_dir is None:
        # In demo mode read only the synthetic briefs dir, never the real one.
        sub = "briefs_demo" if is_demo() else "briefs"
        briefs_dir = Path(__file__).parent / "data" / sub
    if not briefs_dir.exists():
        return None

    # Load all available briefs
    briefs = {}
    for p in briefs_dir.glob("*.json"):
        try:
            with open(p) as f:
                briefs[p.stem] = json.load(f)
        except Exception:
            continue

    if len(briefs) < 60:
        return None

    # Map each brief date to its cycle day
    day_signal_map = {}
    for entry in completed:
        start = date.fromisoformat(entry["start"])
        length = entry["length"]
        for d in range(length):
            dt = start + timedelta(days=d)
            dt_str = str(dt)
            if dt_str in briefs:
                cycle_day = d + 1
                signals = briefs[dt_str].get("signals", {})
                if cycle_day not in day_signal_map:
                    day_signal_map[cycle_day] = []
                day_signal_map[cycle_day].append(signals)

    if not day_signal_map:
        return None

    # Check late luteal (last 5 days) vs follicular (days 6-13) for key signals
    for sig_name in ["sleep_score", "hrv_balance", "resting_heart_rate_score"]:
        follicular_vals = []
        late_vals = []

        for entry in completed:
            length = entry["length"]
            start = date.fromisoformat(entry["start"])

            for d in range(length):
                dt_str = str(start + timedelta(days=d))
                cycle_day = d + 1
                if dt_str not in briefs:
                    continue
                sig = briefs[dt_str].get("signals", {}).get(sig_name, {})
                val = sig.get("value")
                if val is None:
                    continue

                if 6 <= cycle_day <= 13:
                    follicular_vals.append(val)
                elif cycle_day > length - 5:
                    late_vals.append(val)

        if len(follicular_vals) >= 4 and len(late_vals) >= 4:
            foll_avg = statistics.mean(follicular_vals)
            late_avg = statistics.mean(late_vals)
            diff = foll_avg - late_avg

            label = sig_name.replace("_score", "").replace("_balance", "").replace("_", " ").title()
            if label.lower() == "hrv":
                label = "HRV"
            if "resting heart rate" in label.lower():
                label = "resting heart rate"

            if abs(diff) > 4:
                direction = "lower" if diff > 0 else "higher"
                return (
                    f"Across the last {len(completed)} cycles, "
                    f"{label} has been {abs(diff):.0f} points {direction} "
                    f"in late luteal (days {completed[-1]['length'] - 4}-{completed[-1]['length']})."
                )

    return None
