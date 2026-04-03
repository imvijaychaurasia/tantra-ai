"""
Tantra AI — LLM gateway
Single entry-point for all model calls.
Routes through the LiteLLM proxy so all tiers (frontier/director/manager/worker)
can be swapped without changing agent code.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional

from openai import AsyncOpenAI, OpenAI

from tantra.core.config import ModelTier, settings

logger = logging.getLogger(__name__)


def _make_client(async_mode: bool = True) -> AsyncOpenAI | OpenAI:
    """Build an OpenAI-compatible client pointed at the LiteLLM proxy."""
    kwargs: dict[str, Any] = dict(
        base_url=f"{settings.litellm_base_url}/v1",
        api_key=settings.litellm_key,
        timeout=600,
        max_retries=3,
    )
    return AsyncOpenAI(**kwargs) if async_mode else OpenAI(**kwargs)


# Module-level clients (reused across calls)
_async_client: AsyncOpenAI | None = None
_sync_client: OpenAI | None = None


def get_async_client() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        _async_client = _make_client(async_mode=True)  # type: ignore[assignment]
    return _async_client  # type: ignore[return-value]


def get_sync_client() -> OpenAI:
    global _sync_client
    if _sync_client is None:
        _sync_client = _make_client(async_mode=False)  # type: ignore[assignment]
    return _sync_client  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Convenience wrappers used by agents
# ---------------------------------------------------------------------------

async def chat(
    messages: list[dict[str, str]],
    model: ModelTier | str = ModelTier.director,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    stream: bool = False,
    **kwargs: Any,
) -> str | AsyncIterator[str]:
    """
    Single async function for all LLM completions.

    Args:
        messages:    OpenAI-format messages list.
        model:       ModelTier alias (frontier / director / manager / worker / ...).
        temperature: Sampling temperature.
        max_tokens:  Max output tokens.
        stream:      If True, returns an async generator of delta strings.

    Returns:
        Full response string (stream=False) or async generator (stream=True).
    """
    client = get_async_client()
    model_name = model.value if isinstance(model, ModelTier) else model

    logger.debug("LLM call", extra={"model": model_name, "messages": len(messages)})

    if stream:
        return _stream_chat(client, messages, model_name, temperature, max_tokens, **kwargs)

    response = await client.chat.completions.create(
        model=model_name,
        messages=messages,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )
    return response.choices[0].message.content or ""


async def _stream_chat(
    client: AsyncOpenAI,
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
    **kwargs: Any,
) -> AsyncIterator[str]:
    """
    Yield token deltas from a streaming completion.

    Uses .create(stream=True) which returns AsyncStream[ChatCompletionChunk]
    where each chunk has the standard .choices[0].delta.content structure.

    NOTE: .stream() (the newer typed-events API) yields ChunkEvent objects
    that do NOT have .choices — that's why we use .create(stream=True) here.
    """
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        **kwargs,
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


async def embed(
    text: str | list[str],
    model: str = ModelTier.embedder.value,
) -> list[list[float]]:
    """
    Generate embeddings through the LiteLLM proxy.

    Returns a list of embedding vectors (one per input text).
    """
    client = get_async_client()
    inputs = [text] if isinstance(text, str) else text
    response = await client.embeddings.create(model=model, input=inputs)
    return [item.embedding for item in response.data]


def build_system_prompt(role: str, context: Optional[str] = None) -> str:
    """
    Build a structured system prompt for an agent role.
    Optionally injects memory/context from prior interactions.
    """
    base = (
        f"You are {role} within Tantra AI — a hierarchical autonomous agent system.\n"
        "Your job is to reason clearly, plan step-by-step, and produce actionable output.\n"
        "Always think before acting. Break complex tasks into smaller sub-tasks.\n"
        "Be concise but complete. Prefer structured output (JSON / markdown).\n"
    )
    if context:
        base += f"\n## Relevant Context from Memory\n{context}\n"
    return base
