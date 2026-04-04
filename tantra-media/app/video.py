"""
tantra-media — Video assembly
तंत्र  ·  ffmpeg wrapper: image + audio → scene clips → final MP4

Pipeline:
  1. Per scene: combine image (PNG) + narration (MP3) → scene clip (MP4)
     - Image is looped/displayed for the audio duration
     - Video: H.264, 1920×1080, 30fps, CRF 23
     - Audio: AAC 192kbps
  2. Concat all scene clips → final MP4 via ffmpeg concat demuxer
  3. Clean up intermediate clips after successful concat

Output format:
  Video: H.264 (libx264) — universally compatible
  Audio: AAC (aac)       — YouTube preferred format
  Container: MP4
  Resolution: 1920×1080 (for scene slides), thumbnail at 1280×720
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("tantra-media.video")


def _run(cmd: list[str], description: str) -> None:
    """Run an ffmpeg command and raise on failure."""
    log.debug("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        log.error("ffmpeg %s failed:\n%s", description, result.stderr[-2000:])
        raise RuntimeError(f"ffmpeg {description} failed: {result.stderr[-500:]}")
    log.info("ffmpeg ✓ %s", description)


def build_scene_clip(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    duration: float,
) -> None:
    """
    Combine a static image + audio file into an MP4 scene clip.

    The image is displayed as a static frame for the full audio duration.
    A 0.5s fade-in and 0.5s fade-out are applied to the video.

    Args:
        image_path:  1920×1080 PNG slide image.
        audio_path:  .mp3 narration audio.
        output_path: Output .mp4 clip path.
        duration:    Duration in seconds (audio duration, min 2.0s).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(duration, 2.0)

    fade_out_start = max(duration - 0.5, duration * 0.9)

    cmd = [
        "ffmpeg", "-y",
        # Input: loop the image for 'duration' seconds
        "-loop", "1",
        "-framerate", "30",
        "-t", str(duration),
        "-i", str(image_path),
        # Input: audio file
        "-i", str(audio_path),
        # Video encoding
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-vf", f"scale=1920:1080:force_original_aspect_ratio=decrease,"
               f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
               f"fade=t=in:st=0:d=0.5,"
               f"fade=t=out:st={fade_out_start:.2f}:d=0.5",
        # Audio encoding
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        # Sync: stop when shortest input ends (audio drives length)
        "-shortest",
        str(output_path),
    ]
    _run(cmd, f"scene clip {output_path.name}")


def concatenate_clips(
    clip_paths: list[Path],
    output_path: Path,
) -> None:
    """
    Concatenate multiple MP4 scene clips into the final video.

    Uses ffmpeg's concat demuxer for lossless concatenation without re-encoding.

    Args:
        clip_paths:  Ordered list of .mp4 scene clip paths.
        output_path: Final .mp4 output path.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not clip_paths:
        raise ValueError("No clips to concatenate")

    if len(clip_paths) == 1:
        # Single clip: just copy it
        shutil.copy2(str(clip_paths[0]), str(output_path))
        log.info("Single clip — copied to %s", output_path.name)
        return

    # Write a concat list file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, prefix="tantra_concat_"
    ) as f:
        concat_file = Path(f.name)
        for clip in clip_paths:
            f.write(f"file '{clip.resolve()}'\n")

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",           # no re-encode (stream copy)
            str(output_path),
        ]
        _run(cmd, f"concat → {output_path.name}")
    finally:
        concat_file.unlink(missing_ok=True)


def get_duration(media_path: Path) -> float:
    """Get the duration of a media file in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0
