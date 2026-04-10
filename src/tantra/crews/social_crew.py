"""
Tantra AI — Social Media Crew (Phase 1)
CrewAI sequential crew for LinkedIn + YouTube content operations.

Pipeline (sequential):
  Researcher   (manager tier → phi4:14b)  — trend analysis
  ContentWriter (worker tier → phi4:14b)  — post/script writing
  Publisher    (worker tier → phi4:14b)   — platform distribution
  Analyst      (worker tier → phi4:14b)   — performance tracking

CMO (director tier) is reserved for the Phase 2 Director planning crew.
"""
from __future__ import annotations

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tools import tool

from tantra.core.agent_loader import AgentConfigLoader
from tantra.core.config import ModelTier, settings
from tantra.tools.youtube import youtube_search


def _make_llm(tier: ModelTier, base_url: str, api_key: str) -> LLM:
    """
    Create a CrewAI LLM object routed through the LiteLLM proxy.

    Using LLM() instead of a bare model string ensures api_base and api_key
    are forwarded — bare strings like 'openai/director' go directly to
    api.openai.com and fail with 401 when no OPENAI_API_KEY is set.
    """
    return LLM(
        model=f"openai/{tier.value}",
        base_url=base_url,
        api_key=api_key,
    )


# ---------------------------------------------------------------------------
# Tool wrappers for CrewAI
# ---------------------------------------------------------------------------

@tool("LinkedIn Post Publisher")
def publish_linkedin_post(text: str, platform: str = "linkedin") -> str:
    """
    Publish a text post to LinkedIn (or any other configured platform).
    Uses Zernio if ZERNIO_API_KEY is set (recommended — no developer app needed).
    Falls back to direct LinkedIn API if Zernio is not configured.
    Returns post ID/URN or error message.
    """
    import asyncio

    if settings.zernio_enabled:
        from tantra.tools.zernio_client import zernio_post_text
        result = asyncio.get_event_loop().run_until_complete(
            zernio_post_text(content=text, platform=platform)
        )
    else:
        # Fallback: requires access_token to be resolved at runtime from DB
        # In the crew context this won't work without a token — the agent should
        # route publishing through the Celery task instead of calling this directly
        return (
            "Direct publishing requires Zernio configuration. "
            "Set ZERNIO_API_KEY in .env to enable autonomous posting. "
            "Alternatively, the content will be queued for the scheduled publish task."
        )

    return str(result)


@tool("YouTube Trend Searcher")
def search_youtube_trends(query: str) -> str:
    """Search YouTube for trending videos on a topic. Returns video titles and stats."""
    result = youtube_search(query, max_results=5)
    if "error" in result:
        return f"Search failed: {result['error']}"
    videos = result.get("results", [])
    return "\n".join(f"- {v['title']} ({v['channel']})" for v in videos) or "No results found"


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

def build_social_media_crew(
    verbose: bool = True,
    topic_hint: str = "",
    director_guidance: str = "",
) -> Crew:
    """
    Build and return the Social Media CrewAI crew.

    Sequential process: research → write_linkedin → write_youtube → analyse
    CMO agent lives in the Director planning crew (Phase 2) — not used here.

    Args:
        verbose:          Pass to CrewAI agents for debug output.
        topic_hint:       Optional topic seed from the Director's weekly plan.
                          Injected into the research task description.
        director_guidance: Optional tone/style guidance from Director's goals.
                           Appended to the content writer task description.

    Returns a Crew object ready to execute social media content tasks.
    """
    llm_base = f"{settings.litellm_base_url}/v1"
    llm_key = settings.litellm_key

    # Hot-reload: backstories read fresh from agents/ config files on every crew build.
    # Edit soul.md/skills.md on host → next research_draft task picks up the change.
    _researcher_cfg = AgentConfigLoader("social-crew/researcher")
    _drafter_cfg = AgentConfigLoader("social-crew/drafter")

    # ── Researcher ────────────────────────────────────────────────────────────
    researcher = Agent(
        role="Research Analyst",
        goal="Identify trending topics, viral content patterns, and audience pain points",
        backstory=_researcher_cfg.build_crewai_backstory(),
        llm=_make_llm(ModelTier.manager, llm_base, llm_key),
        tools=[search_youtube_trends],
        max_iter=5,
        verbose=verbose,
        allow_delegation=False,
    )

    # ── Content Writer ────────────────────────────────────────────────────────
    content_writer = Agent(
        role="Senior Content Writer",
        goal=(
            "Write compelling LinkedIn posts and YouTube scripts that drive "
            "engagement, comments, and shares"
        ),
        backstory=_drafter_cfg.build_crewai_backstory(),
        llm=_make_llm(ModelTier.worker, llm_base, llm_key),
        max_iter=5,
        verbose=verbose,
        allow_delegation=False,
    )

    # ── Publisher ─────────────────────────────────────────────────────────────
    publisher = Agent(
        role="Content Distribution Specialist",
        goal="Publish content at optimal times and track distribution metrics",
        backstory=(
            "You are a distribution expert who knows the best times to post on each "
            "platform. You handle all publishing tasks and confirm successful distribution."
        ),
        llm=_make_llm(ModelTier.worker, llm_base, llm_key),
        tools=[publish_linkedin_post],
        max_iter=3,
        verbose=verbose,
        allow_delegation=False,
    )

    # ── Analyst ───────────────────────────────────────────────────────────────
    analyst = Agent(
        role="Performance Analytics Specialist",
        goal="Analyse post performance and provide actionable improvement recommendations",
        backstory=(
            "You are a data-driven analyst who turns raw metrics into insights. "
            "You identify what's working, what's not, and precisely why."
        ),
        llm=_make_llm(ModelTier.worker, llm_base, llm_key),
        max_iter=3,
        verbose=verbose,
        allow_delegation=False,
    )

    # ---------------------------------------------------------------------------
    # Task definitions
    # ---------------------------------------------------------------------------

    # Inject Director topic hint into research task if provided
    topic_directive = (
        f"\n\nDirector guidance: This week's primary topic is '{topic_hint}'. "
        f"Prioritise research and content ideas around this theme."
        if topic_hint else ""
    )

    task_research = Task(
        description=(
            "Research the top 5 trending topics in AI, automation, and productivity "
            "on LinkedIn and YouTube this week. For each topic, note: "
            "1) Why it's trending, 2) Target audience, 3) Content angle that performs best."
            + topic_directive
        ),
        expected_output=(
            "A structured research brief with 5 trending topics, each with audience "
            "profile, content angle, and 3 potential post hooks."
        ),
        agent=researcher,
    )

    # Inject Director tone/style guidance into writing task if provided
    guidance_directive = (
        f"\n\nTone guidance from Director: {director_guidance}"
        if director_guidance else ""
    )

    task_write_linkedin = Task(
        description=(
            "Using the research brief, write 3 LinkedIn posts. Each post should: "
            "- Be 150-300 words "
            "- Start with a provocative or relatable hook "
            "- Include a personal insight or data point "
            "- End with an engaging question or CTA "
            "- Include 3-5 relevant hashtags "
            "Format clearly with labels: POST 1, POST 2, POST 3."
            + guidance_directive
        ),
        expected_output=(
            "3 ready-to-publish LinkedIn posts, each labelled and formatted, "
            "with a suggested best time to post."
        ),
        agent=content_writer,
        context=[task_research],
    )

    task_write_youtube = Task(
        description=(
            "Write a YouTube video script (5-8 minutes) for the highest-potential topic "
            "from the research. Include: hook (0-30s), main content (3 key points), "
            "CTA, and a thumbnail concept description."
        ),
        expected_output=(
            "Full YouTube script with timing markers, thumbnail concept, "
            "title options (5), and suggested tags."
        ),
        agent=content_writer,
        context=[task_research],
    )

    task_analyse = Task(
        description=(
            "Review the content created and provide a performance prediction: "
            "estimated engagement rate, likely viral potential (1-10), "
            "and 3 specific improvements for each LinkedIn post."
        ),
        expected_output=(
            "Performance prediction report with scores and actionable improvements "
            "for each piece of content."
        ),
        agent=analyst,
        context=[task_write_linkedin, task_write_youtube],
    )

    # ---------------------------------------------------------------------------
    # Crew assembly
    # ---------------------------------------------------------------------------
    # Sequential process: research → write_linkedin → write_youtube → analyse
    # tasks_output[1] is reliably the content writer's LinkedIn posts text.
    #
    # Hierarchical mode caused the CMO to store its delegation plan in tasks_output
    # instead of the actual post content written by the content_writer agent.
    # CMO agent is kept for future Director-level campaign planning crew.
    return Crew(
        agents=[researcher, content_writer, publisher, analyst],
        tasks=[task_research, task_write_linkedin, task_write_youtube, task_analyse],
        process=Process.sequential,
        verbose=verbose,
        memory=False,
    )
