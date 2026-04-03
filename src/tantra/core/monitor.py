"""
Tantra AI — Live Monitor

Real-time observability for every LLM call and agent step across all crews.

Event Bus:
  Redis pub/sub channel: tantra:monitor:live
  Events are published here by:
    - TantraLiteLLMCallback  → every LLM API call (all models, all crews)
    - make_crew_step_callback → every CrewAI agent step / tool use
    - MonitorEmitter.task_*  → Celery task lifecycle events

Consumers:
  - FastAPI /ws/monitor WebSocket → browser HTML dashboard at /monitor
  - `tantra monitor` CLI command  → terminal pretty-print

All emit operations are non-blocking and non-fatal — a Redis outage never
breaks a crew or task.  Errors are logged at DEBUG level only.

Event schema (JSON, all events share these fields):
  {
    "ts":    "<ISO-8601 UTC timestamp>",
    "event": "<event_type constant>",
    ... event-specific fields ...
  }
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis channel
# ---------------------------------------------------------------------------

MONITOR_CHANNEL = "tantra:monitor:live"


# ---------------------------------------------------------------------------
# Event type constants
# ---------------------------------------------------------------------------

class Event:
    # LLM call lifecycle (fired by TantraLiteLLMCallback)
    LLM_START   = "llm_start"
    LLM_END     = "llm_end"
    LLM_FAILED  = "llm_failed"

    # CrewAI agent actions (fired by make_crew_step_callback)
    AGENT_STEP  = "agent_step"    # agent thinking / deciding
    TOOL_CALL   = "tool_call"     # agent calling a tool

    # Celery task lifecycle (fired by MonitorEmitter.task_*)
    TASK_START  = "task_start"
    TASK_END    = "task_end"
    TASK_FAILED = "task_failed"

    # General system messages
    SYSTEM      = "system"


# ---------------------------------------------------------------------------
# MonitorEmitter
# ---------------------------------------------------------------------------

class MonitorEmitter:
    """
    Publishes structured events to Redis pub/sub.
    All methods are class-level — no instantiation needed.
    Never raises; all errors are logged at DEBUG level.
    """

    _redis = None  # Lazy singleton connection

    @classmethod
    def _get_redis(cls):
        """Return (or create) a shared Redis connection. Returns None on failure."""
        if cls._redis is not None:
            return cls._redis
        try:
            import redis as redis_lib
            from tantra.core.config import settings
            cls._redis = redis_lib.from_url(
                settings.celery_broker_url, decode_responses=True,
                socket_connect_timeout=1, socket_timeout=1,
            )
            return cls._redis
        except Exception as exc:
            logger.debug("MonitorEmitter: Redis init failed (%s)", exc)
            return None

    @classmethod
    def emit(cls, event_type: str, data: dict) -> None:
        """Publish one event to the monitor channel. Non-blocking, non-fatal."""
        try:
            r = cls._get_redis()
            if r is None:
                return
            payload = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event_type,
                **data,
            }
            r.publish(MONITOR_CHANNEL, json.dumps(payload))
        except Exception as exc:
            logger.debug("MonitorEmitter.emit failed (non-fatal): %s", exc)
            cls._redis = None  # Reset so next call tries reconnect

    # ── Convenience wrappers ──────────────────────────────────────────────────

    @classmethod
    def system(cls, message: str, **kwargs) -> None:
        cls.emit(Event.SYSTEM, {"message": message, **kwargs})

    @classmethod
    def task_start(cls, task_type: str, task_id: str, **kwargs) -> None:
        cls.emit(Event.TASK_START, {
            "task_type": task_type, "task_id": task_id, **kwargs
        })

    @classmethod
    def task_end(cls, task_type: str, task_id: str, **kwargs) -> None:
        cls.emit(Event.TASK_END, {
            "task_type": task_type, "task_id": task_id, **kwargs
        })

    @classmethod
    def task_failed(cls, task_type: str, task_id: str, error: str, **kwargs) -> None:
        cls.emit(Event.TASK_FAILED, {
            "task_type": task_type, "task_id": task_id,
            "error": error[:500], **kwargs
        })


# ---------------------------------------------------------------------------
# LiteLLM callback — captures every LLM call across ALL crews
# ---------------------------------------------------------------------------

class TantraLiteLLMCallback:
    """
    LiteLLM-compatible callback object.

    LiteLLM calls these methods synchronously around every completion call,
    so we stay fast — just fire-and-forget to Redis.

    Registration (once, in celery_app.py worker_ready):
        import litellm
        litellm.callbacks.append(TantraLiteLLMCallback())
    """

    def log_pre_api_call(self, model: str, messages: list, kwargs: dict) -> None:
        """Called just before the LLM API request is sent."""
        metadata = kwargs.get("metadata") or {}
        MonitorEmitter.emit(Event.LLM_START, {
            "model": model or "unknown",
            "messages_count": len(messages) if messages else 0,
            "agent": str(metadata.get("agent_name", metadata.get("agent", "unknown")))[:80],
            "task_id": str(metadata.get("task_id", "")),
            "crew": str(metadata.get("crew_name", ""))[:60],
        })

    def log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        """Called after a successful LLM response."""
        try:
            latency_ms = int((end_time - start_time).total_seconds() * 1000)
        except Exception:
            latency_ms = -1

        usage: dict = {}
        try:
            u = response_obj.usage
            usage = {
                "prompt_tokens":     u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens":      u.total_tokens,
            }
        except Exception:
            pass

        metadata = kwargs.get("metadata") or {}
        MonitorEmitter.emit(Event.LLM_END, {
            "model":      kwargs.get("model", "unknown"),
            "latency_ms": latency_ms,
            "agent":      str(metadata.get("agent_name", metadata.get("agent", "unknown")))[:80],
            "task_id":    str(metadata.get("task_id", "")),
            "crew":       str(metadata.get("crew_name", ""))[:60],
            **usage,
        })

    def log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        """Called after an LLM API failure."""
        metadata = kwargs.get("metadata") or {}
        error_str = ""
        try:
            error_str = str(response_obj)[:300] if response_obj else "unknown error"
        except Exception:
            pass
        MonitorEmitter.emit(Event.LLM_FAILED, {
            "model":   kwargs.get("model", "unknown"),
            "error":   error_str,
            "agent":   str(metadata.get("agent_name", metadata.get("agent", "unknown")))[:80],
            "task_id": str(metadata.get("task_id", "")),
            "crew":    str(metadata.get("crew_name", ""))[:60],
        })

    # LiteLLM also calls these (no-ops here — we use the above)
    def log_stream_event(self, *args, **kwargs) -> None:
        pass

    def log_post_api_call(self, *args, **kwargs) -> None:
        pass


# ---------------------------------------------------------------------------
# CrewAI step callback factory
# ---------------------------------------------------------------------------

def make_crew_step_callback(crew_name: str, agent_task_id: str = ""):
    """
    Returns a CrewAI step_callback function that publishes agent step events.

    CrewAI calls this after every agent action (thinking step or tool invocation).
    The returned function is designed to be resilient — any attribute access
    on the action object is guarded.

    Usage:
        crew = Crew(
            ...,
            step_callback=make_crew_step_callback("YouTubeCrew", task_id),
        )
    """
    def _callback(agent_action: Any) -> None:
        try:
            tool       = str(getattr(agent_action, "tool",       "") or "")
            tool_input = str(getattr(agent_action, "tool_input", "") or "")[:300]
            log_text   = str(getattr(agent_action, "log",        "") or "")[:400]

            # Try to extract agent name (CrewAI stores it differently across versions)
            agent_name = (
                getattr(agent_action, "agent", None)
                or getattr(agent_action, "agent_name", None)
                or "unknown"
            )
            agent_name = str(agent_name)[:80]

            if tool:
                MonitorEmitter.emit(Event.TOOL_CALL, {
                    "crew":          crew_name,
                    "agent":         agent_name,
                    "tool":          tool,
                    "input_preview": tool_input,
                    "task_id":       agent_task_id,
                })
            else:
                MonitorEmitter.emit(Event.AGENT_STEP, {
                    "crew":           crew_name,
                    "agent":          agent_name,
                    "thought_preview": log_text,
                    "task_id":        agent_task_id,
                })
        except Exception as exc:
            logger.debug("crew step_callback error (non-fatal): %s", exc)

    return _callback
