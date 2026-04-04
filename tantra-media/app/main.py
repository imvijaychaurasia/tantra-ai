"""
tantra-media — FastAPI service
तंत्र  ·  Media production REST API

Endpoints:
  GET  /health           — service health check
  POST /produce          — run full production pipeline for a video
  GET  /status/{video_id} — check if output files exist for a video

This service is called by the tantra-api Celery worker (produce_youtube_video task).
It runs as a separate Docker container on the tantra-net network at port 8100.

All output files are written to /data/media/ (bind-mounted from ./data/media/ on host).
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .produce import produce_video, BASE_DIR

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tantra-media")

# ── FastAPI app ────────────────────────────────────────────────────────────
app = FastAPI(
    title="tantra-media",
    description="तंत्र · TTS + Slide Generation + Video Assembly",
    version="0.1.0",
)

# ── In-memory job tracker (single-worker, single-process) ─────────────────
# Maps video_id → {"status": "running"|"done"|"failed", "result": dict}
_jobs: dict[str, dict] = {}


# ── Models ─────────────────────────────────────────────────────────────────
class ProduceRequest(BaseModel):
    video_id: str
    script: dict
    voice: Optional[str] = None
    force_regen: bool = False


class ProduceResponse(BaseModel):
    success: bool
    video_id: str
    video_path: Optional[str] = None
    audio_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    scene_count: Optional[int] = None
    total_duration: Optional[float] = None
    error: Optional[str] = None
    duration_seconds: Optional[float] = None   # wall-clock time for production


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health() -> JSONResponse:
    """Service health check."""
    return JSONResponse({
        "status": "ok",
        "service": "tantra-media",
        "media_dir": str(BASE_DIR),
        "media_dir_exists": BASE_DIR.exists(),
    })


@app.post("/produce", response_model=ProduceResponse, tags=["production"])
async def produce(req: ProduceRequest) -> ProduceResponse:
    """
    Run the full production pipeline for a YouTube video.

    This is a **synchronous** endpoint — it blocks until production completes.
    Typical duration: 2-8 minutes (depends on number of scenes and TTS speed).

    The Celery worker calling this should set a timeout of at least 25 minutes.

    Steps:
      1. TTS narration per scene (edge-tts, ~5s per scene)
      2. Slide image per scene (Pillow, <1s per scene)
      3. Scene clip assembly (ffmpeg, ~10s per scene)
      4. Thumbnail generation (Pillow, <1s)
      5. Final video concatenation (ffmpeg stream copy, ~5s)
    """
    video_id = req.video_id
    log.info("Produce request: video_id=%s, scenes=%d, force_regen=%s",
             video_id, len(req.script.get("scenes", [])), req.force_regen)

    t_start = time.time()
    try:
        result = produce_video(
            video_id=video_id,
            script=req.script,
            voice=req.voice,
            force_regen=req.force_regen,
        )
    except Exception as exc:
        log.error("Production failed for %s: %s", video_id, exc, exc_info=True)
        return ProduceResponse(
            success=False,
            video_id=video_id,
            error=str(exc),
        )

    elapsed = round(time.time() - t_start, 1)
    log.info("Production finished: video_id=%s, elapsed=%.1fs, success=%s",
             video_id, elapsed, result.get("success"))

    return ProduceResponse(
        success=result.get("success", False),
        video_id=video_id,
        video_path=result.get("video_path"),
        audio_path=result.get("audio_path"),
        thumbnail_path=result.get("thumbnail_path"),
        scene_count=result.get("scene_count"),
        total_duration=result.get("total_duration"),
        error=result.get("error"),
        duration_seconds=elapsed,
    )


@app.get("/status/{video_id}", tags=["production"])
async def status(video_id: str) -> JSONResponse:
    """
    Check if output files exist for a video (idempotency check).
    Returns file paths if found, or a not-found indicator.
    """
    output_path = BASE_DIR / "output" / f"{video_id}.mp4"
    thumbnail_path = BASE_DIR / "images" / video_id / "thumbnail.png"

    if output_path.exists():
        return JSONResponse({
            "video_id": video_id,
            "produced": True,
            "video_path": str(output_path.relative_to(BASE_DIR)),
            "thumbnail_path": str(thumbnail_path.relative_to(BASE_DIR)) if thumbnail_path.exists() else None,
            "video_size_mb": round(output_path.stat().st_size / 1e6, 1),
        })
    else:
        return JSONResponse({
            "video_id": video_id,
            "produced": False,
        })


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8100)
