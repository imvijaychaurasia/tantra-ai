"""
Tantra AI — Leader Agent
The LEADER:
  - Uses frontier/director model tier
  - Reads its own memory AND subordinates' memories
  - Plans, delegates, and synthesises results from workers
  - Drives CrewAI Process.hierarchical crews
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from tantra.agents.base import AgentResult, TantraAgent
from tantra.core.config import ModelTier

logger = logging.getLogger(__name__)


DELEGATION_PROMPT = """
You are a LEADER agent. You have received a high-level goal.
Your job is to:
1. Analyse the goal
2. Break it into sub-tasks (maximum {max_subtasks})
3. Assign each sub-task to the most appropriate worker role
4. Return a JSON plan

Available worker roles: {worker_roles}

Goal: {goal}

Context from team memory:
{memory_context}

Return your plan as JSON with this exact structure:
{{
  "analysis": "Brief analysis of the goal (2-3 sentences)",
  "subtasks": [
    {{
      "id": "task_1",
      "description": "What needs to be done",
      "assigned_to": "worker_role_name",
      "priority": "high|medium|low",
      "depends_on": []
    }}
  ],
  "success_criteria": "How to know this goal is achieved"
}}
"""

SYNTHESIS_PROMPT = """
You are a LEADER agent. Your workers have completed their tasks.
Review their results, identify what succeeded and what failed,
and produce a final synthesised output for the original goal.

Original goal: {goal}
Worker results:
{worker_results}

Produce a final response that:
1. Summarises what was accomplished
2. Highlights any issues or gaps
3. Provides the actual deliverable (post, report, action, etc.)
"""


class LeaderAgent(TantraAgent):
    """
    Hierarchical leader that plans tasks and coordinates workers.

    Example:
        leader = LeaderAgent(
            name="CMO",
            role="Chief Marketing Officer",
            goal="Grow LinkedIn following by 20% this month",
            model_tier=ModelTier.director,
            worker_roles=["research", "create", "publish", "analyze"],
        )
        result = await leader.execute("Write and post 3 LinkedIn articles this week")
    """

    def __init__(
        self,
        name: str,
        role: str,
        goal: str,
        worker_roles: list[str],
        model_tier: ModelTier = ModelTier.director,
        max_subtasks: int = 5,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, role=role, goal=goal, model_tier=model_tier, **kwargs)
        self.worker_roles = worker_roles
        self.max_subtasks = max_subtasks
        self._subordinate_memory_namespaces: list[str] = []

    def register_subordinate(self, namespace: str) -> None:
        """Register a worker's memory namespace so the leader can read it."""
        if namespace not in self._subordinate_memory_namespaces:
            self._subordinate_memory_namespaces.append(namespace)
            logger.debug(f"Leader {self.name} registered subordinate memory: {namespace}")

    async def plan(self, goal: str, memory_context: str = "") -> dict[str, Any]:
        """
        Ask the LLM to decompose a goal into a structured delegation plan.
        Returns a dict with 'subtasks' and 'success_criteria'.
        """
        prompt = DELEGATION_PROMPT.format(
            max_subtasks=self.max_subtasks,
            worker_roles=", ".join(self.worker_roles),
            goal=goal,
            memory_context=memory_context or "No prior context available.",
        )
        raw = await self.think(task=prompt, temperature=0.5)

        # Extract JSON from response (may be wrapped in markdown fences)
        try:
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            return json.loads(raw)
        except (json.JSONDecodeError, IndexError):
            logger.warning("Leader failed to produce valid JSON plan — returning raw response")
            return {"analysis": raw, "subtasks": [], "success_criteria": ""}

    async def synthesise(self, goal: str, worker_results: list[AgentResult]) -> str:
        """
        Combine worker results into a final coherent output.
        """
        results_text = "\n\n".join(
            f"[{r.agent_name}] {'✓' if r.success else '✗'}\n{r.output}"
            for r in worker_results
        )
        prompt = SYNTHESIS_PROMPT.format(goal=goal, worker_results=results_text)
        return await self.think(task=prompt, temperature=0.6)

    async def execute(self, task: str, context: Optional[str] = None) -> AgentResult:
        """
        Full leader execution cycle:
        1. Plan (decompose task into subtasks)
        2. [Delegates to workers — wired externally via CrewAI / LangGraph]
        3. Synthesise final result

        NOTE: In standalone mode (no workers connected), the leader
        executes all sub-tasks itself using its own model.
        """
        logger.info(f"Leader {self.name} executing: {task[:80]}...")

        try:
            plan = await self.plan(task, memory_context=context or "")
            analysis = plan.get("analysis", "")
            subtasks = plan.get("subtasks", [])

            if not subtasks:
                # Solo mode — no delegation possible
                output = await self.think(task=task, context=context, temperature=0.7)
                return AgentResult(
                    agent_id=self.agent_id,
                    agent_name=self.name,
                    task=task,
                    output=output,
                    success=True,
                    metadata={"mode": "solo", "plan": plan},
                )

            # When running with a full crew, subtasks are passed to CrewAI.
            # Here we return the plan so the crew orchestrator can dispatch them.
            return AgentResult(
                agent_id=self.agent_id,
                agent_name=self.name,
                task=task,
                output=analysis,
                success=True,
                metadata={"mode": "delegation", "plan": plan},
            )

        except Exception as exc:
            logger.exception(f"Leader {self.name} failed: {exc}")
            return AgentResult(
                agent_id=self.agent_id,
                agent_name=self.name,
                task=task,
                output="",
                success=False,
                error=str(exc),
            )
