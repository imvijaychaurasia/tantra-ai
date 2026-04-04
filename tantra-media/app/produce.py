"""
tantra-media — Production orchestrator
तंत्र  ·  Coordinates TTS + image gen + video assembly for one YouTubeVideo

Production pipeline for a single video:
  1. For each scene in script.scenes:
     a. TTS: narration → audio/scene_N.mp3
     b. Image: visual_prompt → images/scene_N.png
     c. Clip: audio + image → clips/scene_N.mp4
  2. Thumbnail: thumbnail_prompt → images/thumbnail.png
  3. Concat: all clips → output/{video_id}.mp4

File layout (all under BASE_DIR = /data/media):
  audio/{video_id}/scene_{id}.mp3
  images/{video_id}/scene_{id}.png
  images/{video_id}/thumbnail.png
  clips/{video_id}/scene_{id}.mp4
  output/{video_id}.mp4

Progress is logged at INFO level. Each step is independently resumable
(files are only regenerated if missing), so a partial failure can be
retried without re-running completed steps.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from .imagegen import generate_scene_image, generate_thumbnail
from .tts import generate_scene_audio, get_actual_audio_duration
from .video import build_scene_clip, concatenate_clips

log = logging.getLogger("tantra-media.produce")

# Base directory — bind-mounted from host at ./data/media
BASE_DIR = Path(os.getenv("TANTRA_MEDIA_DIR", "/data/media"))


def _audio_dir(video_id: str) -> Path:
    return BASE_DIR / "audio" / video_id

def _image_dir(video_id: str) -> Path:
    return BASE_DIR / "images" / video_id

def _clip_dir(video_id: str) -> Path:
    return BASE_DIR / "clips" / video_id

def _output_dir() -> Path:
    return BASE_DIR / "output"


def produce_video(
    video_id: str,
    script: dict,
    *,
    voice: Optional[str] = None,
    force_regen: bool = False,
) -> dict:
    """
    Run the full production pipeline for a YouTube video.

    Args:
        video_id:    UUID string of the YouTubeVideo row.
        script:      Script JSON dict (scenes, thumbnail_prompt, title, …).
        voice:       edge-tts voice name (default from env TANTRA_MEDIA_VOICE).
        force_regen: If True, regenerate files even if they already exist.

    Returns:
        dict with keys:
          success:        bool
          video_path:     str  — relative path from BASE_DIR (e.g. output/{id}.mp4)
          audio_path:     str  — path to first scene's audio (representative)
          thumbnail_path: str  — relative path from BASE_DIR
          scene_count:    int
          total_duration: float (seconds)
          error:          str | None
    """
    log.info("=== Produce video %s ===", video_id)

    scenes = script.get("scenes", [])
    if not scenes:
        return {"success": False, "error": "Script has no scenes"}

    audio_dir = _audio_dir(video_id)
    image_dir = _image_dir(video_id)
    clip_dir = _clip_dir(video_id)
    output_dir = _output_dir()

    for d in [audio_dir, image_dir, clip_dir, output_dir]:
        d.mkdir(parents=True, exist_ok=True)

    clip_paths: list[Path] = []
    total_duration = 0.0

    for idx, scene in enumerate(scenes):
        scene_id = scene.get("id", idx + 1)
        log.info("Scene %s/%s (id=%s, type=%s)", idx + 1, len(scenes), scene_id, scene.get("type"))

        # ── TTS ─────────────────────────────────────────────────────────
        audio_path = audio_dir / f"scene_{scene_id}.mp3"
        if force_regen or not audio_path.exists():
            narration = scene.get("narration", "")
            if not narration:
                log.warning("Scene %s has no narration, using title", scene_id)
                narration = script.get("title", "No narration provided.")
            generate_scene_audio(narration, audio_path, voice=voice)
        else:
            log.debug("TTS skip (exists): %s", audio_path.name)

        audio_duration = get_actual_audio_duration(audio_path)
        # Use audio duration or scene-specified duration, whichever is longer
        scene_duration_spec = float(scene.get("duration_seconds", 0))
        scene_duration = max(audio_duration, scene_duration_spec, 3.0)

        # ── Image ────────────────────────────────────────────────────────
        image_path = image_dir / f"scene_{scene_id}.png"
        if force_regen or not image_path.exists():
            generate_scene_image(
                scene=scene,
                output_path=image_path,
                video_title=script.get("title", ""),
                scene_index=idx,
                total_scenes=len(scenes),
            )
        else:
            log.debug("Image skip (exists): %s", image_path.name)

        # ── Scene clip ───────────────────────────────────────────────────
        clip_path = clip_dir / f"scene_{scene_id}.mp4"
        if force_regen or not clip_path.exists():
            build_scene_clip(
                image_path=image_path,
                audio_path=audio_path,
                output_path=clip_path,
                duration=scene_duration,
            )
        else:
            log.debug("Clip skip (exists): %s", clip_path.name)

        clip_paths.append(clip_path)
        total_duration += scene_duration
        log.info("Scene %s/%s done (%.1fs)", idx + 1, len(scenes), scene_duration)

    # ── Thumbnail ────────────────────────────────────────────────────────
    thumbnail_path = image_dir / "thumbnail.png"
    if force_regen or not thumbnail_path.exists():
        generate_thumbnail(script=script, output_path=thumbnail_path)
    else:
        log.debug("Thumbnail skip (exists): %s", thumbnail_path.name)

    # ── Final concat ─────────────────────────────────────────────────────
    output_path = output_dir / f"{video_id}.mp4"
    if force_regen or not output_path.exists():
        log.info("Concatenating %d clips → %s", len(clip_paths), output_path.name)
        concatenate_clips(clip_paths, output_path)
    else:
        log.debug("Output video skip (exists): %s", output_path.name)

    # Return paths relative to BASE_DIR for portability
    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(BASE_DIR))
        except ValueError:
            return str(p)

    log.info("=== Production complete: %s (%.0fs total) ===", video_id, total_duration)

    return {
        "success": True,
        "video_path": rel(output_path),
        "audio_path": rel(audio_dir / f"scene_{scenes[0].get('id', 1)}.mp3"),
        "thumbnail_path": rel(thumbnail_path),
        "scene_count": len(scenes),
        "total_duration": round(total_duration, 1),
        "error": None,
    }
