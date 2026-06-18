"""Phase 1: Verify Google Calendar OAuth works. Run manually, confirm browser consent."""

import os
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
CREDS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")


def authenticate():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def main():
    print("Authenticating with Google Calendar...")
    creds = authenticate()
    print("Auth OK. Token saved to token.json.\n")

    service = build("calendar", "v3", credentials=creds)

    # List tomorrow's events as a sanity check
    tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    day_after = tomorrow + timedelta(days=1)
    time_min = tomorrow.isoformat() + "Z"
    time_max = day_after.isoformat() + "Z"

    print(f"Fetching events for {tomorrow.strftime('%Y-%m-%d')}...")
    result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = result.get("items", [])
    if not events:
        print("No events found for tomorrow (this is fine).")
    else:
        print(f"Found {len(events)} event(s):")
        for ev in events:
            start = ev["start"].get("dateTime", ev["start"].get("date"))
            print(f"  {start}  {ev.get('summary', '(no title)')}")

    print("\nPhase 1 complete. OAuth is working.")


if __name__ == "__main__":
    main()
