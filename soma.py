"""Soma: longevity autopilot. Orchestrator that pulls data, builds brief, narrates via Claude."""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile

from dotenv import load_dotenv

load_dotenv()

import anthropic

from brief import build_brief
from oura_pull import pull, load_cache

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are Soma, a concise longevity autopilot. Your job is to narrate a structured brief into plain language. You do NOT generate recommendations, diagnoses, or advice. Every recommendation you mention must come directly from the JSON brief provided. Do not invent, add, or embellish recommendations beyond what the brief contains.

Style rules:
- Direct, Paul Graham style prose. Short sentences. No filler.
- Never use em-dashes (the long dash). Use commas, periods, or semicolons instead.
- No bullet points in the morning brief. Write in paragraphs.
- No medical disclaimers or hedge language.
- No wellness influencer tone. No "listen to your body" or "honor your rest."
- Open with a one-sentence verdict that translates the readiness state (green/yellow/red) into plain language.
- Cap the morning brief at 180 words.
- When recommendations mention specific times (like "Walk at 11:30" or "Lift at 16:00" or "Lunch at 12:30"), use those exact times in the narration. The brief knows the user's calendar, so reference the specific times and event titles from context_notes. The narration should sound like it knows the schedule, not like generic advice.
- If a recommendation has a context_note, reference it naturally in the narration. Do not list every adaptation; weave the most important one or two in.
- If cycle context is available AND the day's signals suggest a connection (e.g. lower HRV in late luteal), the narrator MAY mention this as observation, NEVER as prescription. Example: "HRV is down 6 points; you're in late luteal phase, which can amplify the dip." Do NOT prescribe phase-specific training or nutrition. Do NOT use phrases like "because you're in your luteal phase, you should..." Phase is context, not cause.

When answering follow-up questions:
- Ground every answer in the brief JSON and the 14-day data provided.
- If the data does not contain enough information to answer, say so directly.
- Stay concise. You are a tool, not a companion."""


def play_audio(audio_bytes: bytes) -> None:
    """Write MP3 bytes to a temp file and play through the OS audio player."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio_bytes)
        path = f.name

    system = platform.system()
    cmd = None
    if system == "Darwin":
        cmd = ["afplay", path]
    elif system == "Windows":
        cmd = ["powershell", "-NoProfile", "-Command",
               f"(New-Object Media.SoundPlayer '{path}').PlaySync()"]
    else:
        for player in ("ffplay", "mpg123", "play", "aplay"):
            if shutil.which(player):
                cmd = [player, "-nodisp", "-autoexit", path] if player == "ffplay" else [player, path]
                break

    if not cmd:
        print(f"No audio player found. Brief audio saved to {path}")
        return

    try:
        subprocess.run(cmd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Could not play audio ({e}). Saved to {path}")
        return

    try:
        os.remove(path)
    except OSError:
        pass


def narrate_brief(client, brief: dict, data: dict) -> str:
    """Send the brief to Claude for narration."""
    user_msg = (
        "Here is today's structured brief. Narrate it as the morning brief.\n\n"
        f"BRIEF:\n{json.dumps(brief, indent=2)}\n\n"
        f"14-DAY DATA (for context, do not narrate all of it):\n{json.dumps(data, indent=2)}"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    return response.content[0].text


def interactive_chat(client, brief: dict, data: dict):
    """Interactive follow-up chat grounded in the brief and data."""
    context_msg = (
        f"BRIEF:\n{json.dumps(brief, indent=2)}\n\n"
        f"14-DAY DATA:\n{json.dumps(data, indent=2)}"
    )

    messages = [
        {"role": "user", "content": f"Context for this conversation:\n{context_msg}"},
        {"role": "assistant", "content": "Ready. Ask me anything about your data."},
    ]

    print("\n--- Follow-up chat (type 'quit' to exit) ---\n")

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nDone.")
            break

        if not question or question.lower() in ("quit", "exit", "q"):
            print("Done.")
            break

        messages.append({"role": "user", "content": question})

        response = client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        reply = response.content[0].text
        messages.append({"role": "assistant", "content": reply})
        print(f"\nSoma: {reply}\n")


def handle_log_command(args):
    """Handle 'log period start/end [date]' commands."""
    if len(args) < 2 or args[0] != "period":
        print("Usage: python soma.py log period start [YYYY-MM-DD]")
        print("       python soma.py log period end [YYYY-MM-DD]")
        return

    from cycle import log_period_start, log_period_end, get_current_phase, get_phase_label

    action = args[1]
    date_str = args[2] if len(args) > 2 else None

    if action == "start":
        log = log_period_start(date_str)
        print(f"Period start logged: {date_str or 'today'}")
        phase = get_current_phase()
        if phase:
            print(f"Current phase: {get_phase_label(phase)}")
    elif action == "end":
        log = log_period_end(date_str)
        print(f"Period end logged: {date_str or 'today'}")
    else:
        print(f"Unknown action: {action}. Use 'start' or 'end'.")


def main():
    # Check for subcommands
    if len(sys.argv) > 1 and sys.argv[1] == "log":
        handle_log_command(sys.argv[2:])
        return

    from demo import is_demo
    demo = is_demo()

    token = os.environ.get("OURA_TOKEN")
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not token and not demo:
        print("Set OURA_TOKEN in .env")
        sys.exit(1)
    if not api_key:
        print("Set ANTHROPIC_API_KEY in .env")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Pull fresh data
    print("Running in DEMO_MODE: synthetic data only." if demo else "Pulling Oura data...")
    try:
        data = pull(token)
    except Exception as e:
        print(f"Oura pull failed: {e}")
        print("Trying cached data...")
        data = load_cache()
        if not data:
            print("No cached data available. Cannot proceed.")
            sys.exit(1)

    print(f"Got {len(data)} days of data.")

    # Build brief
    from datetime import date
    today_str = str(date.today())
    if today_str not in data:
        today_str = max(data.keys())
        print(f"No data for today yet, using most recent: {today_str}")

    brief = build_brief(data, today_str)

    if "error" in brief:
        print(f"Brief error: {brief['error']}")
        sys.exit(1)

    print(f"\nReadiness: {brief['readiness_state'].upper()}\n")

    # Narrate
    print("Generating morning brief...\n")
    narrative = narrate_brief(client, brief, data)
    print(narrative)

    # Speak the brief via ElevenLabs (best-effort; never blocks the CLI)
    try:
        from tts import synthesize_speech, TTSError
        print("\nGenerating audio...")
        audio = synthesize_speech(narrative)
        play_audio(audio)
    except TTSError as e:
        print(f"(Audio unavailable: {e})")
    except Exception as e:
        print(f"(Audio playback skipped: {e})")

    # Interactive chat
    interactive_chat(client, brief, data)


if __name__ == "__main__":
    main()
