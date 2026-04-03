"""
Tantra AI — Director Agent (CAIO)
Chief AI Intelligence Officer — the top-level autonomous planner.

Responsibilities:
  1. Weekly Planning   — analyse last week, set goals, generate content calendar
  2. Task Decomposition — break the weekly plan into concrete AgentTask rows
  3. End-of-week Review — read published posts, extract lessons, update Qdrant memory

The Director uses the director-tier LLM (llama3.3:70b / local) for planning
and the manager-tier for synthesis tasks where quality over cost matters.

Integration contract:
  - DirectorAgent.plan_week() returns a WeeklyPlanData dataclass
  - The Celery task (director_tasks.py) persists it to Postgres and fires AgentTasks
  - Phase 1 tasks (social_tasks.py) call _get_active_director_context() to read
    the active plan and adjust their behaviour accordingly
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, AsyncIterator, Optional

from tantra.agents.leader import LeaderAgent
from tantra.core.config import ModelTier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data transfer objects (pure Python — no DB imports here)
# ---------------------------------------------------------------------------

@dataclass
class ContentCalendarEntry:
    """A single item in the weekly content calendar."""
    day: str                   # "Monday" | "Tuesday" | ...
    platform: str              # "linkedin" | "youtube" | "both"
    task_type: str             # "research_draft" | "progress_post" | "youtube_script"
    topic: str                 # Suggested topic for this day
    tone: str = "authentic"    # "technical" | "storytelling" | "data-driven" | "authentic"
    priority: str = "medium"   # "high" | "medium" | "low"
    instructions: str = ""     # Specific Director guidance for this task


@dataclass
class WeeklyPlanData:
    """Structured output of DirectorAgent.plan_week()."""
    week_start: date
    week_number: int
    year: int
    goals: dict[str, Any]
    content_calendar: list[ContentCalendarEntry]
    director_analysis: str
    raw_plan: dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceSummary:
    """Summary of last week's content performance, fed into the next plan."""
    posts_published: int = 0
    posts_drafted: int = 0
    posts_rejected: int = 0
    top_topics: list[str] = field(default_factory=list)
    recent_post_previews: list[str] = field(default_factory=list)
    memory_context: str = ""   # Relevant Qdrant memories for strategy


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

WEEKLY_PLANNING_PROMPT = """
You are the Director of Tantra AI (तंत्र) — an autonomous agent system being
built in public by an indie developer. Your role: strategic weekly planner.

Today is {today} (Week {week_number} of {year}).

## Last Week's Performance
{performance_summary}

## Current Platform Strategy
- Primary: LinkedIn (builder audience — engineers, founders, AI practitioners)
- Secondary: YouTube (planned but not yet launched)
- Brand voice: authentic, technical, building-in-public, no hype

## Tantra AI Context
Tantra is a local autonomous agent stack (Python + CrewAI + Ollama + FastAPI + Celery).
Phase 1: Content pipeline (LinkedIn posts + approval workflow) — COMPLETE.
Phase 2: Director layer (this planning system) — in progress.

## Your Task
Create a strategic weekly plan for Week {week_number}.

Return a JSON object with EXACTLY this structure:
{{
  "analysis": "2-3 sentence strategic assessment of last week and focus for this week",
  "goals": {{
    "linkedin_posts_target": <integer 3-5>,
    "progress_posts_target": <integer 3-5>,
    "engagement_target": <integer — realistic based on last week>,
    "primary_topic": "<main theme for this week>",
    "secondary_topics": ["<topic 2>", "<topic 3>"],
    "tone_guidance": "<1-2 sentences on voice/style for this week>",
    "avoid": ["<things that didn't work last week>"]
  }},
  "content_calendar": [
    {{
      "day": "Monday",
      "platform": "linkedin",
      "task_type": "research_draft",
      "topic": "<specific topic to research and draft>",
      "tone": "technical|storytelling|data-driven|authentic",
      "priority": "high|medium|low",
      "instructions": "<1-2 sentences of specific guidance for the content crew>"
    }},
    ... (one entry per planned content day, typically 3-5 entries)
  ]
}}

IMPORTANT:
- task_type must be one of: research_draft | progress_post | youtube_script | analytics_review
- progress_post entries are for the Tantra build story (handled by post_tantra_progress task)
- research_draft entries trigger the 4-agent research crew (research → write → publish → analyse)
- Keep content_calendar realistic — 3-5 entries max for a healthy posting cadence
- If last week performance is unknown, default to 3 linkedin posts + 3 progress posts
"""

PERFORMANCE_REVIEW_PROMPT = """
You are the Director of Tantra AI. It's end of week {week_number}.

## This Week's Plan
Goals: {goals}
Calendar: {calendar}

## Actual Content Published This Week
{published_content}

## Your Task
Write a concise performance review. Return a JSON object:
{{
  "posts_published": <integer>,
  "goals_met": <true|false>,
  "goal_attainment_pct": <0-100>,
  "top_performing": "<topic or post preview that got best engagement>",
  "underperforming": "<topic or post preview that underperformed>",
  "lessons": [
    "<lesson 1 — specific and actionable>",
    "<lesson 2>",
    "<lesson 3>"
  ],
  "next_week_priority": "<one-sentence recommendation for next week's Director>",
  "tone_assessment": "<was the tone right? what to adjust?>"
}}
"""

DIRECTOR_CHAT_SYSTEM_PROMPT = """
You are the Director of Tantra AI (तंत्र) — the Chief AI Intelligence Officer (CAIO).

You are having a direct terminal conversation with Vijay, the founder and sole developer.

## Your capabilities in this session
- Discuss and shape content strategy, platform direction, and growth priorities
- Plan tasks beyond the weekly schedule (ad-hoc research, experiments, platform launches)
- Review performance: what's working, what needs adjustment
- Brainstorm monetisation paths (LinkedIn leads, YouTube, Instagram, X)
- Advise on architecture and capability gaps in the Tantra stack
- When asked, decompose conversation outcomes into concrete AgentTasks committed to the DB

## Approval signals
When Vijay says 'approve', 'go', 'execute', 'commit', 'do it', 'proceed', or 'let's do it':
→ This means: extract discussed tasks as AgentTask rows and commit them to the DB.
→ A follow-up system call will prompt you for a JSON list — provide it precisely.
→ CRITICAL: Only use these EXACT task_type values (the only ones with Celery handlers):
    - research_draft     → 4-agent research crew writes a LinkedIn post draft
    - progress_post      → posts a Tantra AI build update to LinkedIn
    - youtube_script     → generates a YouTube video script
    - analytics_review   → reviews content performance metrics
  Phase 3 tasks (YouTube production, Instagram, X) do NOT have handlers yet.
  If the discussion is strategic/planning, do NOT extract tasks — it's just conversation.

## Tone
Strategic, direct, confident. C-suite executive talking to the CEO.
Skip pleasantries. Cut to insight. Use first-person ("I recommend", "I'll handle that").
Be concise — no bullet-point dumps unless genuinely warranted.

## Tantra AI context
- Stack: Python + CrewAI + Ollama (local GPU) + FastAPI + Celery + Redis + Postgres + Qdrant + n8n
- Phase 1 ✅: LinkedIn content pipeline (research → draft → approve → publish) — LIVE
- Phase 2 ✅: Director planning engine (weekly plans, AgentTask dispatch) — LIVE
- Models: qwen3:30b (director), qwen3:14b (manager/worker), qwen3:4b (fast), bge-m3 (embedder)
- GPU: RTX 5070 Ti 16GB — all inference is local
- Phase 3 (planned): YouTube + Instagram + X earning engine (auto + manual modes)
"""

TASK_DECOMPOSITION_PROMPT = """
You are the Director of Tantra AI. You have approved a weekly plan.
Now decompose the content calendar into specific, schedulable agent tasks.

Weekly Plan Goals: {goals}
Content Calendar: {calendar}
Week Start (Monday): {week_start}

For each calendar entry, decide:
1. Which Celery task should execute it (task_type maps 1:1 to Celery tasks)
2. What day/time to schedule it
3. Any special instructions to override defaults

Return a JSON array:
[
  {{
    "task_type": "research_draft|progress_post|youtube_script|analytics_review",
    "assigned_to": "social_crew|cmo|cto|director",
    "priority": "high|medium|low",
    "scheduled_day": "Monday|Tuesday|...",
    "scheduled_time": "HH:MM",
    "instructions": "<specific guidance — overrides task defaults if present>",
    "context": {{
      "topic_hint": "<optional topic to seed the crew>",
      "tone_override": "<optional tone adjustment>",
      "platform": "linkedin"
    }}
  }},
  ...
]

Schedule research_draft tasks for Mon/Wed/Fri at 07:00 (before the 09:00 publish window).
Schedule progress_post tasks for Tue/Thu at 09:30 (mid-week builder updates).
Schedule analytics_review for Friday at 17:00 (end-of-week review).
"""


# ---------------------------------------------------------------------------
# DirectorAgent
# ---------------------------------------------------------------------------

class DirectorAgent(LeaderAgent):
    """
    Chief AI Intelligence Officer — the top-level autonomous planner for Tantra.

    Extends LeaderAgent with:
    - plan_week():           generate this week's strategic plan
    - decompose_to_tasks():  break plan into AgentTask work items
    - review_week():         analyse published content, extract lessons, update memory

    All LLM calls use director-tier (llama3.3:70b local via LiteLLM proxy).

    Example usage (in a Celery task):
        director = DirectorAgent()
        plan_data = await director.plan_week(
            week_start=date(2026, 3, 30),
            performance=PerformanceSummary(posts_published=4, ...),
        )
        tasks = await director.decompose_to_tasks(plan_data)
    """

    def __init__(self) -> None:
        super().__init__(
            name="Director",
            role="Chief AI Intelligence Officer (CAIO)",
            goal=(
                "Plan weekly content strategy for Tantra AI, assign work to agents, "
                "and continuously improve output quality based on performance data."
            ),
            worker_roles=["social_crew", "cmo", "cto", "content_writer", "publisher", "analyst"],
            model_tier=ModelTier.director,
            max_subtasks=7,
        )

    async def plan_week(
        self,
        week_start: date,
        performance: Optional[PerformanceSummary] = None,
    ) -> WeeklyPlanData:
        """
        Generate the strategic plan for the given week.

        Args:
            week_start:    Monday date for the target week.
            performance:   Summary of last week's performance (None = first run).

        Returns:
            WeeklyPlanData with goals, content calendar, and analysis text.
        """
        iso = week_start.isocalendar()
        week_number = iso[1]
        year = iso[0]

        perf = performance or PerformanceSummary()
        perf_text = _format_performance(perf)

        prompt = WEEKLY_PLANNING_PROMPT.format(
            today=week_start.isoformat(),
            week_number=week_number,
            year=year,
            performance_summary=perf_text,
        )

        logger.info(f"Director planning week {week_number}/{year} starting {week_start}")

        try:
            raw = await self.think(task=prompt, temperature=0.6)
            plan_dict = _extract_json(raw)
        except Exception as exc:
            logger.error(f"Director plan_week LLM call failed: {exc}")
            plan_dict = _default_weekly_plan(week_start)

        analysis = plan_dict.get("analysis", "")
        goals = plan_dict.get("goals", _default_goals())
        calendar_raw = plan_dict.get("content_calendar", [])

        calendar = [
            ContentCalendarEntry(
                day=entry.get("day", "Monday"),
                platform=entry.get("platform", "linkedin"),
                task_type=entry.get("task_type", "research_draft"),
                topic=entry.get("topic", "AI automation"),
                tone=entry.get("tone", "authentic"),
                priority=entry.get("priority", "medium"),
                instructions=entry.get("instructions", ""),
            )
            for entry in calendar_raw
            if isinstance(entry, dict)
        ]

        logger.info(
            f"Director plan complete: {len(calendar)} tasks, "
            f"primary topic: {goals.get('primary_topic', '?')}"
        )

        return WeeklyPlanData(
            week_start=week_start,
            week_number=week_number,
            year=year,
            goals=goals,
            content_calendar=calendar,
            director_analysis=analysis,
            raw_plan=plan_dict,
        )

    async def decompose_to_tasks(
        self,
        plan: WeeklyPlanData,
    ) -> list[dict[str, Any]]:
        """
        Decompose a WeeklyPlanData into a list of schedulable task dicts.
        Each dict maps to one AgentTask row in the DB.

        Returns list of dicts compatible with AgentTask fields.
        """
        calendar_json = json.dumps(
            [
                {
                    "day": e.day,
                    "platform": e.platform,
                    "task_type": e.task_type,
                    "topic": e.topic,
                    "tone": e.tone,
                    "priority": e.priority,
                    "instructions": e.instructions,
                }
                for e in plan.content_calendar
            ],
            indent=2,
        )

        prompt = TASK_DECOMPOSITION_PROMPT.format(
            goals=json.dumps(plan.goals, indent=2),
            calendar=calendar_json,
            week_start=plan.week_start.isoformat(),
        )

        try:
            raw = await self.think(task=prompt, temperature=0.4)
            tasks_raw = _extract_json(raw)
            if not isinstance(tasks_raw, list):
                raise ValueError("Expected JSON array from task decomposition")
        except Exception as exc:
            logger.warning(f"Director task decomposition LLM failed: {exc} — using calendar directly")
            tasks_raw = _calendar_to_tasks(plan)

        # Resolve scheduled_for datetimes from day + time strings
        tasks = []
        for t in tasks_raw:
            if not isinstance(t, dict):
                continue
            tasks.append({
                "task_type": t.get("task_type", "research_draft"),
                "assigned_to": t.get("assigned_to", "social_crew"),
                "priority": t.get("priority", "medium"),
                "instructions": t.get("instructions", ""),
                "context": t.get("context", {}),
                "scheduled_for": _resolve_scheduled_datetime(
                    plan.week_start,
                    t.get("scheduled_day", "Monday"),
                    t.get("scheduled_time", "07:00"),
                ),
                "status": "pending",
            })

        # Deduplicate: if the LLM creates two tasks of the same type at the same
        # time slot (hallucination artifact), keep only the higher-priority one.
        # This prevents double-dispatching the same task type at the same moment.
        _priority_rank = {"high": 0, "medium": 1, "low": 2}
        seen: dict[tuple, dict] = {}
        for t in tasks:
            key = (t["task_type"], t["scheduled_for"])
            if key not in seen:
                seen[key] = t
            else:
                # Keep whichever has higher priority; ties go to the first seen
                if _priority_rank.get(t["priority"], 1) < _priority_rank.get(seen[key]["priority"], 1):
                    seen[key] = t
        deduplicated = list(seen.values())

        if len(deduplicated) < len(tasks):
            logger.warning(
                f"Director task deduplication: {len(tasks)} → {len(deduplicated)} tasks "
                f"(removed {len(tasks) - len(deduplicated)} duplicate slot(s))"
            )

        logger.info(f"Director decomposed {len(deduplicated)} agent tasks from plan")
        return deduplicated

    async def converse(
        self,
        history: list[dict[str, str]],
        live_context: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """
        Stream a conversational response from the Director LLM.

        The caller should pass the full message history (including the current
        user turn as the last entry).  The system prompt is injected automatically.

        Args:
            history: OpenAI-format messages [{"role": "user"|"assistant", "content": "..."}].
                     The latest user message must be the final element.

        Returns:
            Async iterator of token strings (call `await converse(history)` then
            `async for token in result: ...`).

        Example:
            history.append({"role": "user", "content": "What should we post this week?"})
            gen = await director.converse(history)
            async for token in gen:
                print(token, end="", flush=True)
        """
        from tantra.core.llm import chat

        # Base system prompt + optional live DB context as a second system message
        # (OpenAI / LiteLLM supports multiple system messages)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": DIRECTOR_CHAT_SYSTEM_PROMPT},
        ]
        if live_context:
            messages.append({"role": "system", "content": live_context})
        messages.extend(history)

        return await chat(
            messages=messages,
            model=self.model_tier,
            temperature=0.7,
            max_tokens=4096,
            stream=True,
        )

    @staticmethod
    def should_approve(message: str) -> bool:
        """
        Return True if the user message contains a standalone approval keyword.

        Uses regex word boundaries so "executed" does NOT match "execute",
        and "ongoing" does NOT match "go".  Only standalone trigger words count.

        Approval signals that the conversation content should be decomposed
        into AgentTask rows and committed to the DB.
        """
        import re
        # Each pattern is a regex with word boundaries.
        # r"\bexecute\b" matches "execute" but NOT "executed" / "executing".
        approval_patterns = [
            r"\bapprove[d]?\b",      # approve / approved
            r"\bgo ahead\b",          # go ahead
            r"\bexecute\b",           # execute  (NOT executed / executing)
            r"\bcommit\b",            # commit
            r"\bproceed\b",           # proceed
            r"\bdo it\b",             # do it
            r"\blet'?s do it\b",      # let's do it / lets do it
            r"\bship it\b",           # ship it
        ]
        lower = message.strip().lower()
        return any(re.search(pat, lower) for pat in approval_patterns)

    @staticmethod
    def get_live_context() -> str:
        """
        Read current DB state and return a context string injected into every
        Director LLM call.  Keeps the model grounded in the actual system state
        rather than inventing context.

        Fast synchronous DB read — typically < 10 ms.
        Returns empty string on any error (non-fatal).
        """
        lines = ["## Live Tantra System State (read from DB right now)"]
        try:
            from sqlalchemy import create_engine, select
            from sqlalchemy.orm import sessionmaker
            from tantra.core.config import settings
            from tantra.db.director import AgentTask, WeeklyPlan

            engine = create_engine(settings.database_sync_url, echo=False)
            DBSession = sessionmaker(bind=engine)
            with DBSession() as session:
                plan = session.execute(
                    select(WeeklyPlan)
                    .where(WeeklyPlan.status == "active")
                    .order_by(WeeklyPlan.week_start.desc())
                    .limit(1)
                ).scalar_one_or_none()

                if plan:
                    goals = plan.goals or {}
                    lines.append(
                        f"Active plan: Week {plan.week_number}/{plan.year} "
                        f"(starts {plan.week_start})"
                    )
                    lines.append(f"Primary topic: {goals.get('primary_topic', '?')}")
                    lines.append(
                        f"Targets: {goals.get('linkedin_posts_target', '?')} LinkedIn posts, "
                        f"{goals.get('progress_posts_target', '?')} progress posts"
                    )
                    if plan.director_analysis:
                        lines.append(f"Plan analysis: {plan.director_analysis[:250]}")

                    tasks = session.execute(
                        select(AgentTask)
                        .where(AgentTask.plan_id == plan.id)
                        .order_by(AgentTask.scheduled_for.asc())
                    ).scalars().all()

                    if tasks:
                        by_status: dict[str, list[str]] = {}
                        for t in tasks:
                            by_status.setdefault(t.status, []).append(t.task_type)
                        task_lines = [
                            f"  {status} ({len(types)}): {', '.join(types)}"
                            for status, types in by_status.items()
                        ]
                        lines.append("AgentTasks in this plan:")
                        lines.extend(task_lines)
                    else:
                        lines.append("AgentTasks: none yet")
                else:
                    lines.append("No active weekly plan. Run: tantra task run weekly_planning")
        except Exception as exc:
            lines.append(f"(Could not load system state: {exc})")

        return "\n".join(lines)

    async def review_week(
        self,
        plan_goals: dict[str, Any],
        plan_calendar: list[dict[str, Any]],
        published_content: list[dict[str, Any]],
        week_number: int,
    ) -> dict[str, Any]:
        """
        End-of-week review: analyse published vs planned, extract lessons.
        Result is stored in WeeklyPlan.performance_review and saved to Qdrant.

        Args:
            plan_goals:        Goals from this week's WeeklyPlan.goals
            plan_calendar:     Calendar from this week's WeeklyPlan.content_calendar
            published_content: List of ContentQueueItem dicts (status='published')
            week_number:       ISO week number for context

        Returns:
            Performance review dict to store in WeeklyPlan.performance_review
        """
        content_text = "\n".join(
            f"- [{item.get('published_at', '?')}] {item.get('draft_text', '')[:200]}"
            for item in published_content
        ) or "No content was published this week."

        prompt = PERFORMANCE_REVIEW_PROMPT.format(
            week_number=week_number,
            goals=json.dumps(plan_goals, indent=2),
            calendar=json.dumps(plan_calendar, indent=2),
            published_content=content_text,
        )

        try:
            raw = await self.think(task=prompt, temperature=0.5)
            review = _extract_json(raw)
            if not isinstance(review, dict):
                raise ValueError("Expected JSON dict from performance review")
        except Exception as exc:
            logger.warning(f"Director performance review LLM failed: {exc}")
            review = {
                "posts_published": len(published_content),
                "goals_met": False,
                "goal_attainment_pct": 0,
                "lessons": [f"Review generation failed: {exc}"],
                "next_week_priority": "Retry performance review",
            }

        logger.info(
            f"Director week {week_number} review: "
            f"{review.get('posts_published', '?')} posts, "
            f"goals_met={review.get('goals_met')}"
        )
        return review


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Any:
    """
    Extract the first JSON object or array from an LLM response string.

    Tries multiple strategies in order:
    1. Standard json.loads()  — handles clean JSON
    2. ast.literal_eval()     — handles Python-dict output (single quotes, True/False/None)
    3. Single-quote swap       — simple heuristic for trivial single-quote cases

    The LLM (especially local models via LiteLLM) sometimes returns Python-dict
    style responses with single-quoted keys/values.  ast.literal_eval() handles
    these correctly because Python booleans (True/False/None) are also valid.
    """
    import ast

    text = text.strip()
    # Strip markdown fences
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    # Find first { or [
    start = -1
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            start = i
            break
    if start == -1:
        raise ValueError("No JSON found in LLM response")

    json_str = text[start:]

    # Strategy 1: standard JSON
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Python literal (single-quoted keys/values, True/False/None)
    try:
        result = ast.literal_eval(json_str)
        if isinstance(result, (dict, list)):
            return result
    except Exception:
        pass

    # Strategy 3: naive single-quote → double-quote swap (last resort)
    try:
        fixed = json_str.replace("'", '"')
        return json.loads(fixed)
    except Exception:
        pass

    raise ValueError(
        f"Could not parse JSON from LLM response — "
        f"first 300 chars: {json_str[:300]!r}"
    )


def _format_performance(perf: PerformanceSummary) -> str:
    """Format a PerformanceSummary as a human-readable block for the LLM prompt."""
    lines = [
        f"Posts published last week: {perf.posts_published}",
        f"Posts drafted (incl. rejected): {perf.posts_drafted}",
        f"Posts rejected: {perf.posts_rejected}",
    ]
    if perf.top_topics:
        lines.append(f"Top topics: {', '.join(perf.top_topics)}")
    if perf.recent_post_previews:
        lines.append("Recent posts:")
        for preview in perf.recent_post_previews[:3]:
            lines.append(f"  - {preview[:120]}")
    if perf.memory_context:
        lines.append(f"\nRelevant strategic context:\n{perf.memory_context}")
    if perf.posts_published == 0 and not perf.recent_post_previews:
        lines.append("Note: No historical data available (first planning run).")
    return "\n".join(lines)


def _default_goals() -> dict[str, Any]:
    return {
        "linkedin_posts_target": 3,
        "progress_posts_target": 3,
        "engagement_target": 200,
        "primary_topic": "Building Tantra AI — local autonomous agents",
        "secondary_topics": ["Local LLMs", "Builder transparency"],
        "tone_guidance": "Authentic, builder voice. Share real numbers and real struggles.",
        "avoid": [],
    }


def _default_weekly_plan(week_start: date) -> dict[str, Any]:
    """Safe fallback plan when LLM call fails."""
    return {
        "analysis": (
            "Defaulting to standard weekly plan due to LLM unavailability. "
            "Running 3 research drafts and 3 progress posts."
        ),
        "goals": _default_goals(),
        "content_calendar": [
            {"day": "Monday",    "platform": "linkedin", "task_type": "research_draft",
             "topic": "AI agents and automation",   "tone": "data-driven",  "priority": "high",
             "instructions": "Focus on practical AI agent use cases for developers."},
            {"day": "Tuesday",   "platform": "linkedin", "task_type": "progress_post",
             "topic": "Tantra AI build update",     "tone": "authentic",    "priority": "medium",
             "instructions": "Share a genuine update about building the Director layer."},
            {"day": "Wednesday", "platform": "linkedin", "task_type": "research_draft",
             "topic": "Local LLMs on consumer hardware", "tone": "technical", "priority": "medium",
             "instructions": "Highlight running 70B models locally on RTX 5070 Ti."},
            {"day": "Thursday",  "platform": "linkedin", "task_type": "progress_post",
             "topic": "Tantra AI Phase 2 progress",  "tone": "storytelling", "priority": "medium",
             "instructions": "Tell the story of building the Director agent."},
            {"day": "Friday",    "platform": "linkedin", "task_type": "research_draft",
             "topic": "Building in public — what I learned", "tone": "authentic", "priority": "low",
             "instructions": "Reflect on this week's build. Share a real insight."},
        ],
    }


def _calendar_to_tasks(plan: WeeklyPlanData) -> list[dict[str, Any]]:
    """
    Fallback: convert ContentCalendarEntry list → task dicts directly,
    using standard scheduling defaults.
    """
    default_times = {
        "research_draft": "07:00",
        "progress_post": "09:30",
        "youtube_script": "10:00",
        "analytics_review": "17:00",
    }
    return [
        {
            "task_type": entry.task_type,
            "assigned_to": "social_crew" if entry.task_type in ("research_draft", "youtube_script") else "cmo",
            "priority": entry.priority,
            "scheduled_day": entry.day,
            "scheduled_time": default_times.get(entry.task_type, "09:00"),
            "instructions": entry.instructions,
            "context": {
                "topic_hint": entry.topic,
                "tone_override": entry.tone,
                "platform": entry.platform,
            },
        }
        for entry in plan.content_calendar
    ]


def _resolve_scheduled_datetime(
    week_start: date,
    day_name: str,
    time_str: str,
) -> datetime:
    """
    Convert 'Monday' + '07:00' relative to week_start into an absolute datetime.
    Falls back to week_start + 09:00 if parsing fails.
    """
    day_offsets = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    }
    offset = day_offsets.get(day_name.lower(), 0)
    target_date = week_start + timedelta(days=offset)
    try:
        hour, minute = [int(x) for x in time_str.split(":")]
    except Exception:
        hour, minute = 9, 0
    return datetime(target_date.year, target_date.month, target_date.day, hour, minute)
