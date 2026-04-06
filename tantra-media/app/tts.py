"""
tantra-media — TTS engine
तंत्र  ·  Text-to-Speech using Kokoro TTS (open-source, fully local)

Kokoro is a state-of-the-art neural TTS model that runs entirely on CPU
via ONNX runtime — no API key, no network calls, no IP blocking.

Model files are downloaded once from HuggingFace on first run and cached
in /data/media/.kokoro_models (bind-mounted to host, so only downloaded once).

Voice selection (TANTRA_MEDIA_VOICE env var):
  af_heart    — Female, warm (default for neutral content)
  am_adam     — Male, clear, deep  ← default (best for tech YouTube)
  am_michael  — Male, authoritative
  af_sarah    — Female, bright
  af_sky      — Female, energetic

Full voice list: https://github.com/thewh1teagle/kokoro-onnx?tab=readme-ov-file#voices
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("tantra-media.tts")

DEFAULT_VOICE = os.getenv("TANTRA_MEDIA_VOICE", "am_adam")
# Model cache — stored in the media bind-mount so it persists across rebuilds
_MODEL_CACHE = Path(os.getenv("TANTRA_MEDIA_DIR", "/data/media")) / ".kokoro_models"
_SAMPLE_RATE = 24000

# Module-level cache so the model is only loaded once per worker process
_kokoro: object | None = None


def _get_kokoro():
    """Lazy-load Kokoro model (downloads on first call, cached thereafter)."""
    global _kokoro
    if _kokoro is not None:
        return _kokoro

    from kokoro_onnx import Kokoro

    _MODEL_CACHE.mkdir(parents=True, exist_ok=True)
    model_path = _MODEL_CACHE / "kokoro-v0_19.onnx"
    voices_path = _MODEL_CACHE / "voices-v0_19.bin"

    # Download model files if not already cached
    if not model_path.exists() or not voices_path.exists():
        log.info("Kokoro models not found — downloading from HuggingFace (~100 MB, one-time)...")
        _download_kokoro_models(model_path, voices_path)

    log.info("Loading Kokoro TTS model from %s", _MODEL_CACHE)
    _kokoro = Kokoro(str(model_path), str(voices_path))
    log.info("Kokoro TTS model loaded ✓")
    return _kokoro


def _download_kokoro_models(model_path: Path, voices_path: Path) -> None:
    """Download Kokoro ONNX model files from HuggingFace."""
    import urllib.request

    BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files"
    files = {
        model_path: f"{BASE}/kokoro-v0_19.onnx",
        voices_path: f"{BASE}/voices-v0_19.bin",
    }
    for dest, url in files.items():
        if dest.exists():
            continue
        log.info("Downloading %s → %s", url, dest.name)
        tmp = dest.with_suffix(".tmp")
        try:
            urllib.request.urlretrieve(url, str(tmp))
            tmp.rename(dest)
            log.info("Downloaded %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
        except Exception as exc:
            if tmp.exists():
                tmp.unlink()
            raise RuntimeError(f"Failed to download Kokoro model {dest.name}: {exc}") from exc


def generate_scene_audio(
    narration: str,
    output_path: Path,
    voice: str | None = None,
) -> float:
    """
    Synchronous: generate TTS audio for a scene narration using Kokoro TTS.

    Fully synchronous and self-contained — no asyncio, no network calls after
    initial model download. Safe to call from run_in_executor().

    Args:
        narration:   Scene narration text from the script JSON.
        output_path: Where to write the .mp3 file (written as WAV then converted).
        voice:       Kokoro voice ID (default: am_adam).

    Returns:
        Actual audio duration in seconds.
    """
    import soundfile as sf
    import subprocess

    voice = voice or DEFAULT_VOICE
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        kokoro = _get_kokoro()

        samples, sample_rate = kokoro.create(
            text=narration,
            voice=voice,
            speed=1.05,   # Slightly faster delivery for YouTube pacing
            lang="en-us",
        )

        # Write WAV first, then convert to MP3 via ffmpeg (already in container)
        wav_path = output_path.with_suffix(".wav")
        sf.write(str(wav_path), samples, sample_rate)

        # Convert WAV → MP3
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(wav_path),
                "-codec:a", "libmp3lame",
                "-q:a", "2",          # VBR quality ~190 kbps
                str(output_path),
            ],
            capture_output=True, text=True, timeout=60,
        )
        wav_path.unlink(missing_ok=True)

        if result.returncode != 0:
            raise RuntimeError(f"WAV→MP3 conversion failed: {result.stderr[-300:]}")

        # Return actual duration from file size (MP3 ~190kbps ≈ 24KB/s)
        duration = max(output_path.stat().st_size / 24000, 2.0)
        log.info("TTS ✓ %s via Kokoro/%s (%.1fs)", output_path.name, voice, duration)
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
        size_bytes = audio_path.stat().st_size
        return max(size_bytes / 16000, 2.0)
