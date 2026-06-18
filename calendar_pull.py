"""Google Calendar integration. Read-only access to today's events."""

import os
from datetime import datetime, timedelta

from demo import is_demo, synthetic_events
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
CREDS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")


def authenticate():
    """Return valid Credentials. Refreshes silently if possible, else raises."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError(
                "No valid token.json. Run 'python test_oauth.py' to authenticate."
            )
    return creds


def _tz_suffix():
    """Compute local timezone offset string like +05:00 or -04:00."""
    import time as _time
    utc_offset = -(_time.timezone if _time.daylight == 0 else _time.altzone)
    offset_h, offset_m = divmod(abs(utc_offset) // 60, 60)
    sign = "+" if utc_offset >= 0 else "-"
    return f"{sign}{offset_h:02d}:{offset_m:02d}"


def _parse_events(raw_items: list) -> list:
    """Parse Google Calendar API items into our event format."""
    events = []
    for ev in raw_items:
        attendees = ev.get("attendees", [])
        self_status = None
        for a in attendees:
            if a.get("self"):
                self_status = a.get("responseStatus")
        if self_status == "declined":
            continue

        start_raw = ev["start"].get("dateTime")
        end_raw = ev["end"].get("dateTime")
        all_day = start_raw is None

        if all_day:
            events.append({
                "start_time": None,
                "end_time": None,
                "title": ev.get("summary", "(no title)"),
                "all_day": True,
            })
        else:
            start_dt = datetime.fromisoformat(start_raw)
            end_dt = datetime.fromisoformat(end_raw)
            events.append({
                "start_time": start_dt.strftime("%H:%M"),
                "end_time": end_dt.strftime("%H:%M"),
                "title": ev.get("summary", "(no title)"),
                "all_day": False,
            })
    return sorted(events, key=lambda e: e["start_time"] or "00:00")


def get_todays_events() -> list:
    """Return today's events sorted by start time. Skips declined events."""
    return get_events_for_date(datetime.now().date())


def get_events_for_date(target_date) -> list:
    """Return events for a specific date. target_date is a date object."""
    # DEMO_MODE: synthetic events only, never authenticate or call Google.
    if is_demo():
        return synthetic_events(target_date)

    creds = authenticate()
    service = build("calendar", "v3", credentials=creds)

    start_of_day = datetime(target_date.year, target_date.month, target_date.day)
    end_of_day = start_of_day + timedelta(days=1)

    tz = _tz_suffix()
    time_min = start_of_day.strftime("%Y-%m-%dT%H:%M:%S") + tz
    time_max = end_of_day.strftime("%Y-%m-%dT%H:%M:%S") + tz

    result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    return _parse_events(result.get("items", []))


def find_free_blocks(events, day_start="07:00", day_end="22:30", min_block_minutes=20) -> list:
    """Find free time gaps of at least min_block_minutes. Ignores all-day events."""
    timed = [e for e in events if not e["all_day"] and e["start_time"] and e["end_time"]]
    timed.sort(key=lambda e: e["start_time"])

    def to_min(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)

    def to_hm(m):
        return f"{m // 60:02d}:{m % 60:02d}"

    blocks = []
    cursor = to_min(day_start)
    end = to_min(day_end)

    for ev in timed:
        ev_start = to_min(ev["start_time"])
        ev_end = to_min(ev["end_time"])
        if ev_start > cursor:
            gap = ev_start - cursor
            if gap >= min_block_minutes:
                blocks.append({
                    "start": to_hm(cursor),
                    "end": to_hm(ev_start),
                    "duration_min": gap,
                })
        cursor = max(cursor, ev_end)

    if end > cursor:
        gap = end - cursor
        if gap >= min_block_minutes:
            blocks.append({
                "start": to_hm(cursor),
                "end": to_hm(end),
                "duration_min": gap,
            })

    return blocks
