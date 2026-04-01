"""
Tantra AI — Director Celery Tasks (Phase 2)

Task registry:
  tantra.tasks.director.weekly_planning      — Monday 6AM: Director plans the week
  tantra.tasks.director.cmo_review           — Friday 5PM: CMO reviews content performance
  tantra.tasks.director.cto_review           — Friday 5PM: CTO reviews technical progress
  tantra.tasks.director.execute_agent_task   — Execute a specific AgentTask by ID
  tantra.tasks.director.dispatch_due_tasks   — Every 30 min: fire tasks that are due

Integration with Phase 1:
  social_tasks.py reads the active WeeklyPlan via _get_director_context() helper
  at the start of research_and_draft_posts + post_tantra_progress. If a plan
  exists, the task uses plan goals/calendar as guidance. If not, defaults apply.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

from celery import Celery

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB helpers (sync wrappers around async SQLAlchemy)
# ---------------------------------------------------------------------------

def _run(coro):
    """
    Run an async coroutine from a sync Celery task context.

    Uses asyncio.run() which always creates a fresh event loop, runs the
    coroutine to completion, and closes the loop cleanly.  This is safer than
    get_event_loop().run_until_complete() in Celery ForkPoolWorker processes
    where a previously-used loop can be left in a dirty state.

    IMPORTANT: do NOT call _run() twice in the same function — reuse the same
    event loop by grouping all coroutines into a single async wrapper:

        async def _pipeline():
            a = await coro_one()
            b = await coro_two(a)
            return a, b

        result_a, result_b = _run(_pipeline())  # ← one call only
    """
    return asyncio.run(coro)


def _make_session():
    """Create a synchronous SQLAlchemy session for DB writes."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from tantra.core.config import settings
    engine = create_engine(settings.database_sync_url, echo=False)
    Session = sessionmaker(bind=engine)
    return Session()


# ---------------------------------------------------------------------------
# _get_director_context — used by social_tasks.py to read active plan
# ---------------------------------------------------------------------------

def get_director_context() -> Optional[dict[str, Any]]:
    """
    Read the active WeeklyPlan from DB and return it as a plain dict.

    Called by Phase 1 tasks (research_and_draft_posts, post_tantra_progress)
    to get Director's topic guidance. Returns None if no active plan exists.

    Returns dict:
      {
        "goals": {...},
        "content_calendar": [...],
        "director_analysis": "...",
        "week_start": "2026-03-30",
        "week_number": 14,
      }
    """
    try:
        from sqlalchemy import select, text
        session = _make_session()
        try:
            from tantra.db.director import WeeklyPlan
            plan = session.execute(
                select(WeeklyPlan)
                .where(WeeklyPlan.status == "active")
                .order_by(WeeklyPlan.week_start.desc())
                .limit(1)
            ).scalar_one_or_none()

            if plan is None:
                return None

            return {
                "goals": plan.goals,
                "content_calendar": plan.content_calendar,
                "director_analysis": plan.director_analysis,
                "week_start": str(plan.week_start),
                "week_number": plan.week_number,
                "plan_id": str(plan.id),
            }
        finally:
            session.close()
    except Exception as exc:
        logger.warning(f"get_director_context failed (non-fatal): {exc}")
        return None


# ---------------------------------------------------------------------------
# Internal: persist WeeklyPlan + AgentTasks to DB
# ---------------------------------------------------------------------------

def _save_weekly_plan(plan_data, agent_tasks_data: list[dict]) -> str:
    """
    Persist a WeeklyPlanData + list of task dicts to the DB.
    Returns the new WeeklyPlan UUID.
    """
    from tantra.db.director import AgentTask, WeeklyPlan

    session = _make_session()
    try:
        # Deactivate any existing active plan for this week
        from sqlalchemy import select
        existing = session.execute(
            select(WeeklyPlan).where(
                WeeklyPlan.week_start == plan_data.week_start
            )
        ).scalar_one_or_none()

        if existing:
            existing.status = "planning"
            existing.goals = plan_data.goals
            existing.content_calendar = [
                {
                    "day": e.day,
                    "platform": e.platform,
                    "task_type": e.task_type,
                    "topic": e.topic,
                    "tone": e.tone,
                    "priority": e.priority,
                    "instructions": e.instructions,
                }
                for e in plan_data.content_calendar
            ]
            existing.director_analysis = plan_data.director_analysis
            existing.updated_at = datetime.utcnow()
            plan_id = existing.id

            # Purge stale pending/in_progress tasks before inserting the new set.
            # Only remove tasks that haven't started yet — leave completed/failed
            # tasks as historical record.  This prevents duplicate task rows when
            # weekly_planning is re-run for the same week (e.g. manual trigger,
            # fallback plan replaced by real LLM plan, or beat schedule retry).
            from sqlalchemy import delete as sa_delete
            session.execute(
                sa_delete(AgentTask).where(
                    AgentTask.plan_id == plan_id,
                    AgentTask.status.in_(["pending", "in_progress"]),
                )
            )
            session.flush()
        else:
            plan = WeeklyPlan(
                id=uuid.uuid4(),
                week_start=plan_data.week_start,
                week_number=plan_data.week_number,
                year=plan_data.year,
                status="planning",
                goals=plan_data.goals,
                content_calendar=[
                    {
                        "day": e.day,
                        "platform": e.platform,
                        "task_type": e.task_type,
                        "topic": e.topic,
                        "tone": e.tone,
                        "priority": e.priority,
                        "instructions": e.instructions,
                    }
                    for e in plan_data.content_calendar
                ],
                director_analysis=plan_data.director_analysis,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(plan)
            session.flush()  # get ID before commit
            plan_id = plan.id

        # Save AgentTasks
        for t in agent_tasks_data:
            agent_task = AgentTask(
                id=uuid.uuid4(),
                plan_id=plan_id,
                task_type=t["task_type"],
                assigned_to=t["assigned_to"],
                priority=t["priority"],
                status="pending",
                instructions=t.get("instructions", ""),
                context=t.get("context", {}),
                scheduled_for=t.get("scheduled_for"),
                created_at=datetime.utcnow(),
            )
            session.add(agent_task)

        session.commit()
        logger.info(f"Saved WeeklyPlan {plan_id} + {len(agent_tasks_data)} tasks to DB")
        return str(plan_id)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _activate_weekly_plan(plan_id: str) -> None:
    """Set a WeeklyPlan to status='active'."""
    from sqlalchemy import select
    from tantra.db.director import WeeklyPlan

    session = _make_session()
    try:
        # Deactivate other active plans first
        from sqlalchemy import update
        session.execute(
            update(WeeklyPlan)
            .where(WeeklyPlan.status == "active")
            .values(status="completed", completed_at=datetime.utcnow())
        )
        # Activate this plan
        plan_uuid = uuid.UUID(plan_id)
        plan = session.execute(
            select(WeeklyPlan).where(WeeklyPlan.id == plan_uuid)
        ).scalar_one_or_none()
        if plan:
            plan.status = "active"
            plan.activated_at = datetime.utcnow()
            plan.updated_at = datetime.utcnow()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _update_agent_task_status(task_id: str, status: str, result: dict = None, error: str = None) -> None:
    """Update an AgentTask row with status + optional result/error."""
    from sqlalchemy import select
    from tantra.db.director import AgentTask

    session = _make_session()
    try:
        task_uuid = uuid.UUID(task_id)
        task = session.execute(
            select(AgentTask).where(AgentTask.id == task_uuid)
        ).scalar_one_or_none()
        if task:
            task.status = status
            if status == "in_progress":
                task.started_at = datetime.utcnow()
            elif status in ("completed", "failed", "skipped"):
                task.completed_at = datetime.utcnow()
            if result is not None:
                task.result = result
            if error is not None:
                task.error_message = error
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _get_due_agent_tasks(now: datetime = None) -> list[dict]:
    """
    Return pending AgentTasks whose scheduled_for <= now.
    Used by dispatch_due_tasks.
    """
    from sqlalchemy import select
    from tantra.db.director import AgentTask

    now = now or datetime.utcnow()
    session = _make_session()
    try:
        tasks = session.execute(
            select(AgentTask)
            .where(
                AgentTask.status == "pending",
                AgentTask.scheduled_for <= now,
            )
            .order_by(AgentTask.priority.asc(), AgentTask.scheduled_for.asc())
            .limit(10)
        ).scalars().all()

        return [
            {
                "id": str(t.id),
                "task_type": t.task_type,
                "assigned_to": t.assigned_to,
                "priority": t.priority,
                "instructions": t.instructions,
                "context": t.context or {},
                "scheduled_for": str(t.scheduled_for),
            }
            for t in tasks
        ]
    finally:
        session.close()


def _get_this_weeks_published_content() -> list[dict]:
    """Fetch content published this week for the performance review."""
    from sqlalchemy import select
    from tantra.db.social import ContentQueueItem

    # Start of current ISO week (Monday)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    cutoff = datetime(week_start.year, week_start.month, week_start.day)

    session = _make_session()
    try:
        items = session.execute(
            select(ContentQueueItem)
            .where(
                ContentQueueItem.status == "published",
                ContentQueueItem.published_at >= cutoff,
            )
            .order_by(ContentQueueItem.published_at.desc())
        ).scalars().all()

        return [
            {
                "id": str(item.id),
                "draft_text": item.draft_text,
                "hashtags": item.hashtags,
                "post_urn": item.post_urn,
                "published_at": str(item.published_at),
            }
            for item in items
        ]
    finally:
        session.close()


def _get_last_weeks_performance() -> dict:
    """
    Build a PerformanceSummary-style dict from last week's DB data.
    Used by weekly_planning to inform the Director's strategy.
    """
    from sqlalchemy import func, select
    from tantra.db.social import ContentQueueItem

    today = date.today()
    this_week_monday = today - timedelta(days=today.weekday())
    last_week_monday = this_week_monday - timedelta(weeks=1)
    last_week_sunday = this_week_monday - timedelta(days=1)

    last_week_start = datetime(last_week_monday.year, last_week_monday.month, last_week_monday.day)
    last_week_end = datetime(last_week_sunday.year, last_week_sunday.month, last_week_sunday.day, 23, 59, 59)

    session = _make_session()
    try:
        published = session.execute(
            select(ContentQueueItem).where(
                ContentQueueItem.status == "published",
                ContentQueueItem.published_at >= last_week_start,
                ContentQueueItem.published_at <= last_week_end,
            )
        ).scalars().all()

        drafted = session.execute(
            select(func.count(ContentQueueItem.id)).where(
                ContentQueueItem.created_at >= last_week_start,
                ContentQueueItem.created_at <= last_week_end,
            )
        ).scalar() or 0

        rejected = session.execute(
            select(func.count(ContentQueueItem.id)).where(
                ContentQueueItem.status == "rejected",
                ContentQueueItem.created_at >= last_week_start,
                ContentQueueItem.created_at <= last_week_end,
            )
        ).scalar() or 0

        previews = [item.draft_text[:150] for item in published[:5]]

        return {
            "posts_published": len(published),
            "posts_drafted": drafted,
            "posts_rejected": rejected,
            "recent_post_previews": previews,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Celery Task registrations
# (imported by celery_app.py via conf.include)
# ---------------------------------------------------------------------------

# Late import to avoid circular — celery_app is imported by tasks
def _get_celery_app() -> Celery:
    from tantra.tasks.celery_app import app
    return app


# ---------------------------------------------------------------------------
# Task: weekly_planning  (Monday 6AM)
# ---------------------------------------------------------------------------

async def _weekly_planning_pipeline(director, week_start, perf):
    """
    Single async pipeline for weekly planning — both LLM calls in one event loop.

    Grouping plan_week() + decompose_to_tasks() into one asyncio.run() call
    prevents event loop state issues in Celery ForkPoolWorker processes.
    """
    from tantra.agents.director import PerformanceSummary
    plan_data = await director.plan_week(week_start=week_start, performance=perf)
    agent_tasks_data = await director.decompose_to_tasks(plan_data)
    return plan_data, agent_tasks_data


def weekly_planning() -> dict:
    """
    Director plans the week:
    1. Pull last week's performance from DB
    2. Run DirectorAgent.plan_week() + decompose_to_tasks() in one event loop
    3. Persist WeeklyPlan + AgentTask rows to DB
    4. Activate the plan (status = 'active')

    Registered as: tantra.tasks.director.weekly_planning
    Queue: agents
    Beat: Monday 6:00 AM
    """
    logger.info("Director: weekly_planning started")

    try:
        # Determine this week's Monday
        today = date.today()
        week_start = today - timedelta(days=today.weekday())

        # Gather last week's performance (sync DB query)
        perf_data = _get_last_weeks_performance()

        from tantra.agents.director import DirectorAgent, PerformanceSummary
        director = DirectorAgent()
        perf = PerformanceSummary(**perf_data)

        # Run both LLM calls in a SINGLE asyncio.run() to avoid event loop
        # state issues in Celery ForkPoolWorker (two sequential _run() calls
        # leave the loop dirty — the second one hangs silently).
        plan_data, agent_tasks_data = _run(
            _weekly_planning_pipeline(director, week_start, perf)
        )

        # Persist to DB (sync)
        plan_id = _save_weekly_plan(plan_data, agent_tasks_data)

        # Activate the new plan
        _activate_weekly_plan(plan_id)

        result = {
            "success": True,
            "plan_id": plan_id,
            "week_start": str(plan_data.week_start),
            "week_number": plan_data.week_number,
            "goals": plan_data.goals,
            "tasks_created": len(agent_tasks_data),
            "analysis": plan_data.director_analysis[:300],
        }
        logger.info(
            f"Director: weekly plan created — "
            f"week {plan_data.week_number}, "
            f"{len(agent_tasks_data)} tasks, "
            f"plan_id={plan_id}"
        )
        return result

    except Exception as exc:
        logger.exception(f"Director: weekly_planning failed: {exc}")
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Task: cmo_review  (Friday 5PM)
# ---------------------------------------------------------------------------

def cmo_review() -> dict:
    """
    CMO runs end-of-week content review:
    1. Reads this week's published posts
    2. Runs the CMO crew (content analyst + CMO strategist)
    3. Stores performance review in the active WeeklyPlan

    Registered as: tantra.tasks.director.cmo_review
    Queue: agents
    Beat: Friday 17:00
    """
    logger.info("Director: CMO review started")

    try:
        from tantra.crews.director_crew import run_cmo_crew_sync
        result = run_cmo_crew_sync(verbose=False)

        if result["success"]:
            # Also update the weekly plan performance review
            published = _get_this_weeks_published_content()
            _update_performance_review(published, result["output"])

        return result

    except Exception as exc:
        logger.exception(f"Director: cmo_review failed: {exc}")
        return {"success": False, "error": str(exc)}


def _update_performance_review(published: list[dict], cmo_output: str) -> None:
    """Update the active WeeklyPlan with performance review data."""
    from sqlalchemy import select, update
    from tantra.db.director import WeeklyPlan

    session = _make_session()
    try:
        plan = session.execute(
            select(WeeklyPlan)
            .where(WeeklyPlan.status == "active")
            .order_by(WeeklyPlan.week_start.desc())
            .limit(1)
        ).scalar_one_or_none()

        if plan:
            plan.performance_review = {
                "posts_published": len(published),
                "cmo_analysis": cmo_output[:1000],
                "reviewed_at": datetime.utcnow().isoformat(),
            }
            plan.updated_at = datetime.utcnow()
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Task: cto_review  (Friday 5PM)
# ---------------------------------------------------------------------------

def cto_review() -> dict:
    """
    CTO runs end-of-week technical review:
    1. Reviews completed AgentTasks + system metrics
    2. Produces build context brief for next week's progress posts

    Registered as: tantra.tasks.director.cto_review
    Queue: agents
    Beat: Friday 17:00 (same time as cmo_review, different queue slot)
    """
    logger.info("Director: CTO review started")

    try:
        from tantra.crews.director_crew import run_cto_crew_sync
        result = run_cto_crew_sync(verbose=False)
        return result

    except Exception as exc:
        logger.exception(f"Director: cto_review failed: {exc}")
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Task: execute_agent_task  (on-demand / dispatched by dispatch_due_tasks)
# ---------------------------------------------------------------------------

def execute_agent_task(task_id: str) -> dict:
    """
    Execute a specific AgentTask from the weekly plan.

    Maps task_type to the appropriate Phase 1 Celery task:
      research_draft   → social_tasks.research_and_draft_posts
      progress_post    → social_tasks.post_tantra_progress
      youtube_script   → celery_app.youtube_analytics_pull (stub)
      analytics_review → cmo_review
      engagement_scan  → social_tasks.linkedin_engage_feed

    Registered as: tantra.tasks.director.execute_agent_task
    Queue: agents
    """
    logger.info(f"Director: execute_agent_task({task_id})")

    try:
        from sqlalchemy import select
        from tantra.db.director import AgentTask

        session = _make_session()
        try:
            task = session.execute(
                select(AgentTask).where(AgentTask.id == uuid.UUID(task_id))
            ).scalar_one_or_none()

            if task is None:
                return {"success": False, "error": f"AgentTask {task_id} not found"}

            task_type = task.task_type
            instructions = task.instructions or ""
            context = task.context or {}
        finally:
            session.close()

        _update_agent_task_status(task_id, "in_progress")

        # Dispatch to the appropriate Phase 1 task
        result = _dispatch_task_type(task_type, instructions, context)

        _update_agent_task_status(
            task_id,
            "completed" if result.get("success") else "failed",
            result=result,
            error=result.get("error"),
        )
        return {"success": result.get("success", False), "task_id": task_id, "result": result}

    except Exception as exc:
        logger.exception(f"Director: execute_agent_task failed: {exc}")
        _update_agent_task_status(task_id, "failed", error=str(exc))
        return {"success": False, "task_id": task_id, "error": str(exc)}


def _dispatch_task_type(task_type: str, instructions: str, context: dict) -> dict:
    """Map a task_type string to the actual Celery task and execute it."""
    app = _get_celery_app()

    if task_type == "research_draft":
        from tantra.tasks.social_tasks import research_and_draft_posts
        result = research_and_draft_posts.apply(
            kwargs={"director_instructions": instructions, "director_context": context}
        )
        return result.get(timeout=600) if hasattr(result, "get") else {"success": True}

    elif task_type == "progress_post":
        from tantra.tasks.social_tasks import post_tantra_progress
        result = post_tantra_progress.apply(
            kwargs={"director_instructions": instructions}
        )
        return result.get(timeout=120) if hasattr(result, "get") else {"success": True}

    elif task_type == "youtube_script":
        from tantra.tasks.celery_app import youtube_analytics_pull
        result = youtube_analytics_pull.apply()
        return result.get(timeout=120) if hasattr(result, "get") else {"success": True}

    elif task_type == "analytics_review":
        return cmo_review()

    elif task_type == "engagement_scan":
        from tantra.tasks.social_tasks import linkedin_engage_feed
        result = linkedin_engage_feed.apply()
        return result.get(timeout=120) if hasattr(result, "get") else {"success": True}

    else:
        logger.warning(f"Unknown task_type: {task_type!r} — skipping")
        return {"success": False, "error": f"Unknown task_type: {task_type!r}"}


# ---------------------------------------------------------------------------
# Task: dispatch_due_tasks  (every 30 minutes)
# ---------------------------------------------------------------------------

def dispatch_due_tasks() -> dict:
    """
    Dispatcher: check for AgentTasks that are due and fire them.

    Runs every 30 minutes. Picks up to 5 tasks whose scheduled_for <= now
    and queues them as execute_agent_task Celery calls.

    Registered as: tantra.tasks.director.dispatch_due_tasks
    Queue: scheduled
    Beat: every 30 minutes
    """
    logger.info("Director: dispatch_due_tasks started")

    due_tasks = _get_due_agent_tasks(now=datetime.utcnow())

    if not due_tasks:
        logger.info("Director: no due tasks")
        return {"success": True, "dispatched": 0}

    app = _get_celery_app()
    dispatched = []

    for task in due_tasks:
        try:
            task_id = task["id"]
            # Mark as in_progress before dispatching to prevent double-dispatch
            _update_agent_task_status(task_id, "in_progress")

            # Queue the execution
            celery_result = app.send_task(
                "tantra.tasks.director.execute_agent_task",
                args=[task_id],
                queue="agents",
            )
            # Store Celery task ID for tracking
            _store_celery_task_id(task_id, celery_result.id)
            dispatched.append({"task_id": task_id, "task_type": task["task_type"], "celery_id": celery_result.id})
            logger.info(f"Director: dispatched {task['task_type']} task {task_id}")
        except Exception as exc:
            logger.error(f"Director: failed to dispatch task {task.get('id')}: {exc}")
            _update_agent_task_status(task.get("id", ""), "failed", error=str(exc))

    return {"success": True, "dispatched": len(dispatched), "tasks": dispatched}


def _store_celery_task_id(agent_task_id: str, celery_task_id: str) -> None:
    """Store the Celery task ID on the AgentTask row for tracking."""
    from sqlalchemy import select
    from tantra.db.director import AgentTask

    session = _make_session()
    try:
        task = session.execute(
            select(AgentTask).where(AgentTask.id == uuid.UUID(agent_task_id))
        ).scalar_one_or_none()
        if task:
            task.celery_task_id = celery_task_id
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()
