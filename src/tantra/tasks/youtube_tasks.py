"""
Tantra AI — YouTube Celery Tasks (Phase 3a)

Task registry:
  tantra.tasks.youtube.generate_youtube_script  — Run YouTubeCrew → script → n8n approval
  tantra.tasks.youtube.produce_youtube_video     — tantra-media API → MP4 (Phase 3b)
  tantra.tasks.youtube.upload_youtube_video      — YouTube Data API upload (Phase 3c)
  tantra.tasks.youtube.update_youtube_metadata   — Update title/desc/tags post-upload

Phase 3a implements generate_youtube_script fully.
produce_youtube_video and upload_youtube_video are stubs that will be
filled in Phase 3b and 3c respectively.

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

            # ── Step 4: Checkpoint raw output ─────────────────────────────────
            _save_script_checkpoint(agent_task_id, {"raw_output": raw_output})

            # ── Step 5: Parse script ──────────────────────────────────────────
            script_data = parse_script_output(raw_output)
            if not script_data:
                raise ValueError(
                    "YouTubeCrew produced output that could not be parsed as a YouTube script. "
                    f"Raw output (first 500 chars): {raw_output[:500]}"
                )

            # Update checkpoint with parsed data
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
# produce_youtube_video  (Phase 3b stub)
# ---------------------------------------------------------------------------

def produce_youtube_video(youtube_video_id: str) -> dict[str, Any]:
    """
    Call tantra-media API to produce TTS + video/images + thumbnail → final MP4.

    Phase 3b implementation. Currently a stub that marks the video as 'failed'
    with a clear message that tantra-media is not yet implemented.
    """
    from tantra.db.social import YouTubeVideo

    session = _make_session()
    try:
        video = session.get(YouTubeVideo, uuid.UUID(youtube_video_id))
        if not video:
            return {"success": False, "error": f"YouTubeVideo {youtube_video_id} not found"}

        if video.status != "approved":
            return {
                "success": False,
                "error": f"Expected status 'approved', got '{video.status}'",
            }

        # Phase 3b not yet implemented
        logger.warning(
            "produce_youtube_video called but tantra-media not yet implemented. "
            "Video %s will remain in 'approved' status until Phase 3b ships.",
            youtube_video_id,
        )
        return {
            "success": False,
            "skipped": True,
            "reason": "tantra-media service not yet implemented (Phase 3b)",
            "youtube_video_id": youtube_video_id,
        }

    finally:
        session.close()


# ---------------------------------------------------------------------------
# upload_youtube_video  (Phase 3c stub)
# ---------------------------------------------------------------------------

def upload_youtube_video(youtube_video_id: str) -> dict[str, Any]:
    """
    Upload produced MP4 to YouTube via Data API v3.

    Phase 3c implementation. Currently a stub.
    """
    from tantra.db.social import YouTubeVideo

    session = _make_session()
    try:
        video = session.get(YouTubeVideo, uuid.UUID(youtube_video_id))
        if not video:
            return {"success": False, "error": f"YouTubeVideo {youtube_video_id} not found"}

        if video.status != "produced":
            return {
                "success": False,
                "error": f"Expected status 'produced', got '{video.status}'",
            }

        logger.warning(
            "upload_youtube_video called but not yet implemented (Phase 3c). "
            "Video %s will remain in 'produced' status.",
            youtube_video_id,
        )
        return {
            "success": False,
            "skipped": True,
            "reason": "YouTube upload not yet implemented (Phase 3c)",
            "youtube_video_id": youtube_video_id,
        }

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
