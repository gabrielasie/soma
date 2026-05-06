"""Soma: longevity autopilot. Orchestrator that pulls data, builds brief, narrates via Claude."""

import json
import os
import sys

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

When answering follow-up questions:
- Ground every answer in the brief JSON and the 14-day data provided.
- If the data does not contain enough information to answer, say so directly.
- Stay concise. You are a tool, not a companion."""


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


def main():
    token = os.environ.get("OURA_TOKEN")
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not token:
        print("Set OURA_TOKEN in .env")
        sys.exit(1)
    if not api_key:
        print("Set ANTHROPIC_API_KEY in .env")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Pull fresh data
    print("Pulling Oura data...")
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

    # Interactive chat
    interactive_chat(client, brief, data)


if __name__ == "__main__":
    main()
