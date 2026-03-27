"""
Tantra AI — Base agent abstraction
All agents (Leader and Worker) inherit from TantraAgent.

Design:
  - Each agent has its own memory namespace (mem0 + Qdrant)
  - Leader agents can READ subordinate memories
  - All agents communicate through LiteLLM model aliases
  - Agents are composable via CrewAI + LangGraph
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from tantra.core.config import ModelTier, settings
from tantra.core.llm import build_system_prompt, chat

logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    """A single message in an agent's conversation history."""
    role: str           # "system" | "user" | "assistant" | "tool"
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_openai(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class AgentResult:
    """Structured result returned by any agent after execution."""
    agent_id: str
    agent_name: str
    task: str
    output: str
    success: bool
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


class TantraAgent(ABC):
    """
    Abstract base for all Tantra agents.

    Subclass this to create specialised agents:
        class ResearchWorker(TantraAgent):
            async def execute(self, task): ...
    """

    def __init__(
        self,
        name: str,
        role: str,
        goal: str,
        model_tier: ModelTier = ModelTier.worker,
        backstory: Optional[str] = None,
        agent_id: Optional[str] = None,
        memory_namespace: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        self.agent_id = agent_id or str(uuid.uuid4())
        self.name = name
        self.role = role
        self.goal = goal
        self.model_tier = model_tier
        self.backstory = backstory or f"A specialised {role} agent in the Tantra system."
        self.memory_namespace = memory_namespace or f"agent:{self.name.lower().replace(' ', '_')}"
        self.verbose = verbose

        # Short-term context window
        self._history: list[AgentMessage] = []

        logger.info(
            "Agent initialised",
            extra={"agent": self.name, "model": self.model_tier.value, "id": self.agent_id},
        )

    # ------------------------------------------------------------------
    # Core interface — must implement in subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute(self, task: str, context: Optional[str] = None) -> AgentResult:
        """
        Run a task and return a structured result.

        Args:
            task:    Natural language task description.
            context: Optional extra context (injected memory, prior results).

        Returns:
            AgentResult with output and success status.
        """
        ...

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    async def think(
        self,
        task: str,
        context: Optional[str] = None,
        temperature: float = 0.7,
    ) -> str:
        """
        One-shot LLM call with system prompt + optional context.
        Maintains rolling conversation history (last N messages).
        """
        system = build_system_prompt(
            role=f"{self.role} named {self.name}",
            context=context,
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]

        # Add rolling history (last `settings.agent_memory_window` messages)
        window = self._history[-settings.agent_memory_window:]
        messages.extend(m.to_openai() for m in window)
        messages.append({"role": "user", "content": task})

        response = await chat(
            messages=messages,
            model=self.model_tier,
            temperature=temperature,
        )
        result = str(response)

        # Record in history
        self._history.append(AgentMessage(role="user", content=task))
        self._history.append(AgentMessage(role="assistant", content=result))

        return result

    def clear_history(self) -> None:
        """Clear short-term conversation history."""
        self._history.clear()

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} model={self.model_tier.value!r}>"
