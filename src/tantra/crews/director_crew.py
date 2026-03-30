"""
Tantra AI — Director Crew (Phase 2)
CMO + CTO supervisor agents that sit between the Director and the Phase 1 crew.

Hierarchy:
  Director (CAIO)
    ├── CMO Crew (Chief Marketing Officer) — content strategy + LinkedIn
    │     Agents: CMO Strategist, Content Analyst
    └── CTO Crew (Chief Technology Officer) — tech insights + build posts
          Agents: CTO Strategist, Tech Analyst

These are CrewAI hierarchical crews. The CMO/CTO read the active WeeklyPlan,
provide specialised guidance, and hand work down to the Phase 1 social crew.

Design notes:
  - Director Crew agents run on director/manager tiers (local Ollama models)
  - They do NOT publish directly — they enrich instructions for Phase 1 tasks
  - Results are stored in AgentTask.result and fed into the next planning cycle
"""
from __future__ import annotations

import json
import logging
from typing import Any

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tools import tool

from tantra.core.config import ModelTier, settings

logger = logging.getLogger(__name__)


def _make_llm(tier: ModelTier, base_url: str, api_key: str) -> LLM:
    """Route through LiteLLM proxy — same pattern as social_crew.py."""
    return LLM(
        model=f"openai/{tier.value}",
        base_url=base_url,
        api_key=api_key,
    )


# ---------------------------------------------------------------------------
# Shared CrewAI tools
# ---------------------------------------------------------------------------

@tool("Active Weekly Plan Reader")
def read_active_weekly_plan() -> str:
    """
    Read the current active WeeklyPlan from the database.
    Returns the Director's goals, content calendar, and strategic analysis as JSON.
    """
    import asyncio
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from tantra.core.config import settings as _settings
    from tantra.db.director import WeeklyPlan

    async def _fetch() -> str:
        engine = create_async_engine(_settings.database_url, echo=False)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            result = await session.execute(
                select(WeeklyPlan)
                .where(WeeklyPlan.status == "active")
                .order_by(WeeklyPlan.week_start.desc())
                .limit(1)
            )
            plan = result.scalar_one_or_none()
            if plan is None:
                return json.dumps({"status": "no_active_plan", "message": "No active weekly plan found."})
            return json.dumps({
                "week_start": str(plan.week_start),
                "week_number": plan.week_number,
                "goals": plan.goals,
                "content_calendar": plan.content_calendar,
                "director_analysis": plan.director_analysis,
            }, default=str)

    return asyncio.get_event_loop().run_until_complete(_fetch())


@tool("Published Content Reader")
def read_published_content(days_back: int = 7) -> str:
    """
    Read recently published LinkedIn posts from the content queue.
    Returns a list of post previews and metadata for performance analysis.
    """
    import asyncio
    from datetime import datetime, timedelta

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from tantra.core.config import settings as _settings
    from tantra.db.social import ContentQueueItem

    async def _fetch() -> str:
        engine = create_async_engine(_settings.database_url, echo=False)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        async with async_session() as session:
            result = await session.execute(
                select(ContentQueueItem)
                .where(
                    ContentQueueItem.status == "published",
                    ContentQueueItem.published_at >= cutoff,
                )
                .order_by(ContentQueueItem.published_at.desc())
                .limit(10)
            )
            items = result.scalars().all()
            if not items:
                return json.dumps({"posts": [], "message": f"No published posts in last {days_back} days."})
            posts = [
                {
                    "id": str(item.id),
                    "post_urn": item.post_urn,
                    "preview": item.draft_text[:200],
                    "hashtags": item.hashtags,
                    "published_at": str(item.published_at),
                }
                for item in items
            ]
            return json.dumps({"posts": posts, "count": len(posts)}, default=str)

    return asyncio.get_event_loop().run_until_complete(_fetch())


@tool("Pending Agent Tasks Reader")
def read_pending_tasks() -> str:
    """
    Read pending AgentTasks from the database for this week.
    Returns tasks that are waiting to be executed by Celery.
    """
    import asyncio
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from tantra.core.config import settings as _settings
    from tantra.db.director import AgentTask

    async def _fetch() -> str:
        engine = create_async_engine(_settings.database_url, echo=False)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            result = await session.execute(
                select(AgentTask)
                .where(AgentTask.status == "pending")
                .order_by(AgentTask.scheduled_for.asc())
                .limit(20)
            )
            tasks = result.scalars().all()
            if not tasks:
                return json.dumps({"tasks": [], "message": "No pending tasks."})
            task_list = [
                {
                    "id": str(t.id),
                    "task_type": t.task_type,
                    "assigned_to": t.assigned_to,
                    "priority": t.priority,
                    "scheduled_for": str(t.scheduled_for),
                    "instructions": t.instructions,
                }
                for t in tasks
            ]
            return json.dumps({"tasks": task_list, "count": len(task_list)}, default=str)

    return asyncio.get_event_loop().run_until_complete(_fetch())


# ---------------------------------------------------------------------------
# CMO Crew — Content Marketing strategy
# ---------------------------------------------------------------------------

def build_cmo_crew(
    weekly_plan_json: str = "",
    verbose: bool = True,
) -> Crew:
    """
    Build the CMO supervisor crew.

    The CMO crew reviews the active weekly plan + recent content performance,
    then produces enriched content briefs that guide the Phase 1 social crew.

    Output: A content strategy brief (stored as AgentTask.result) that includes:
      - Topic priorities for this week
      - Tone/angle recommendations per topic
      - Hooks that performed well last week
      - What to avoid
    """
    llm_base = f"{settings.litellm_base_url}/v1"
    llm_key = settings.litellm_key

    # ── CMO Strategist ────────────────────────────────────────────────────────
    cmo_strategist = Agent(
        role="Chief Marketing Officer",
        goal=(
            "Define the content strategy for the week. Decide which topics to cover, "
            "what angles resonate with the Tantra AI audience, and set quality standards."
        ),
        backstory=(
            "You are the CMO of Tantra AI — a local autonomous agent system being built "
            "in public. Your audience is engineers, founders, and AI practitioners on LinkedIn. "
            "You know what makes technical content engaging: real numbers, honest struggles, "
            "and specific insights rather than vague hype. You read the Director's plan "
            "and translate it into actionable content briefs."
        ),
        llm=_make_llm(ModelTier.director, llm_base, llm_key),
        tools=[read_active_weekly_plan, read_published_content],
        max_iter=4,
        verbose=verbose,
        allow_delegation=False,
    )

    # ── Content Analyst ───────────────────────────────────────────────────────
    content_analyst = Agent(
        role="Content Performance Analyst",
        goal=(
            "Analyse past content performance and identify patterns: "
            "what topics, tones, and formats drive the most engagement."
        ),
        backstory=(
            "You are a sharp analyst who turns content data into strategy. "
            "You track which LinkedIn posts get more comments vs. likes, "
            "which hooks stop the scroll, and which topics build long-term authority. "
            "You report directly to the CMO."
        ),
        llm=_make_llm(ModelTier.manager, llm_base, llm_key),
        tools=[read_published_content, read_pending_tasks],
        max_iter=3,
        verbose=verbose,
        allow_delegation=False,
    )

    # ── Tasks ─────────────────────────────────────────────────────────────────
    task_analyse_performance = Task(
        description=(
            "Pull the last 7 days of published LinkedIn posts. For each post, note: "
            "the topic, the tone, the opening hook, and any performance signals available. "
            "Identify 2-3 patterns: what worked, what didn't, what the audience responded to."
        ),
        expected_output=(
            "A performance analysis with 2-3 clear patterns, examples from recent posts, "
            "and specific recommendations for this week's content tone and topics."
        ),
        agent=content_analyst,
    )

    task_create_content_brief = Task(
        description=(
            "Read the active weekly plan from the database. "
            "Using the performance analysis, create a detailed content brief for this week. "
            "The brief must include: "
            "1) Prioritised topic list (top 3 for the week) with rationale, "
            "2) Tone guidance (specific, not generic), "
            "3) 3 proven hook structures for LinkedIn posts, "
            "4) Topics/angles to AVOID this week, "
            "5) One specific angle for the Tantra build progress post."
        ),
        expected_output=(
            "A structured content strategy brief with topic priorities, tone guidance, "
            "3 hook templates, avoid list, and Tantra progress post angle. "
            "Formatted as a clear, actionable document for the content crew."
        ),
        agent=cmo_strategist,
        context=[task_analyse_performance],
    )

    return Crew(
        agents=[cmo_strategist, content_analyst],
        tasks=[task_analyse_performance, task_create_content_brief],
        process=Process.sequential,
        verbose=verbose,
        memory=False,
    )


# ---------------------------------------------------------------------------
# CTO Crew — Technical content + build insights
# ---------------------------------------------------------------------------

def build_cto_crew(verbose: bool = True) -> Crew:
    """
    Build the CTO supervisor crew.

    The CTO crew reviews Tantra's technical progress and produces rich
    context for the progress-post task, including:
      - What was actually built this week (technical specifics)
      - Performance numbers (model inference time, task success rate, etc.)
      - Technical insights worth sharing publicly

    Output: A technical build brief (stored as AgentTask.result)
    """
    llm_base = f"{settings.litellm_base_url}/v1"
    llm_key = settings.litellm_key

    # ── CTO Strategist ────────────────────────────────────────────────────────
    cto_strategist = Agent(
        role="Chief Technology Officer",
        goal=(
            "Document Tantra AI's technical progress for the week and identify "
            "the most compelling insights worth sharing publicly."
        ),
        backstory=(
            "You are the CTO of Tantra AI — a full-stack engineer and AI researcher "
            "building a production-grade autonomous agent stack with Python, FastAPI, "
            "CrewAI, Ollama, and Celery. You value precision and specific technical details. "
            "When writing about technology, you use real numbers, real code, real results. "
            "You read the pending task queue and published content to understand what "
            "was built and what audiences found most interesting."
        ),
        llm=_make_llm(ModelTier.director, llm_base, llm_key),
        tools=[read_pending_tasks, read_published_content],
        max_iter=4,
        verbose=verbose,
        allow_delegation=False,
    )

    # ── Tech Analyst ──────────────────────────────────────────────────────────
    tech_analyst = Agent(
        role="Technical Analytics Specialist",
        goal=(
            "Review completed AgentTasks and extract technical performance metrics: "
            "execution times, success rates, model quality, and system bottlenecks."
        ),
        backstory=(
            "You are a detail-oriented engineer who cares about system performance. "
            "You dig through task logs and results to find the real story: "
            "which agents are fast, which are slow, which prompts produce better output. "
            "You translate raw data into insights the CTO can use."
        ),
        llm=_make_llm(ModelTier.manager, llm_base, llm_key),
        tools=[read_pending_tasks],
        max_iter=3,
        verbose=verbose,
        allow_delegation=False,
    )

    # ── Tasks ─────────────────────────────────────────────────────────────────
    task_technical_review = Task(
        description=(
            "Review the completed and pending AgentTasks from this week. "
            "Extract: which tasks ran, success/failure rates, any notable technical "
            "details from task results (execution time, model used, output quality). "
            "Summarise the technical state of the system this week."
        ),
        expected_output=(
            "A technical summary with: tasks executed count, success rate, "
            "any failures and root causes, and 2-3 specific technical observations "
            "worth highlighting (e.g. 'phi4:14b completed research in 23s', "
            "'Director plan generation took 45s on llama3.3:70b')."
        ),
        agent=tech_analyst,
    )

    task_build_brief = Task(
        description=(
            "Using the technical review, write a build context brief for this week's "
            "Tantra progress post. The brief should give the content writer enough "
            "specific, honest technical details to write a compelling 'building in public' "
            "LinkedIn post. Include: "
            "1) What was built/shipped this week (be specific), "
            "2) One real number or metric from the system, "
            "3) One honest challenge or unexpected thing encountered, "
            "4) What Phase 2 looks like from a technical standpoint."
        ),
        expected_output=(
            "A 200-300 word build context brief with specific technical details, "
            "real metrics, an honest challenge, and a forward-looking note about Phase 2. "
            "This will be fed directly to the progress post writer."
        ),
        agent=cto_strategist,
        context=[task_technical_review],
    )

    return Crew(
        agents=[cto_strategist, tech_analyst],
        tasks=[task_technical_review, task_build_brief],
        process=Process.sequential,
        verbose=verbose,
        memory=False,
    )


# ---------------------------------------------------------------------------
# Convenience: run a crew synchronously (for Celery task context)
# ---------------------------------------------------------------------------

def run_cmo_crew_sync(verbose: bool = False) -> dict[str, Any]:
    """
    Run the CMO crew synchronously. Returns dict with output text + success flag.
    Designed to be called from a Celery task.
    """
    try:
        crew = build_cmo_crew(verbose=verbose)
        result = crew.kickoff()
        output = str(result.tasks_output[-1].raw) if result.tasks_output else str(result)
        return {"success": True, "output": output, "crew": "cmo"}
    except Exception as exc:
        logger.exception(f"CMO crew failed: {exc}")
        return {"success": False, "error": str(exc), "crew": "cmo"}


def run_cto_crew_sync(verbose: bool = False) -> dict[str, Any]:
    """
    Run the CTO crew synchronously. Returns dict with output text + success flag.
    Designed to be called from a Celery task.
    """
    try:
        crew = build_cto_crew(verbose=verbose)
        result = crew.kickoff()
        output = str(result.tasks_output[-1].raw) if result.tasks_output else str(result)
        return {"success": True, "output": output, "crew": "cto"}
    except Exception as exc:
        logger.exception(f"CTO crew failed: {exc}")
        return {"success": False, "error": str(exc), "crew": "cto"}
