"""
tantra-media — TTS engine
तंत्र  ·  Text-to-Speech using gTTS (Google Text-to-Speech)

Free, no API key, works from any server IP via plain HTTPS to Google.
Outputs MP3 directly — no model download, no GPU, no conversion step.

Voice selection:
  gTTS uses Google's TTS voices. The TANTRA_MEDIA_VOICE env var is accepted
  but unused (gTTS does not support named voices); keep the variable for
  future TTS engine upgrades without changing docker-compose.yml.

  To control accent/locale set TANTRA_MEDIA_LANG (default: en):
    en      — English (Google selects a natural voice)
    en-IN   — Indian English accent
    en-GB   — British English accent
    en-AU   — Australian English accent
    hi      — Hindi (for Hindi narration)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("tantra-media.tts")

LANG = os.getenv("TANTRA_MEDIA_LANG", "en")
# TANTRA_MEDIA_VOICE accepted but unused — kept for API compatibility
_VOICE = os.getenv("TANTRA_MEDIA_VOICE", "am_adam")


def generate_scene_audio(
    narration: str,
    output_path: Path,
    voice: str | None = None,
) -> float:
    """
    Synchronous: generate TTS audio for a scene narration using gTTS.

    Uses Google Translate's TTS endpoint over plain HTTPS — no WebSocket,
    no API key, no model download. Works from any server IP.

    Args:
        narration:   Scene narration text from the script JSON.
        output_path: Where to write the .mp3 file.
        voice:       Accepted but unused (gTTS does not support named voices).

    Returns:
        Estimated audio duration in seconds.
    """
    from gtts import gTTS

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        tts = gTTS(text=narration, lang=LANG, slow=False)
        tts.save(str(output_path))

        # Estimate duration from word count (~150 wpm ≈ 2.5 words/sec)
        words = len(narration.split())
        estimated_duration = max(words / 2.5, 2.0)
        log.info("TTS ✓ %s via gTTS/%s (%.1fs estimated)", output_path.name, LANG, estimated_duration)
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
        size_bytes = audio_path.stat().st_size
        return max(size_bytes / 16000, 2.0)
