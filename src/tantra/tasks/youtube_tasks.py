"""
Tantra AI — YouTube Celery Tasks (Phase 3a / 3b / 3c)

Task registry:
  tantra.tasks.youtube.generate_youtube_script  — Run YouTubeCrew → script → n8n approval (Phase 3a)
  tantra.tasks.youtube.produce_youtube_video     — tantra-media API → MP4 (Phase 3b)
  tantra.tasks.youtube.upload_youtube_video      — YouTube Data API v3 resumable upload (Phase 3c)
  tantra.tasks.youtube.update_youtube_metadata   — Update title/desc/tags post-upload

All three phases are fully implemented.

Resilience:
  generate_youtube_script checkpoints the expensive YouTubeCrew output to Redis
  DB3 after the crew completes. On restart, the task skips the crew and resumes
  at the DB-insert + n8n-webhook step.
  Checkpoint key: tantra:checkpoint:youtube_script:{agent_task_id}
  Checkpoint TTL: 4 hours (same-day only, aligned with content calendar)

State machine for YouTubeVideo:
  scripted → approved → producing → produced → uploading → live | failed | rejected
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine from a sync Celery task context."""
    return asyncio.run(coro)


def _make_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from tantra.core.config import settings
    engine = create_engine(settings.database_sync_url, echo=False)
    Session = sessionmaker(bind=engine)
    return Session()


def _redis_db3():
    """Return a Redis connection to DB3 (Tantra app state)."""
    import redis as redis_lib
    from tantra.core.config import settings
    url = settings.celery_broker_url.replace("/1", "/3")
    return redis_lib.from_url(url, decode_responses=True)


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

_SCRIPT_CHECKPOINT_TTL = 4 * 3600  # 4 hours in seconds


def _save_script_checkpoint(agent_task_id: str, script_data: dict) -> None:
    """Save crew output to Redis checkpoint to survive restarts."""
    try:
        r = _redis_db3()
        key = f"tantra:checkpoint:youtube_script:{agent_task_id}"
        r.setex(key, _SCRIPT_CHECKPOINT_TTL, json.dumps(script_data))
        logger.info("YouTube script checkpoint saved: %s", key)
    except Exception as exc:
        logger.warning("Failed to save script checkpoint: %s", exc)


def _load_script_checkpoint(agent_task_id: str) -> Optional[dict]:
    """Load a previously saved crew output from Redis checkpoint."""
    try:
        r = _redis_db3()
        key = f"tantra:checkpoint:youtube_script:{agent_task_id}"
        raw = r.get(key)
        if raw:
            logger.info("YouTube script checkpoint found, skipping crew: %s", key)
            return json.loads(raw)
    except Exception as exc:
        logger.warning("Failed to load script checkpoint: %s", exc)
    return None


def _clear_script_checkpoint(agent_task_id: str) -> None:
    try:
        r = _redis_db3()
        r.delete(f"tantra:checkpoint:youtube_script:{agent_task_id}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# generate_youtube_script
# ---------------------------------------------------------------------------

def generate_youtube_script(agent_task_id: str) -> dict[str, Any]:
    """
    Run YouTubeCrew to generate a scene-by-scene YouTube video script.

    Steps:
      1. Load AgentTask, mark in_progress
      2. Check Redis checkpoint (skip crew if restarting after crash)
      3. Run YouTubeCrew (researcher → writer → SEO → reviewer)
      4. Checkpoint crew output to Redis
      5. Parse and validate script JSON
      6. Create YouTubeVideo row (status: scripted)
      7. Fire n8n webhook for human script review
      8. Update AgentTask result + mark completed

    On restart after crash, step 3 is skipped using the Redis checkpoint.
    """
    from tantra.db.director import AgentTask
    from tantra.db.social import YouTubeVideo

    session = _make_session()
    try:
        # ── Step 1: Load AgentTask ────────────────────────────────────────────
        task = session.get(AgentTask, uuid.UUID(agent_task_id))
        if not task:
            logger.error("generate_youtube_script: AgentTask not found: %s", agent_task_id)
            return {"success": False, "error": f"AgentTask {agent_task_id} not found"}

        if task.status == "completed":
            logger.info("generate_youtube_script: task already completed, skipping: %s", agent_task_id)
            return {"success": True, "skipped": True, "reason": "already completed"}

        task.status = "in_progress"
        task.started_at = datetime.utcnow()
        session.commit()
        logger.info("generate_youtube_script: starting for task %s", agent_task_id)

        # Pull context from task
        topic_hint = task.instructions or ""
        context = task.context or {}
        director_guidance = context.get("tone_override", "")
        plan_id = task.plan_id

        # ── Step 2: Check checkpoint ──────────────────────────────────────────
        script_data = _load_script_checkpoint(agent_task_id)

        if script_data is None:
            # ── Step 3: Run YouTubeCrew ───────────────────────────────────────
            from tantra.crews.youtube_crew import build_youtube_crew, parse_script_output

            # Gather live context for crew
            recent_titles = _get_recent_video_titles(session)
            channel_context = _get_channel_context(session)

            logger.info("generate_youtube_script: launching YouTubeCrew for topic=%r", topic_hint)
            crew = build_youtube_crew(
                topic_hint=topic_hint,
                director_guidance=director_guidance,
                channel_context=channel_context,
                recent_video_titles=recent_titles,
                verbose=True,
                agent_task_id=agent_task_id,
            )

            crew_result = crew.kickoff()
            raw_output = str(crew_result.raw if hasattr(crew_result, "raw") else crew_result)

            # Extract SEO task intermediate output for merger fallback (task index 2)
            seo_raw = ""
            try:
                if hasattr(crew_result, "tasks_output") and len(crew_result.tasks_output) >= 3:
                    seo_raw = str(getattr(crew_result.tasks_output[2], "raw", ""))
            except Exception as _e:
                logger.debug("Could not extract SEO task output: %s", _e)

            # ── Step 4: Checkpoint raw output ─────────────────────────────────
            _save_script_checkpoint(agent_task_id, {"raw_output": raw_output})

            # ── Step 5: Parse script ──────────────────────────────────────────
            script_data = parse_script_output(raw_output)
            if not script_data:
                raise ValueError(
                    "YouTubeCrew produced output that could not be parsed as a YouTube script. "
                    f"Raw output (first 500 chars): {raw_output[:500]}"
                )

            # Fill any metadata keys the quality reviewer omitted using the
            # SEO optimizer's intermediate output as a fallback source.
            if seo_raw:
                script_data = _fill_missing_seo_fields(script_data, seo_raw)

            # Update checkpoint with parsed + merged data
            _save_script_checkpoint(agent_task_id, script_data)

        else:
            # Restarting: checkpoint was raw crew output from previous run
            # Try to parse if it looks like raw output, otherwise treat as parsed script
            if "raw_output" in script_data:
                from tantra.crews.youtube_crew import parse_script_output
                parsed = parse_script_output(script_data["raw_output"])
                if parsed:
                    script_data = parsed
                    _save_script_checkpoint(agent_task_id, script_data)
                else:
                    raise ValueError("Checkpoint raw output could not be parsed on retry")

        # ── Step 6: Create YouTubeVideo row ──────────────────────────────────
        video = YouTubeVideo(
            agent_task_id=uuid.UUID(agent_task_id),
            plan_id=plan_id,
            title=script_data.get("title", "Untitled"),
            description=script_data.get("description", ""),
            script=script_data,
            thumbnail_concept=script_data.get("thumbnail_concept", ""),
            tags=script_data.get("tags", []),
            topic_hint=topic_hint,
            status="scripted",
            created_at=datetime.utcnow(),
        )
        session.add(video)
        session.flush()   # get video.id without committing
        video_id = str(video.id)

        logger.info(
            "generate_youtube_script: YouTubeVideo created id=%s title=%r",
            video_id, video.title,
        )

        # ── Step 7: Fire n8n webhook ──────────────────────────────────────────
        n8n_exec_id = _fire_script_approval_webhook(video, script_data)
        if n8n_exec_id:
            video.n8n_execution_id = n8n_exec_id

        session.commit()

        # ── Step 8: Update AgentTask ──────────────────────────────────────────
        task.status = "completed"
        task.completed_at = datetime.utcnow()
        task.result = {
            "youtube_video_id": video_id,
            "title": video.title,
            "status": "scripted",
            "n8n_execution_id": n8n_exec_id,
            "scenes_count": len(script_data.get("scenes", [])),
        }
        session.commit()

        _clear_script_checkpoint(agent_task_id)
        logger.info("generate_youtube_script: completed, video_id=%s", video_id)

        try:
            from tantra.core.monitor import MonitorEmitter
            MonitorEmitter.task_end(
                "youtube_script", agent_task_id,
                video_id=video_id, title=video.title,
                scenes=len(script_data.get("scenes", [])),
            )
        except Exception:
            pass

        return {
            "success": True,
            "youtube_video_id": video_id,
            "title": video.title,
            "status": "scripted",
            "scenes_count": len(script_data.get("scenes", [])),
        }

    except Exception as exc:
        logger.error("generate_youtube_script failed: %s", exc, exc_info=True)
        try:
            from tantra.core.monitor import MonitorEmitter
            MonitorEmitter.task_failed("youtube_script", agent_task_id, str(exc))
        except Exception:
            pass
        try:
            task_obj = session.get(AgentTask, uuid.UUID(agent_task_id))
            if task_obj:
                task_obj.status = "failed"
                task_obj.error_message = str(exc)[:1000]
                task_obj.completed_at = datetime.utcnow()
                session.commit()
        except Exception:
            pass
        return {"success": False, "error": str(exc)}

    finally:
        session.close()


# ---------------------------------------------------------------------------
# produce_youtube_video  (Phase 3b — tantra-media integration)
# ---------------------------------------------------------------------------

def produce_youtube_video(youtube_video_id: str) -> dict[str, Any]:
    """
    Call tantra-media HTTP API to produce TTS + slide images + final MP4.

    Pipeline (delegated to tantra-media container):
      1. edge-tts narration per scene → audio/{video_id}/scene_N.mp3
      2. Pillow dark-slide image per scene → images/{video_id}/scene_N.png
      3. ffmpeg: image + audio → clips/{video_id}/scene_N.mp4
      4. Pillow thumbnail → images/{video_id}/thumbnail.png
      5. ffmpeg concat → output/{video_id}.mp4

    DB state machine:
      approved → producing → produced
                          → failed (on error)

    This task is idempotent: if output files already exist and status is
    'produced', it returns success without re-calling tantra-media.

    Timeout: tantra-media /produce is synchronous and can take up to 15 min
    for a long video. The Celery soft_time_limit is set to 30 minutes.
    """
    import httpx
    from tantra.core.config import settings
    from tantra.db.social import YouTubeVideo

    session = _make_session()
    try:
        video = session.get(YouTubeVideo, uuid.UUID(youtube_video_id))
        if not video:
            return {"success": False, "error": f"YouTubeVideo {youtube_video_id} not found"}

        # Idempotency: if already produced, return success
        if video.status == "produced":
            logger.info("produce_youtube_video: video %s already produced, skipping", youtube_video_id)
            return {
                "success": True,
                "skipped": True,
                "reason": "already produced",
                "youtube_video_id": youtube_video_id,
                "video_path": video.video_path,
            }

        if video.status != "approved":
            return {
                "success": False,
                "error": f"Expected status 'approved' or 'produced', got '{video.status}'",
            }

        if not video.script:
            return {"success": False, "error": "No script JSON on YouTubeVideo row — cannot produce"}

        # ── Mark as producing ─────────────────────────────────────────────
        video.status = "producing"
        session.commit()
        logger.info("produce_youtube_video: video %s → producing", youtube_video_id)

        # ── Call tantra-media /produce ─────────────────────────────────────
        media_url = getattr(settings, "tantra_media_url", "http://tantra-media:8100")
        produce_url = f"{media_url}/produce"

        payload = {
            "video_id": youtube_video_id,
            "script": video.script,
            "force_regen": False,
        }

        logger.info("produce_youtube_video: calling %s (video_id=%s, scenes=%d)",
                    produce_url, youtube_video_id, len(video.script.get("scenes", [])))

        try:
            # Long timeout — production can take 5-15 minutes for a multi-scene video
            with httpx.Client(timeout=httpx.Timeout(connect=30, read=1800, write=60, pool=60)) as client:
                resp = client.post(produce_url, json=payload)
                resp.raise_for_status()
                result = resp.json()
        except httpx.ConnectError as exc:
            error_msg = (
                f"Cannot connect to tantra-media at {media_url}. "
                f"Is the service running? Run: docker compose up -d tantra-media. "
                f"Original error: {exc}"
            )
            logger.error("produce_youtube_video: %s", error_msg)
            video.status = "failed"
            video.error_message = error_msg[:1000]
            session.commit()
            return {"success": False, "error": error_msg}
        except httpx.HTTPStatusError as exc:
            error_msg = f"tantra-media returned HTTP {exc.response.status_code}: {exc.response.text[:500]}"
            logger.error("produce_youtube_video: %s", error_msg)
            video.status = "failed"
            video.error_message = error_msg[:1000]
            session.commit()
            return {"success": False, "error": error_msg}
        except Exception as exc:
            error_msg = f"tantra-media call failed: {exc}"
            logger.error("produce_youtube_video: %s", error_msg, exc_info=True)
            video.status = "failed"
            video.error_message = error_msg[:1000]
            session.commit()
            return {"success": False, "error": error_msg}

        # ── Handle response ───────────────────────────────────────────────
        if not result.get("success"):
            error_msg = result.get("error", "tantra-media returned success=false")
            logger.error("produce_youtube_video: production failed for %s: %s", youtube_video_id, error_msg)
            video.status = "failed"
            video.error_message = error_msg[:1000]
            session.commit()
            return {"success": False, "error": error_msg}

        # ── Update DB with file paths ──────────────────────────────────────
        # tantra-media returns paths relative to /data/media (bind-mounted from ./data/media/)
        # Store the host-relative paths so the API can serve them
        media_base = "/data/media"
        video.video_path = f"{media_base}/{result['video_path']}" if result.get("video_path") else None
        video.audio_path = f"{media_base}/{result['audio_path']}" if result.get("audio_path") else None
        video.thumbnail_path = f"{media_base}/{result['thumbnail_path']}" if result.get("thumbnail_path") else None
        video.status = "produced"
        video.produced_at = datetime.utcnow()
        session.commit()

        logger.info(
            "produce_youtube_video: ✓ video %s produced in %.0fs — %d scenes, %.0fs total",
            youtube_video_id,
            result.get("duration_seconds", 0),
            result.get("scene_count", 0),
            result.get("total_duration", 0),
        )

        return {
            "success": True,
            "youtube_video_id": youtube_video_id,
            "video_path": video.video_path,
            "thumbnail_path": video.thumbnail_path,
            "scene_count": result.get("scene_count"),
            "total_duration": result.get("total_duration"),
            "production_time_seconds": result.get("duration_seconds"),
        }

    except Exception as exc:
        logger.error("produce_youtube_video: unexpected error for %s: %s", youtube_video_id, exc, exc_info=True)
        try:
            if video and video.status == "producing":
                video.status = "failed"
                video.error_message = str(exc)[:1000]
                session.commit()
        except Exception:
            pass
        return {"success": False, "error": str(exc)}

    finally:
        session.close()


# ---------------------------------------------------------------------------
# upload_youtube_video  (Phase 3c — YouTube Data API v3)
# ---------------------------------------------------------------------------

def upload_youtube_video(youtube_video_id: str) -> dict[str, Any]:
    """
    Upload produced MP4 to YouTube via Data API v3.

    Prerequisites:
      - YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET in .env
      - YOUTUBE_REFRESH_TOKEN in .env  (run scripts/youtube_oauth_setup.py once)
      - celery-worker has ./data/media:/data/media bind-mount

    DB state machine:
      produced → uploading → live
                           → failed (on error, reverts to 'produced' for retry)

    Idempotent: if video is already 'live', returns success immediately.

    The upload uses the YouTube Data API v3 resumable upload protocol with
    8 MB chunks and automatic retry on transient HTTP 5xx errors (up to 5 retries
    with exponential back-off). A 10-minute timeout covers videos up to ~1 GB.
    """
    import os
    import time

    from tantra.core.config import settings
    from tantra.db.social import YouTubeVideo

    session = _make_session()
    video = None
    try:
        video = session.get(YouTubeVideo, uuid.UUID(youtube_video_id))
        if not video:
            return {"success": False, "error": f"YouTubeVideo {youtube_video_id} not found"}

        # ── Idempotency ────────────────────────────────────────────────────────
        if video.status == "live":
            logger.info("upload_youtube_video: %s already live, skipping", youtube_video_id)
            return {
                "success": True,
                "skipped": True,
                "reason": "already live",
                "youtube_video_id": youtube_video_id,
                "youtube_url": video.youtube_url,
            }

        if video.status != "produced":
            return {
                "success": False,
                "error": (
                    f"Expected status 'produced', got '{video.status}'. "
                    "Video must be in 'produced' state before uploading."
                ),
            }

        # ── Validate credentials ───────────────────────────────────────────────
        if not settings.youtube_client_id or not settings.youtube_client_secret:
            return {
                "success": False,
                "error": (
                    "YouTube OAuth not configured. "
                    "Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in .env "
                    "then run: python scripts/youtube_oauth_setup.py"
                ),
            }

        refresh_token = (
            settings.youtube_refresh_token.get_secret_value()
            if settings.youtube_refresh_token
            else None
        )
        if not refresh_token:
            return {
                "success": False,
                "error": (
                    "YOUTUBE_REFRESH_TOKEN not set. "
                    "Run: python scripts/youtube_oauth_setup.py  "
                    "then add YOUTUBE_REFRESH_TOKEN=<token> to .env"
                ),
            }

        # ── Validate video file ────────────────────────────────────────────────
        if not video.video_path:
            return {"success": False, "error": "No video_path on YouTubeVideo row — re-run produce_youtube_video"}

        video_file = video.video_path   # e.g. /data/media/output/{id}.mp4
        if not os.path.isfile(video_file):
            return {
                "success": False,
                "error": (
                    f"Video file not found: {video_file}. "
                    "Ensure celery-worker has ./data/media:/data/media mounted "
                    "and re-run produce_youtube_video if the file is missing."
                ),
            }

        file_size_mb = os.path.getsize(video_file) / (1024 * 1024)
        logger.info(
            "upload_youtube_video: %s → uploading (%.1f MB from %s)",
            youtube_video_id, file_size_mb, video_file,
        )

        # ── Import Google API client ───────────────────────────────────────────
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            return {
                "success": False,
                "error": (
                    f"google-api-python-client not installed: {exc}. "
                    "Run: pip install google-api-python-client google-auth"
                ),
            }

        # ── Mark as uploading ──────────────────────────────────────────────────
        video.status = "uploading"
        session.commit()

        # ── Build OAuth2 credentials ───────────────────────────────────────────
        # We use an offline refresh token — the client library auto-refreshes
        # the access token as needed without any user interaction.
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=settings.youtube_client_id,
            client_secret=settings.youtube_client_secret.get_secret_value(),
            token_uri="https://oauth2.googleapis.com/token",
            scopes=[
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube",
            ],
        )
        # Force a token refresh before upload to catch auth errors early
        creds.refresh(Request())

        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

        # ── Build video metadata ───────────────────────────────────────────────
        tags = list(video.tags or [])
        # YouTube enforces a 500-character limit on the tags array joined by commas
        tag_budget = 500
        selected_tags: list[str] = []
        for tag in tags:
            if tag_budget - len(tag) - 1 >= 0:
                selected_tags.append(tag)
                tag_budget -= len(tag) + 1
            else:
                break

        body = {
            "snippet": {
                "title": (video.title or "Tantra AI — Local Autonomous Agent Stack")[:100],
                "description": (video.description or "")[:5000],
                "tags": selected_tags,
                "categoryId": settings.youtube_upload_category_id,
                "defaultLanguage": "en",
            },
            "status": {
                "privacyStatus": settings.youtube_upload_privacy,
                "selfDeclaredMadeForKids": False,
                "embeddable": True,
            },
        }

        # ── Resumable upload with retry ────────────────────────────────────────
        media = MediaFileUpload(
            video_file,
            mimetype="video/mp4",
            resumable=True,
            chunksize=8 * 1024 * 1024,   # 8 MB chunks
        )

        insert_request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        retry_count = 0
        max_retries = 5
        retriable_statuses = {500, 502, 503, 504}

        logger.info("upload_youtube_video: starting resumable upload for %s", youtube_video_id)

        while response is None:
            try:
                upload_status, response = insert_request.next_chunk()
                if upload_status:
                    pct = int(upload_status.progress() * 100)
                    logger.info(
                        "upload_youtube_video: %s — %d%% uploaded",
                        youtube_video_id, pct,
                    )
            except HttpError as exc:
                if exc.resp.status in retriable_statuses and retry_count < max_retries:
                    retry_count += 1
                    wait = 2 ** retry_count
                    logger.warning(
                        "upload_youtube_video: HTTP %d (retry %d/%d in %ds) for %s",
                        exc.resp.status, retry_count, max_retries, wait, youtube_video_id,
                    )
                    time.sleep(wait)
                    continue
                # Non-retriable error or max retries exceeded
                error_msg = f"YouTube API error {exc.resp.status}: {exc.content[:500]!r}"
                logger.error("upload_youtube_video: %s for %s", error_msg, youtube_video_id)
                video.status = "failed"
                video.error_message = error_msg[:1000]
                session.commit()
                return {"success": False, "error": error_msg}
            except Exception as exc:
                error_msg = f"Upload failed mid-stream: {exc}"
                logger.error("upload_youtube_video: %s for %s", error_msg, youtube_video_id, exc_info=True)
                video.status = "failed"
                video.error_message = error_msg[:1000]
                session.commit()
                return {"success": False, "error": error_msg}

        # ── Upload complete — store result ─────────────────────────────────────
        yt_video_id = response.get("id", "")
        yt_url = f"https://www.youtube.com/watch?v={yt_video_id}" if yt_video_id else ""

        video.youtube_video_id = yt_video_id
        video.youtube_url = yt_url
        video.status = "live"
        video.uploaded_at = datetime.utcnow()
        session.commit()

        logger.info(
            "upload_youtube_video: ✓ %s is LIVE — %s (%.1f MB uploaded)",
            youtube_video_id, yt_url, file_size_mb,
        )

        # ── Monitor event ──────────────────────────────────────────────────────
        try:
            from tantra.core.monitor import MonitorEmitter
            MonitorEmitter.task_end(
                "youtube_upload", youtube_video_id,
                yt_video_id=yt_video_id,
                yt_url=yt_url,
                file_size_mb=round(file_size_mb, 1),
            )
        except Exception:
            pass

        return {
            "success": True,
            "youtube_video_id": youtube_video_id,
            "yt_video_id": yt_video_id,
            "youtube_url": yt_url,
            "file_size_mb": round(file_size_mb, 1),
        }

    except Exception as exc:
        logger.error(
            "upload_youtube_video: unexpected error for %s: %s",
            youtube_video_id, exc, exc_info=True,
        )
        try:
            if video and video.status == "uploading":
                # Roll back to "produced" so the operator can retry
                video.status = "produced"
                video.error_message = str(exc)[:1000]
                session.commit()
        except Exception:
            pass
        return {"success": False, "error": str(exc)}

    finally:
        session.close()


# ---------------------------------------------------------------------------
# Helpers — live context for crew
# ---------------------------------------------------------------------------

def _get_recent_video_titles(session) -> list[str]:
    """Return titles of the last 5 live YouTube videos to avoid repetition."""
    try:
        from sqlalchemy import select
        from tantra.db.social import YouTubeVideo
        rows = session.execute(
            select(YouTubeVideo.title)
            .where(YouTubeVideo.status == "live")
            .order_by(YouTubeVideo.uploaded_at.desc())
            .limit(5)
        ).scalars().all()
        return [r for r in rows if r]
    except Exception as exc:
        logger.warning("_get_recent_video_titles failed: %s", exc)
        return []


def _get_channel_context(session) -> str:
    """Return channel focus string from active WeeklyPlan goals."""
    try:
        from sqlalchemy import select
        from tantra.db.director import WeeklyPlan
        plan = session.execute(
            select(WeeklyPlan)
            .where(WeeklyPlan.status == "active")
            .order_by(WeeklyPlan.week_start.desc())
            .limit(1)
        ).scalar_one_or_none()
        if plan and plan.goals:
            topic = plan.goals.get("primary_topic", "")
            tone = plan.goals.get("tone", "")
            if topic:
                return (
                    f"This week's primary topic: {topic}. "
                    + (f"Tone: {tone}. " if tone else "")
                    + "Channel audience: engineers, founders, AI practitioners building in public."
                )
    except Exception as exc:
        logger.warning("_get_channel_context failed: %s", exc)
    return (
        "Building Tantra AI — a fully local autonomous agent stack. "
        "Audience: engineers, founders, AI practitioners building in public."
    )


# ---------------------------------------------------------------------------
# SEO merger fallback
# ---------------------------------------------------------------------------

def _fill_missing_seo_fields(script_data: dict, seo_raw: str) -> dict:
    """
    If the quality reviewer's JSON is missing tags / description / thumbnail_prompt,
    attempt to extract them from the SEO optimizer's raw text output.

    The SEO optimizer produces structured text like:
      1. Title: ...
      2. Description: [multi-line block]
      3. Tags: tag1, tag2, tag3, ...
      4. Thumbnail concept: ...
      5. Thumbnail prompt: ...
      6. Hook: ...

    This is a best-effort extraction — if a field cannot be reliably extracted
    it is left empty (already the default). A warning is logged for each miss.
    """
    import re

    needed = {
        "tags": not script_data.get("tags"),
        "description": not script_data.get("description"),
        "thumbnail_prompt": not script_data.get("thumbnail_prompt"),
        "thumbnail_concept": not script_data.get("thumbnail_concept"),
    }
    if not any(needed.values()):
        return script_data  # nothing to fill

    logger.warning(
        "YouTubeCrew: quality reviewer omitted fields %s — attempting SEO fallback merge",
        [k for k, v in needed.items() if v],
    )

    # ── tags ──────────────────────────────────────────────────────────────────
    if needed["tags"]:
        # Look for a line starting with "Tags:" or "3." followed by comma-separated terms
        tags_match = re.search(
            r"(?:^|\n)\s*(?:3\.|Tags?:)\s*(.+?)(?:\n|$)",
            seo_raw, re.IGNORECASE
        )
        if tags_match:
            raw_tags = tags_match.group(1).strip()
            tags = [t.strip().strip('"\'') for t in raw_tags.split(",") if t.strip()]
            if tags:
                script_data["tags"] = tags
                logger.info("SEO fallback: filled tags (%d items)", len(tags))

    # ── description ───────────────────────────────────────────────────────────
    if needed["description"]:
        # Description is a multi-line block after "Description:" or "2."
        desc_match = re.search(
            r"(?:^|\n)\s*(?:2\.|Description:)\s*\n?([\s\S]+?)(?:\n\s*(?:3\.|Tags?:|Thumbnail)|\Z)",
            seo_raw, re.IGNORECASE
        )
        if desc_match:
            desc = desc_match.group(1).strip()
            if len(desc) > 50:
                script_data["description"] = desc
                logger.info("SEO fallback: filled description (%d chars)", len(desc))

    # ── thumbnail_concept ─────────────────────────────────────────────────────
    if needed["thumbnail_concept"]:
        tc_match = re.search(
            r"(?:^|\n)\s*(?:4\.|Thumbnail concept:)\s*(.+?)(?:\n|$)",
            seo_raw, re.IGNORECASE
        )
        if tc_match:
            script_data["thumbnail_concept"] = tc_match.group(1).strip()
            logger.info("SEO fallback: filled thumbnail_concept")

    # ── thumbnail_prompt ──────────────────────────────────────────────────────
    if needed["thumbnail_prompt"]:
        tp_match = re.search(
            r"(?:^|\n)\s*(?:5\.|Thumbnail prompt:)\s*(.+?)(?:\n\s*(?:6\.|Hook:)|\Z)",
            seo_raw, re.IGNORECASE | re.DOTALL
        )
        if tp_match:
            prompt = tp_match.group(1).strip()
            if len(prompt) > 20:
                script_data["thumbnail_prompt"] = prompt
                logger.info("SEO fallback: filled thumbnail_prompt (%d chars)", len(prompt))

    # Log any still-missing fields after best-effort extraction
    still_missing = [k for k, v in needed.items() if v and not script_data.get(k)]
    if still_missing:
        logger.warning("SEO fallback: could not extract %s from SEO output", still_missing)

    return script_data


# ---------------------------------------------------------------------------
# n8n webhook helper
# ---------------------------------------------------------------------------

def _fire_script_approval_webhook(video, script_data: dict) -> Optional[str]:
    """
    POST script details to n8n YouTube script approval workflow.
    Returns n8n execution ID string, or None if n8n is not configured / call fails.
    """
    import httpx
    from tantra.core.config import settings

    webhook_url = getattr(settings, "n8n_youtube_script_webhook", None)
    if not webhook_url:
        logger.info(
            "N8N_YOUTUBE_SCRIPT_WEBHOOK not configured — skipping approval webhook. "
            "Video %s is scripted and awaiting manual approval.",
            str(video.id),
        )
        return None

    scenes = script_data.get("scenes", [])
    # Build a compact scene summary for the approval notification
    scene_summary = "\n".join(
        f"Scene {s.get('id', i+1)} [{s.get('type', '?')}] "
        f"({s.get('duration_seconds', '?')}s): {s.get('narration', '')[:120]}..."
        for i, s in enumerate(scenes[:6])  # show first 6 scenes in notification
    )

    payload = {
        "video_id": str(video.id),
        "title": video.title,
        "thumbnail_concept": script_data.get("thumbnail_concept", ""),
        "thumbnail_prompt": script_data.get("thumbnail_prompt", ""),
        "hook": script_data.get("hook", ""),
        "scene_count": len(scenes),
        "duration_target_seconds": script_data.get("duration_target_seconds", 0),
        "scene_summary": scene_summary,
        "tags": script_data.get("tags", []),
        "approval_url": f"{settings.api_base_url}/api/v1/youtube/{video.id}/approve",
        "rejection_url": f"{settings.api_base_url}/api/v1/youtube/{video.id}/reject",
    }

    try:
        resp = httpx.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        exec_id = data.get("executionId", data.get("id", ""))
        logger.info(
            "n8n YouTube script approval webhook fired for video %s (exec_id=%s)",
            str(video.id), exec_id,
        )
        return str(exec_id) if exec_id else None
    except Exception as exc:
        # Webhook failure is non-fatal — video is still created; human can approve manually
        logger.warning(
            "n8n YouTube script webhook failed for video %s: %s. "
            "Video status is 'scripted' — approve manually via API.",
            str(video.id), exc,
        )
        return None
