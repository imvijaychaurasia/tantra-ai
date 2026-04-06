"""
tantra-media — TTS engine
तंत्र  ·  Text-to-Speech using OpenAI TTS API

Generates narration MP3 files for each scene from the script's narration field.
OpenAI TTS produces high-quality neural voices and works from any server IP
(unlike edge-tts which is blocked by Microsoft from datacenter/VPS IPs).

Voice selection:
  TANTRA_MEDIA_VOICE env var — any OpenAI TTS voice name
  Default: onyx (male, deep, authoritative — ideal for tech content)

Available OpenAI TTS voices:
  alloy    — Neutral, versatile
  echo     — Male, engaging
  fable    — Expressive, warm
  onyx     — Male, deep, authoritative (default)
  nova     — Female, warm, professional
  shimmer  — Female, clear, bright

Model:
  TANTRA_MEDIA_TTS_MODEL env var — tts-1 (fast) or tts-1-hd (higher quality)
  Default: tts-1
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("tantra-media.tts")

# OpenAI TTS configuration — resolved from env at call time (not module load)
DEFAULT_VOICE = os.getenv("TANTRA_MEDIA_VOICE", "onyx")
TTS_MODEL = os.getenv("TANTRA_MEDIA_TTS_MODEL", "tts-1")


def generate_scene_audio(
    narration: str,
    output_path: Path,
    voice: str | None = None,
) -> float:
    """
    Synchronous: generate TTS audio for a scene narration using OpenAI TTS.

    Fully synchronous — no asyncio involved, safe to call from any context
    including FastAPI async handlers via run_in_executor().

    Args:
        narration:   Scene narration text from the script JSON.
        output_path: Where to write the .mp3 file.
        voice:       OpenAI TTS voice name (default: onyx).

    Returns:
        Estimated audio duration in seconds.
    """
    from openai import OpenAI

    voice = voice or DEFAULT_VOICE
    model = TTS_MODEL
    output_path.parent.mkdir(parents=True, exist_ok=True)

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set — cannot generate TTS audio. "
            "Add OPENAI_API_KEY to your .env file."
        )

    try:
        client = OpenAI(api_key=api_key)
        with client.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,          # type: ignore[arg-type]
            input=narration,
            response_format="mp3",
        ) as response:
            response.stream_to_file(str(output_path))

        # Estimate duration from word count (~150 wpm ≈ 2.5 words/sec)
        words = len(narration.split())
        estimated_duration = max(words / 2.5, 2.0)
        log.info("TTS ✓ %s via OpenAI/%s (%.1fs estimated)", output_path.name, voice, estimated_duration)
        return estimated_duration

    except Exception as exc:
        log.error("TTS failed for %s: %s", output_path.name, exc)
        raise


def get_actual_audio_duration(audio_path: Path) -> float:
    """
    Get the actual duration of an MP3 file using ffprobe.
    Falls back to estimation if ffprobe is unavailable.
    """
    import subprocess
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(result.stdout.strip())
        return duration
    except Exception:
        # Fallback: estimate from file size (MP3 ~128kbps ≈ 16KB/s)
        size_bytes = audio_path.stat().st_size
        return max(size_bytes / 16000, 2.0)
