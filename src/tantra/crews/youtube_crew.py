"""
Tantra AI — YouTube Crew (Phase 3)
CrewAI sequential crew for YouTube video script generation.

Pipeline (sequential):
  TopicResearcher  (worker tier)   — trend research + competitor gap analysis
  ScriptWriter     (director tier) — scene-by-scene video script with narration + visual prompts
  SEOOptimizer     (worker tier)   — title, description, tags, thumbnail prompt
  QualityReviewer  (fast tier)     — hook strength, retention markers, brand voice, CTA check

Output: a structured YouTubeScript dict (stored as JSON in YouTubeVideo.script).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tools import tool

from tantra.core.config import ModelTier, settings
from tantra.tools.youtube import youtube_search

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# YouTubeScript dataclass — structured output from the crew
# ---------------------------------------------------------------------------

@dataclass
class YouTubeScene:
    """A single scene in a YouTube video script."""
    id: int
    type: str           # hook | content | transition | cta | outro
    duration_seconds: int
    narration: str      # TTS text — what the presenter says
    visual_prompt: str  # AI image/video generation prompt for this scene
    b_roll_description: str  # Human-readable description of background footage
    on_screen_text: Optional[str] = None  # Text overlay (lower-thirds, captions)


@dataclass
class YouTubeScript:
    """Full video script as produced by YouTubeCrew."""
    title: str
    duration_target_seconds: int
    hook: str
    scenes: list[YouTubeScene]
    call_to_action: str
    thumbnail_concept: str      # Human-readable thumbnail idea
    thumbnail_prompt: str       # FLUX.1 generation prompt
    description: str            # SEO YouTube description (250+ words)
    tags: list[str]             # 15-20 keyword tags

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dict for JSON storage."""
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# LLM factory (same pattern as social_crew.py)
# ---------------------------------------------------------------------------

def _make_llm(tier: ModelTier) -> LLM:
    return LLM(
        model=f"openai/{tier.value}",
        base_url=f"{settings.litellm_base_url}/v1",
        api_key=settings.litellm_key,
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool("YouTube Trend Researcher")
def research_youtube_trends(query: str) -> str:
    """
    Search YouTube for trending videos on a topic.
    Returns top video titles, channels, and publish dates to inform script direction.
    """
    result = youtube_search(query, max_results=8)
    if "error" in result:
        return f"Search failed: {result['error']}"
    videos = result.get("results", [])
    if not videos:
        return "No trending videos found for this query."
    lines = [f"- {v['title']} | {v['channel']} | {v['published_at'][:10]}" for v in videos]
    return "Trending YouTube videos:\n" + "\n".join(lines)


@tool("YouTube Competitor Gap Analyser")
def analyse_competitor_gap(topic: str) -> str:
    """
    Search YouTube for content on a topic and identify what angles are MISSING
    or underserved — gaps the Tantra AI channel can own.
    """
    result = youtube_search(f"{topic} tutorial guide 2025", max_results=6)
    if "error" in result:
        return f"Search failed: {result['error']}"
    videos = result.get("results", [])
    covered = [v["title"] for v in videos]
    return (
        f"Existing content on '{topic}':\n"
        + "\n".join(f"  • {t}" for t in covered)
        + "\n\nYour task: identify 3 angles NOT covered by these videos "
        "that would resonate with a builder/engineer audience."
    )


# ---------------------------------------------------------------------------
# Crew builder
# ---------------------------------------------------------------------------

def build_youtube_crew(
    topic_hint: str = "",
    director_guidance: str = "",
    channel_context: str = "",
    recent_video_titles: Optional[list[str]] = None,
    verbose: bool = True,
) -> Crew:
    """
    Build and return the YouTube script-generation CrewAI crew.

    Sequential process:
      researcher → script_writer → seo_optimizer → quality_reviewer

    Args:
        topic_hint:          Director's topic guidance (from AgentTask.instructions).
        director_guidance:   Tone/style notes from active WeeklyPlan.goals.
        channel_context:     Current channel focus statement for brand voice alignment.
        recent_video_titles: Last 5 published video titles to avoid repetition.
        verbose:             CrewAI verbose mode.

    Returns a Crew whose final task_output is a JSON string (YouTubeScript dict).
    """
    recent = recent_video_titles or []
    recent_str = "\n".join(f"  - {t}" for t in recent) if recent else "  (no recent videos yet)"
    channel_ctx = channel_context or (
        "Building Tantra AI — a fully local autonomous agent stack in Python. "
        "Audience: engineers, founders, AI practitioners building in public."
    )

    # ── Topic Researcher ──────────────────────────────────────────────────────
    researcher = Agent(
        role="YouTube Content Research Analyst",
        goal=(
            "Identify trending angles, competitor gaps, and audience pain points "
            "for YouTube videos on the assigned topic. Produce a structured research brief."
        ),
        backstory=(
            "You are an expert YouTube content strategist who understands what makes "
            "technical content go viral in the builder/AI community. You analyse trends, "
            "spot underserved angles, and always ground your recommendations in real data "
            "from what's already out there."
        ),
        llm=_make_llm(ModelTier.worker),
        tools=[research_youtube_trends, analyse_competitor_gap],
        max_iter=6,
        verbose=verbose,
        allow_delegation=False,
    )

    # ── Script Writer ─────────────────────────────────────────────────────────
    script_writer = Agent(
        role="Senior YouTube Script Writer",
        goal=(
            "Write a compelling, scene-by-scene YouTube video script with strong hooks, "
            "clear narration text per scene, and specific visual prompts for AI generation."
        ),
        backstory=(
            "You are a seasoned YouTube scriptwriter who understands retention, pacing, "
            "and the builder/technical audience. You write scripts that are authentic and "
            "story-driven — not corporate or polished. You know the first 30 seconds are "
            "everything. Your scripts include specific narration text (for TTS) and "
            "visual prompts (for AI image/video generation) for every scene."
        ),
        llm=_make_llm(ModelTier.director),
        max_iter=5,
        verbose=verbose,
        allow_delegation=False,
    )

    # ── SEO Optimizer ─────────────────────────────────────────────────────────
    seo_optimizer = Agent(
        role="YouTube SEO Specialist",
        goal=(
            "Write a keyword-optimised title (≤100 chars), SEO description (250+ words), "
            "15-20 tags, and a concrete FLUX.1 image generation prompt for the thumbnail."
        ),
        backstory=(
            "You are a YouTube SEO expert who understands search intent, click-through rate "
            "optimisation, and how to write titles that balance curiosity and clarity. "
            "You know that for technical builder content, specific > clickbait. "
            "Your thumbnail prompts are precise enough for an AI image model to generate "
            "a compelling visual without any human interpretation."
        ),
        llm=_make_llm(ModelTier.worker),
        max_iter=4,
        verbose=verbose,
        allow_delegation=False,
    )

    # ── Quality Reviewer ──────────────────────────────────────────────────────
    quality_reviewer = Agent(
        role="Content Quality and Brand Voice Reviewer",
        goal=(
            "Validate the complete script for hook strength, retention markers, CTA quality, "
            "brand voice consistency, and technical accuracy. Output the final approved "
            "script as a valid JSON object matching the YouTubeScript schema."
        ),
        backstory=(
            "You are a meticulous quality reviewer who ensures every video represents "
            "the Tantra AI brand — authentic, technical, building-in-public, zero hype. "
            "You check that the hook grabs in the first 5 seconds, that each scene has "
            "a clear narration and visual prompt, that the CTA is specific and genuine, "
            "and that the thumbnail prompt will produce a compelling image. "
            "Your final output is ALWAYS a valid JSON object — no markdown, no preamble."
        ),
        llm=_make_llm(ModelTier.fast),
        max_iter=4,
        verbose=verbose,
        allow_delegation=False,
    )

    # ---------------------------------------------------------------------------
    # Tasks
    # ---------------------------------------------------------------------------

    topic_directive = (
        f"\n\nTopic from Director: {topic_hint}" if topic_hint else ""
    )
    guidance_directive = (
        f"\n\nTone guidance: {director_guidance}" if director_guidance else ""
    )

    task_research = Task(
        description=(
            f"Research YouTube trends and competitor content for the following:\n"
            f"Channel focus: {channel_ctx}\n"
            f"Recent videos (avoid repetition):\n{recent_str}"
            + topic_directive
            + "\n\nDeliver: a structured brief with 3 potential video angles, "
            "audience pain points each angle addresses, and what makes each unique "
            "compared to existing YouTube content."
        ),
        expected_output=(
            "A research brief with 3 video angle options, each with: "
            "target audience pain point, unique differentiator vs existing content, "
            "suggested hook concept, estimated appeal score (1-10)."
        ),
        agent=researcher,
    )

    task_write_script = Task(
        description=(
            "Using the research brief, write a complete scene-by-scene YouTube script "
            "for the highest-potential angle. Requirements:\n"
            "- Duration: 6-10 minutes (360-600 seconds)\n"
            "- Scenes: 6-12 scenes (hook + content + cta minimum)\n"
            "- Each scene must include: narration text (verbatim for TTS), "
            "visual_prompt (specific prompt for FLUX.1/Wan2.1 generation), "
            "b_roll_description (human-readable footage description), "
            "duration_seconds, type (hook/content/transition/cta/outro)\n"
            "- Hook scene: must grab attention in first 15 seconds\n"
            "- CTA: specific, genuine — subscriber growth / build-in-public theme\n"
            "- Narration must be conversational and match the builder/authentic brand voice"
            + guidance_directive
        ),
        expected_output=(
            "A complete video script with all scenes written out. "
            "Each scene clearly labelled with type, duration, narration, "
            "visual prompt, and b_roll description."
        ),
        agent=script_writer,
        context=[task_research],
    )

    task_seo = Task(
        description=(
            "Based on the video script, produce the complete YouTube SEO package:\n"
            "1. Title: ≤100 chars, curiosity-driving, keyword-rich, no clickbait\n"
            "2. Description: 250+ words, first 125 chars critical (above the fold), "
            "   include 5-8 target keywords naturally, end with subscribe CTA\n"
            "3. Tags: 15-20 tags mixing broad (AI, automation) and specific "
            "   (local LLMs, autonomous agents, building in public)\n"
            "4. Thumbnail concept: 1-2 sentences describing what a human would draw\n"
            "5. Thumbnail prompt: specific FLUX.1-Schnell generation prompt "
            "   (style: dark tech aesthetic, high contrast, minimal text overlay)\n"
            "6. Hook (1 sentence): the verbal hook for the first 5 seconds"
        ),
        expected_output=(
            "YouTube SEO package: title, description, tags list, "
            "thumbnail concept (human-readable), thumbnail prompt (AI-ready), hook sentence."
        ),
        agent=seo_optimizer,
        context=[task_write_script],
    )

    task_review = Task(
        description=(
            "Review and assemble the final script package. Validate:\n"
            "✓ Hook grabs attention within 15 seconds\n"
            "✓ Every scene has narration, visual_prompt, b_roll_description, duration_seconds\n"
            "✓ Total duration is 360-600 seconds\n"
            "✓ CTA is genuine, not forced\n"
            "✓ Brand voice is authentic/technical/builder — not corporate\n"
            "✓ Thumbnail prompt is specific enough for FLUX.1 to generate without guessing\n\n"
            "Output the COMPLETE final package as a single JSON object with this EXACT schema:\n"
            "{\n"
            '  "title": "...",\n'
            '  "duration_target_seconds": 480,\n'
            '  "hook": "...",\n'
            '  "scenes": [\n'
            '    {\n'
            '      "id": 1,\n'
            '      "type": "hook",\n'
            '      "duration_seconds": 20,\n'
            '      "narration": "...",\n'
            '      "visual_prompt": "...",\n'
            '      "b_roll_description": "...",\n'
            '      "on_screen_text": null\n'
            '    }\n'
            '  ],\n'
            '  "call_to_action": "...",\n'
            '  "thumbnail_concept": "...",\n'
            '  "thumbnail_prompt": "...",\n'
            '  "description": "...",\n'
            '  "tags": ["...", "..."]\n'
            "}\n"
            "Output ONLY the JSON object. No preamble, no markdown fences, no explanation."
        ),
        expected_output=(
            "A single valid JSON object matching the YouTubeScript schema exactly. "
            "No markdown, no extra text — just the JSON."
        ),
        agent=quality_reviewer,
        context=[task_write_script, task_seo],
    )

    return Crew(
        agents=[researcher, script_writer, seo_optimizer, quality_reviewer],
        tasks=[task_research, task_write_script, task_seo, task_review],
        process=Process.sequential,
        verbose=verbose,
        memory=False,
    )


# ---------------------------------------------------------------------------
# Script parser — converts crew text output → YouTubeScript dataclass
# ---------------------------------------------------------------------------

def parse_script_output(raw_output: str) -> Optional[dict[str, Any]]:
    """
    Parse the quality reviewer's text output into a YouTubeScript dict.

    The quality reviewer is instructed to output only JSON, but LLMs sometimes
    wrap it in markdown fences. This parser strips common wrappers and validates
    the result has the required keys.

    Returns a plain dict (ready for JSON column storage), or None on parse failure.
    """
    text = raw_output.strip()

    # Strip markdown code fences if present (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        inner = "\n".join(lines[1:])
        if inner.endswith("```"):
            inner = inner[:-3].strip()
        text = inner.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # Try to find first { and last } in case of extra text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.error("YouTubeCrew: failed to parse script JSON: %s", exc)
                return None
        else:
            logger.error("YouTubeCrew: no JSON object found in output: %s", exc)
            return None

    # Validate required top-level keys
    required = {"title", "scenes", "thumbnail_prompt", "description", "tags"}
    missing = required - set(data.keys())
    if missing:
        logger.warning("YouTubeCrew: script JSON missing keys: %s", missing)
        # Not fatal — return partial data, caller can decide

    return data
