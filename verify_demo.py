"""Verify that DEMO_MODE seals off ALL real personal data.

Strategy: turn DEMO_MODE on, then instrument the process so that any read of a
real data file or any network call is recorded as a leak. Then exercise every
endpoint (brief, week, patterns, clinic) and assert nothing leaked and the
clinic never contains the real user.

Run:  DEMO_MODE=true ./venv/bin/python verify_demo.py
Exit code is non-zero if any leak or failed check is detected.
"""

import builtins
import os
import sys
from pathlib import Path

# Force demo on and drop the Oura token to prove demo doesn't need it.
os.environ["DEMO_MODE"] = "true"
os.environ.pop("OURA_TOKEN", None)

REPO = Path(__file__).resolve().parent
DATA = REPO / "data"

# Real personal-data files/dirs that must never be touched in demo mode.
REAL_FILES = {
    str((DATA / "cache.json").resolve()),
    str((DATA / "cycle_log.json").resolve()),
    str((DATA / "weather_cache.json").resolve()),
    str((DATA / "weather_cache_2day.json").resolve()),
    str((REPO / "credentials.json").resolve()),
    str((REPO / "token.json").resolve()),
}
REAL_BRIEFS_DIR = str((DATA / "briefs").resolve())
REAL_BRIEFS_PREFIX = REAL_BRIEFS_DIR + os.sep

leaks = []

# 1. Guard every file open against the real-data set.
_real_open = builtins.open


def _guarded_open(file, *a, **k):
    try:
        p = str(Path(file).resolve())
    except Exception:
        p = str(file)
    if p in REAL_FILES or p == REAL_BRIEFS_DIR or p.startswith(REAL_BRIEFS_PREFIX):
        leaks.append(f"opened real file: {p}")
    return _real_open(file, *a, **k)


builtins.open = _guarded_open

# 2. Guard the network. In demo, no gated source should reach the wire.
import requests


def _deny(method):
    def f(url, *a, **k):
        leaks.append(f"network {method} {url}")
        raise AssertionError(f"network blocked in demo: {url}")
    return f


requests.get = _deny("GET")
requests.post = _deny("POST")

# 3. Import the app, then stub the Claude narration (not personal data; avoids
#    an Anthropic call and API cost during verification).
import api  # noqa: E402

api._get_client = lambda: None
api.narrate_brief = lambda client, brief, data: "Synthetic demo narration. Green today."

import demo  # noqa: E402
from fastapi import HTTPException  # noqa: E402

# Also block direct Google Calendar auth as a backstop.
import calendar_pull  # noqa: E402


def _no_google():
    leaks.append("calendar_pull.authenticate() called")
    raise AssertionError("Google auth blocked in demo")


calendar_pull.authenticate = _no_google

checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))


check("is_demo() is True", demo.is_demo())

brief = api._get_brief()
check("brief.demo_mode is True", brief.get("demo_mode") is True)
check("brief built", brief.get("readiness_state") in ("green", "yellow", "red"))

pop = api.clinic_patients()
ids = sorted(p["id"] for p in pop["patients"])
check("clinic = synthetic A-D only",
      ids == ["patient-a", "patient-b", "patient-c", "patient-d"], str(ids))
check("clinic exposes demo_mode", pop.get("demo_mode") is True)
names = [p["name"] for p in pop["patients"]]
check("no 'Patient E' (real user) in clinic", "Patient E" not in names, str(names))

try:
    api.clinic_patient_detail("patient-e")
    check("patient-e -> 404", False, "resolved a patient")
except HTTPException as e:
    check("patient-e -> 404", e.status_code == 404)

wk = api.get_week()
check("week built (7 days)", len(wk.get("days", [])) == 7)

pat = api.get_patterns()
check("patterns built", "observations" in pat)

# Sanity: confirm the served numbers are the synthetic ones.
synth = demo.synthetic_oura()
today = brief["date"]
served = brief["signals"]["sleep_score"]["value"]
check("served data == synthetic profile",
      served == synth.get(today, {}).get("sleep_score"),
      f"served={served} synthetic={synth.get(today, {}).get('sleep_score')}")

builtins.open = _real_open  # restore before printing

ok_all = all(ok for _, ok, _ in checks) and not leaks

print("DEMO_MODE leak verification")
print("=" * 48)
for name, ok, detail in checks:
    suffix = f"  -> {detail}" if detail and not ok else ""
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")
print("-" * 48)
if leaks:
    print("LEAKS DETECTED:")
    for leak in leaks:
        print("  !", leak)
else:
    print("No real files opened. No network calls. Sealed.")
print("=" * 48)
print("RESULT:", "PASS" if ok_all else "FAIL")
sys.exit(0 if ok_all else 1)
