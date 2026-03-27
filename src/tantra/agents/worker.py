"""
Tantra AI — Worker Agent
A focused, single-responsibility agent that:
  - Executes a specific task assigned by a Leader
  - Uses an appropriate model tier (worker / coder / fast)
  - Maintains its own episodic memory
  - Reports result back to Leader
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from tantra.agents.base import AgentResult, TantraAgent
from tantra.core.config import ModelTier

logger = logging.getLogger(__name__)


class WorkerAgent(TantraAgent):
    """
    General-purpose worker agent.
    Specialise by subclassing or by passing a focused system prompt.

    Example:
        writer = WorkerAgent(
            name="ContentWriter",
            role="Senior Content Writer",
            goal="Write engaging LinkedIn posts that drive profile visits",
            model_tier=ModelTier.worker,
            skills=["linkedin_post_format", "seo_hashtags"],
        )
    """

    def __init__(
        self,
        name: str,
        role: str,
        goal: str,
        model_tier: ModelTier = ModelTier.worker,
        skills: Optional[list[str]] = None,
        tools: Optional[list[Any]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, role=role, goal=goal, model_tier=model_tier, **kwargs)
        self.skills = skills or []
        self.tools = tools or []

    async def execute(self, task: str, context: Optional[str] = None) -> AgentResult:
        """Execute a focused task and return a structured result."""
        logger.info(f"Worker {self.name} executing: {task[:80]}...")

        try:
            # Build task prompt with skills context
            skill_hint = ""
            if self.skills:
                skill_hint = f"\nApply these skills: {', '.join(self.skills)}.\n"

            full_task = f"{skill_hint}{task}"
            output = await self.think(task=full_task, context=context)

            return AgentResult(
                agent_id=self.agent_id,
                agent_name=self.name,
                task=task,
                output=output,
                success=True,
                metadata={"skills_used": self.skills},
            )
        except Exception as exc:
            logger.exception(f"Worker {self.name} failed: {exc}")
            return AgentResult(
                agent_id=self.agent_id,
                agent_name=self.name,
                task=task,
                output="",
                success=False,
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# Pre-built specialised workers (ready to use)
# ---------------------------------------------------------------------------

def make_research_worker() -> WorkerAgent:
    return WorkerAgent(
        name="Researcher",
        role="Research Analyst",
        goal="Find trends, gather competitive intelligence, and summarise findings",
        model_tier=ModelTier.manager,
        skills=["web_research", "trend_analysis", "competitive_intel"],
    )


def make_content_writer() -> WorkerAgent:
    return WorkerAgent(
        name="ContentWriter",
        role="Senior Content Writer",
        goal="Write compelling, platform-native content that drives engagement",
        model_tier=ModelTier.worker,
        skills=["linkedin_post", "youtube_script", "copywriting", "seo_hashtags"],
    )


def make_publisher() -> WorkerAgent:
    return WorkerAgent(
        name="Publisher",
        role="Content Distribution Specialist",
        goal="Schedule and publish content across platforms at optimal times",
        model_tier=ModelTier.worker,
        skills=["linkedin_api", "youtube_api", "optimal_timing"],
    )


def make_analyst() -> WorkerAgent:
    return WorkerAgent(
        name="Analyst",
        role="Performance Analytics Specialist",
        goal="Track metrics, identify patterns, and suggest improvements",
        model_tier=ModelTier.worker,
        skills=["analytics", "reporting", "a_b_testing"],
    )


def make_coder() -> WorkerAgent:
    return WorkerAgent(
        name="DevWorker",
        role="Software Engineer",
        goal="Write clean, tested code for automation scripts and integrations",
        model_tier=ModelTier.coder,
        skills=["python", "api_integration", "code_review"],
    )
