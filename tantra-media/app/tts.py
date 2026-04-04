"""
tantra-media — TTS engine
तंत्र  ·  Text-to-Speech using Microsoft edge-tts

Generates narration MP3 files for each scene from the script's narration field.
edge-tts uses Microsoft Azure Neural TTS (free, no API key, no model download).

Voice selection:
  TANTRA_MEDIA_VOICE env var — any edge-tts voice name
  Default: en-US-GuyNeural (male, natural, good for tech content)

Quality voices for tech YouTube:
  en-US-GuyNeural       — Male, clear, natural (default)
  en-US-JennyNeural     — Female, warm, professional
  en-US-DavisNeural     — Male, deep, authoritative
  en-US-TonyNeural      — Male, energetic, enthusiastic
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import edge_tts

log = logging.getLogger("tantra-media.tts")

# Default voice — can override via env
DEFAULT_VOICE = os.getenv("TANTRA_MEDIA_VOICE", "en-US-GuyNeural")
# Speech rate adjustment (e.g. "+10%" for slightly faster delivery)
SPEECH_RATE = os.getenv("TANTRA_MEDIA_SPEECH_RATE", "+5%")


async def _synthesise(text: str, voice: str, output_path: Path) -> float:
    """
    Synthesise text to speech and save to output_path (.mp3).
    Returns audio duration in seconds (estimated from word count).
    """
    communicate = edge_tts.Communicate(text, voice, rate=SPEECH_RATE)
    await communicate.save(str(output_path))

    # Estimate duration: edge-tts doesn't return duration directly.
    # Average speaking rate ~145 words/min with +5% rate ≈ 152 wpm.
    words = len(text.split())
    estimated_duration = max(words / 2.5, 2.0)  # 150 wpm ≈ 2.5 words/sec
    log.debug("TTS generated %s: %d words → %.1fs", output_path.name, words, estimated_duration)
    return estimated_duration


def generate_scene_audio(
    narration: str,
    output_path: Path,
    voice: str | None = None,
) -> float:
    """
    Synchronous wrapper: generate TTS audio for a scene narration.

    Args:
        narration:   Scene narration text from the script JSON.
        output_path: Where to write the .mp3 file.
        voice:       edge-tts voice name (default from env).

    Returns:
        Estimated audio duration in seconds.
    """
    voice = voice or DEFAULT_VOICE
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        duration = asyncio.run(_synthesise(narration, voice, output_path))
        log.info("TTS ✓ %s (%.1fs)", output_path.name, duration)
        return duration
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
