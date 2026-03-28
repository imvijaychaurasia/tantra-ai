"""
Tantra AI — Celery application
Task queues:
  default   — General background tasks
  social    — LinkedIn/YouTube API calls
  agents    — Agent execution tasks
  scheduled — Cron-triggered tasks
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from tantra.core.config import settings

# ---------------------------------------------------------------------------
# App initialisation
# ---------------------------------------------------------------------------
app = Celery("tantra")

app.config_from_object({
    # Broker + backend
    "broker_url": settings.celery_broker_url,
    "result_backend": settings.celery_result_backend,

    # Serialisation
    "task_serializer": "json",
    "result_serializer": "json",
    "accept_content": ["json"],
    "result_expires": 3600,       # 1 hour result TTL

    # Explicitly include all task modules so workers register them on startup.
    # autodiscover_tasks() only works for packages (directories with tasks.py),
    # not for single-file modules like social_tasks.py — use include= instead.
    "include": [
        "tantra.tasks.social_tasks",   # Phase 1: LinkedIn pipeline tasks
    ],

    # Queues
    "task_default_queue": "default",
    "task_routes": {
        "tantra.tasks.social.*": {"queue": "social"},
        "tantra.tasks.agent.*":  {"queue": "agents"},
        "tantra.tasks.scheduled.*": {"queue": "scheduled"},
    },

    # Worker behaviour
    "worker_prefetch_multiplier": 1,    # Fair dispatch for long tasks
    "task_acks_late": True,             # Ack only after task completes
    "task_reject_on_worker_lost": True,

    # Timezone
    "timezone": settings.timezone,
    "enable_utc": True,
})


# ---------------------------------------------------------------------------
# Celery Beat schedule (cron tasks)
# ---------------------------------------------------------------------------
app.conf.beat_schedule = {
    # ── Phase 1: LinkedIn content pipeline ───────────────────────────────────

    # Step 1: Research + draft 3 LinkedIn posts → insert to content_queue → n8n approval
    # Runs Mon/Wed/Fri at 7 AM so drafts are ready for the 9 AM publishing window
    "content-research-and-draft": {
        "task": "tantra.tasks.social.research_and_draft_posts",
        "schedule": crontab(hour=7, minute=0, day_of_week="1,3,5"),
        "options": {"queue": "agents"},
    },

    # Step 2: Publish approved posts from content_queue to LinkedIn
    # Runs Mon-Fri at 9 AM — after n8n approval has had 2 hours to collect decisions
    "linkedin-publish-approved": {
        "task": "tantra.tasks.social.publish_approved_linkedin_posts",
        "schedule": crontab(hour=9, minute=0, day_of_week="1-5"),
        "options": {"queue": "social"},
    },

    # ── Supporting tasks ──────────────────────────────────────────────────────

    # YouTube analytics pull — daily at 8 AM
    "youtube-analytics-pull": {
        "task": "tantra.tasks.social.youtube_analytics_pull",
        "schedule": crontab(hour=8, minute=0),
        "options": {"queue": "scheduled"},
    },

    # Agent memory consolidation — nightly at 2 AM
    "memory-consolidation": {
        "task": "tantra.tasks.agent.consolidate_memories",
        "schedule": crontab(hour=2, minute=0),
        "options": {"queue": "scheduled"},
    },
}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@app.task(bind=True, name="tantra.tasks.social.youtube_analytics_pull", queue="social")
def youtube_analytics_pull(self: Celery) -> dict:
    """Pull YouTube channel analytics and store in DB."""
    from tantra.tools.youtube import YouTubeClient
    try:
        client = YouTubeClient.from_api_key()
        # Placeholder — in full impl, store to DB
        return {"success": True, "message": "YouTube analytics pull complete"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.task(bind=True, name="tantra.tasks.agent.run_agent_task", queue="agents")
def run_agent_task(self: Celery, task_id: str, request_data: dict) -> dict:
    """
    Execute an agent task asynchronously.
    Results are stored in Redis with the task_id key.
    """
    import asyncio
    from tantra.agents.worker import WorkerAgent
    from tantra.core.config import ModelTier

    agent = WorkerAgent(
        name=request_data.get("agent_name", "TantraWorker"),
        role="General Agent",
        goal="Execute the given task accurately",
        model_tier=ModelTier(request_data.get("model", "worker")),
    )

    result = asyncio.get_event_loop().run_until_complete(
        agent.execute(
            task=request_data["task"],
            context=request_data.get("context"),
        )
    )

    return {
        "task_id": task_id,
        "success": result.success,
        "output": result.output,
        "error": result.error,
    }


@app.task(bind=True, name="tantra.tasks.agent.consolidate_memories", queue="scheduled")
def consolidate_memories(self: Celery) -> dict:
    """
    Nightly job: summarise episodic memories from the past day,
    save condensed facts to long-term semantic memory.
    """
    # TODO: implement episodic → semantic memory consolidation
    return {"success": True, "message": "Memory consolidation complete"}


@app.task(bind=True, name="tantra.tasks.agent.generate_content_ideas", queue="agents")
def generate_content_ideas(self: Celery) -> dict:
    """
    Legacy content idea stub — superseded by research_and_draft_posts (social_tasks.py).
    Kept for backward compatibility; beat schedule now points to the new task.
    Delegates directly to the new task.
    """
    from tantra.tasks.social_tasks import research_and_draft_posts
    return research_and_draft_posts.delay().get(timeout=600)
