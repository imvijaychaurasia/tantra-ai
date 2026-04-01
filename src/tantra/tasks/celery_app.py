"""
Tantra AI — Celery application
Task queues:
  default   — General background tasks
  social    — LinkedIn/YouTube API calls
  agents    — Agent execution tasks
  scheduled — Cron-triggered tasks
"""
from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

from tantra.core.config import settings

logger = logging.getLogger(__name__)

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
        "tantra.tasks.social_tasks",    # Phase 1: LinkedIn pipeline + engagement tasks
        "tantra.tasks.director_tasks",  # Phase 2: Director planning + task dispatch
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

    # ── Phase 1: Engagement + Progress tasks ─────────────────────────────────

    # Task 3: Find AI posts in LinkedIn feed → comment in human tone
    # PAUSED — Zernio comments API not yet available; re-enable when comments endpoint is live
    # "linkedin-engage-feed": {
    #     "task": "tantra.tasks.social.linkedin_engage_feed",
    #     "schedule": crontab(hour="*/4", minute=0),
    #     "options": {"queue": "social"},
    # },

    # Task 4: Write + publish a human-tone post about the Tantra AI build
    # Weekdays at 9:30 AM IST (Redis cooldown still guards against accidental double-runs)
    "tantra-progress-post": {
        "task": "tantra.tasks.social.post_tantra_progress",
        "schedule": crontab(hour=9, minute=30, day_of_week="1-5"),
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

    # ── Phase 2: Director planning engine ─────────────────────────────────────

    # Step 1: Director plans the week — Monday 6AM (before research crew at 7AM)
    # Creates WeeklyPlan + AgentTask rows; activates the plan for social tasks to read
    "director-weekly-planning": {
        "task": "tantra.tasks.director.weekly_planning",
        "schedule": crontab(hour=6, minute=0, day_of_week="1"),
        "options": {"queue": "agents"},
    },

    # Step 2: Dispatcher — checks AgentTasks due for execution every 30 minutes
    # Picks up tasks whose scheduled_for <= now and fires execute_agent_task
    "director-dispatch-due-tasks": {
        "task": "tantra.tasks.director.dispatch_due_tasks",
        "schedule": crontab(minute="*/30"),
        "options": {"queue": "scheduled"},
    },

    # Step 3: CMO end-of-week content review — Friday 5PM
    # Reviews published posts, extracts lessons, updates WeeklyPlan.performance_review
    "director-cmo-review": {
        "task": "tantra.tasks.director.cmo_review",
        "schedule": crontab(hour=17, minute=0, day_of_week="5"),
        "options": {"queue": "agents"},
    },

    # Step 4: CTO end-of-week technical review — Friday 5:15PM
    # Reviews completed AgentTasks, generates build context for next week's posts
    "director-cto-review": {
        "task": "tantra.tasks.director.cto_review",
        "schedule": crontab(hour=17, minute=15, day_of_week="5"),
        "options": {"queue": "agents"},
    },
}


# ---------------------------------------------------------------------------
# Worker startup — ensure all DB tables exist
# ---------------------------------------------------------------------------

@worker_ready.connect
def on_worker_ready(**kwargs) -> None:
    """
    Ensure all DB tables exist when the Celery worker starts.

    The FastAPI app calls init_db() (async create_all) on startup, but the
    Celery worker boots independently and may start before the API — or be
    restarted without an API restart.  This handler uses a sync SQLAlchemy
    engine to guarantee every ORM-registered table exists before the first
    task runs.

    This is safe to call repeatedly — create_all() is idempotent (it only
    creates tables that don't already exist).
    """
    try:
        from sqlalchemy import create_engine
        from tantra.core.database import Base
        import tantra.db          # registers Phase 1 models (ContentQueueItem, User, …)
        import tantra.db.director # registers Phase 2 models (WeeklyPlan, AgentTask)

        engine = create_engine(settings.database_sync_url, echo=False)
        Base.metadata.create_all(engine)
        engine.dispose()
        logger.info("Worker DB tables ensured (create_all idempotent)")
    except Exception as exc:
        logger.warning(f"Worker DB init failed (non-fatal — API may have already created tables): {exc}")


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


# ---------------------------------------------------------------------------
# Phase 2: Director Tasks
# (full implementations live in director_tasks.py — registered via include=)
# ---------------------------------------------------------------------------

@app.task(bind=True, name="tantra.tasks.director.weekly_planning", queue="agents")
def director_weekly_planning(self: Celery) -> dict:
    """Director plans the week — Monday 6AM."""
    from tantra.tasks.director_tasks import weekly_planning
    return weekly_planning()


@app.task(bind=True, name="tantra.tasks.director.dispatch_due_tasks", queue="scheduled")
def director_dispatch_due_tasks(self: Celery) -> dict:
    """Fire AgentTasks due for execution — every 30 min."""
    from tantra.tasks.director_tasks import dispatch_due_tasks
    return dispatch_due_tasks()


@app.task(bind=True, name="tantra.tasks.director.cmo_review", queue="agents")
def director_cmo_review(self: Celery) -> dict:
    """CMO end-of-week content review — Friday 5PM."""
    from tantra.tasks.director_tasks import cmo_review
    return cmo_review()


@app.task(bind=True, name="tantra.tasks.director.cto_review", queue="agents")
def director_cto_review(self: Celery) -> dict:
    """CTO end-of-week technical review — Friday 5:15PM."""
    from tantra.tasks.director_tasks import cto_review
    return cto_review()


@app.task(bind=True, name="tantra.tasks.director.execute_agent_task", queue="agents")
def director_execute_agent_task(self: Celery, task_id: str) -> dict:
    """Execute a specific AgentTask by ID (dispatched by dispatch_due_tasks)."""
    from tantra.tasks.director_tasks import execute_agent_task
    return execute_agent_task(task_id)
