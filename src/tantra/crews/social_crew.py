"""
Tantra AI — Social Media Crew (Phase 1)
CrewAI hierarchical crew for LinkedIn + YouTube content operations.

Hierarchy:
  CMO (LeaderAgent, director tier)
    ├─ Researcher   (WorkerAgent, manager tier)  — trend analysis
    ├─ ContentWriter (WorkerAgent, worker tier)  — post/script writing
    ├─ Publisher    (WorkerAgent, worker tier)   — platform distribution
    └─ Analyst      (WorkerAgent, worker tier)   — performance tracking
"""
from __future__ import annotations

from crewai import Agent, Crew, Process, Task
from crewai.tools import tool

from tantra.core.config import ModelTier, settings
from tantra.tools.linkedin import linkedin_post_text
from tantra.tools.youtube import youtube_search


def _litellm_model(tier: ModelTier) -> str:
    """Return the LiteLLM proxy model string for a given tier."""
    return f"openai/{tier.value}"   # LiteLLM routes based on alias


# ---------------------------------------------------------------------------
# Tool wrappers for CrewAI
# ---------------------------------------------------------------------------

@tool("LinkedIn Post Publisher")
def publish_linkedin_post(access_token: str, author_urn: str, text: str) -> str:
    """Publish a text post to LinkedIn. Returns post URN or error."""
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        linkedin_post_text(access_token=access_token, author_urn=author_urn, text=text)
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

def build_social_media_crew(verbose: bool = True) -> Crew:
    """
    Build and return the Social Media CrewAI crew.

    Returns a Crew object ready to execute social media content tasks.
    """
    llm_base = f"{settings.litellm_base_url}/v1"
    llm_key = settings.litellm_key

    # ── CMO — Chief Marketing Officer ────────────────────────────────────────
    cmo = Agent(
        role="Chief Marketing Officer",
        goal=(
            "Drive maximum engagement and follower growth on LinkedIn and YouTube "
            "through strategic content planning and data-driven decisions."
        ),
        backstory=(
            "You are a world-class CMO with 15 years of experience in digital marketing. "
            "You understand what resonates with professional audiences on LinkedIn and "
            "content-hungry viewers on YouTube. You think in campaigns, not single posts."
        ),
        llm=f"openai/{ModelTier.director.value}",
        max_iter=settings.agent_max_iterations,
        verbose=verbose,
        allow_delegation=True,    # CMO can delegate to workers
    )

    # ── Researcher ────────────────────────────────────────────────────────────
    researcher = Agent(
        role="Research Analyst",
        goal="Identify trending topics, viral content patterns, and audience pain points",
        backstory=(
            "You are a sharp research analyst who spots trends before they go mainstream. "
            "You use data to back every recommendation and present findings clearly."
        ),
        llm=f"openai/{ModelTier.manager.value}",
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
        backstory=(
            "You are a skilled content creator who writes with the perfect balance of "
            "expertise and human touch. You know that the best LinkedIn posts tell a story, "
            "have a strong hook, and end with a call to action. For YouTube, you write "
            "attention-grabbing scripts with a strong thumbnail concept."
        ),
        llm=f"openai/{ModelTier.worker.value}",
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
        llm=f"openai/{ModelTier.worker.value}",
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
        llm=f"openai/{ModelTier.worker.value}",
        max_iter=3,
        verbose=verbose,
        allow_delegation=False,
    )

    # ---------------------------------------------------------------------------
    # Task definitions
    # ---------------------------------------------------------------------------

    task_research = Task(
        description=(
            "Research the top 5 trending topics in AI, automation, and productivity "
            "on LinkedIn and YouTube this week. For each topic, note: "
            "1) Why it's trending, 2) Target audience, 3) Content angle that performs best."
        ),
        expected_output=(
            "A structured research brief with 5 trending topics, each with audience "
            "profile, content angle, and 3 potential post hooks."
        ),
        agent=researcher,
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
    return Crew(
        agents=[cmo, researcher, content_writer, publisher, analyst],
        tasks=[task_research, task_write_linkedin, task_write_youtube, task_analyse],
        process=Process.hierarchical,
        manager_agent=cmo,
        verbose=verbose,
        memory=True,
        embedder={
            "provider": "openai",
            "config": {
                "model": ModelTier.embedder.value,
                "api_base": f"{settings.litellm_base_url}/v1",
                "api_key": settings.litellm_key,
            },
        },
    )
