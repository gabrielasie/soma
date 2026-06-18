"""ElevenLabs text-to-speech. Turns the narrated brief into spoken audio.

This does not generate prose. It takes text that Claude already wrote and
returns MP3 bytes. Reads ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID from env.
"""

import os

import requests

TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
DEFAULT_MODEL = "eleven_multilingual_v2"


class TTSError(RuntimeError):
    """Raised when speech synthesis is unavailable or fails."""


def synthesize_speech(text: str, voice_id: str = None, model_id: str = None) -> bytes:
    """Send narrated text to ElevenLabs and return MP3 audio bytes.

    Reads ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID from the environment.
    Raises TTSError if credentials are missing or the API call fails.
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise TTSError("ELEVENLABS_API_KEY not set")

    voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID")
    if not voice_id:
        raise TTSError("ELEVENLABS_VOICE_ID not set")

    if not text or not text.strip():
        raise TTSError("No text to synthesize")

    url = TTS_URL.format(voice_id=voice_id)
    headers = {
        "xi-api-key": api_key,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model_id or os.environ.get("ELEVENLABS_MODEL_ID", DEFAULT_MODEL),
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
    except requests.RequestException as e:
        raise TTSError(f"ElevenLabs request failed: {e}") from e

    if resp.status_code != 200:
        # ElevenLabs returns a JSON error body; surface a trimmed message.
        raise TTSError(f"ElevenLabs API error {resp.status_code}: {resp.text[:300]}")

    return resp.content
