"""
Tantra AI — Director / Planning DB models

Tables:
  weekly_plans   — The Director's strategic plan for each calendar week
  agent_tasks    — Specific work items assigned to agents by the Director

Design:
  Each week the Director (CAIO) analyses last week's performance and generates
  a WeeklyPlan with goals + a content calendar. The plan is then decomposed into
  AgentTask rows that the existing Phase 1 Celery tasks pick up and execute.

  If no active WeeklyPlan exists, Phase 1 tasks fall back to their built-in
  behaviour — so the Director layer is purely additive and non-breaking.

Status machines:
  WeeklyPlan:  planning → active → completed | cancelled
  AgentTask:   pending  → in_progress → completed | failed | skipped
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tantra.core.database import Base


# ---------------------------------------------------------------------------
# WeeklyPlan — the Director's strategic plan for one ISO week
# ---------------------------------------------------------------------------

class WeeklyPlan(Base):
    """
    One row per ISO week.  The Director creates this on Monday morning,
    then updates it on Friday with the performance review.

    goals (JSON) example:
      {
        "linkedin_posts_target": 3,
        "progress_posts_target": 5,
        "engagement_target": 500,
        "primary_topic": "AI agents and automation",
        "secondary_topics": ["local LLMs", "building in public"],
        "tone": "builder, authentic, data-driven"
      }

    content_calendar (JSON) example:
      [
        {"day": "Monday",   "platform": "linkedin", "type": "research_draft",   "topic": "Autonomous agents replacing SaaS"},
        {"day": "Tuesday",  "platform": "linkedin", "type": "progress_post",    "topic": "Tantra build update"},
        {"day": "Wednesday","platform": "linkedin", "type": "research_draft",   "topic": "Local LLMs on consumer hardware"},
        ...
      ]

    performance_review (JSON) example:
      {
        "posts_published": 4,
        "total_impressions": 1240,
        "avg_engagement_rate": 3.2,
        "top_post_urn": "urn:li:share:xxx",
        "top_post_topic": "Phase 1 complete",
        "lessons": ["Longer posts with data performed better", "Friday posts get less reach"]
      }
    """
    __tablename__ = "weekly_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Week identity
    week_start: Mapped[date] = mapped_column(
        Date, nullable=False, unique=True, index=True
    )  # ISO Monday of this week
    week_number: Mapped[int] = mapped_column(Integer, nullable=False)  # ISO week number
    year: Mapped[int] = mapped_column(Integer, nullable=False)

    # Status
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="planning", index=True
    )  # planning | active | completed | cancelled

    # Strategic content (LLM-generated)
    goals: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    content_calendar: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(
        JSON, nullable=True
    )
    director_analysis: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # Director's strategic reasoning text

    # Performance review (filled on Friday / end-of-week)
    performance_review: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<WeeklyPlan week={self.week_start} "
            f"status={self.status!r} "
            f"tasks_target={self.goals.get('linkedin_posts_target') if self.goals else '?'}>"
        )


# ---------------------------------------------------------------------------
# AgentTask — a specific work item assigned to an agent / crew
# ---------------------------------------------------------------------------

class AgentTask(Base):
    """
    One row per work item created by the Director's weekly plan.

    task_type values:
      research_draft     — run research_and_draft_posts (social crew)
      progress_post      — run post_tantra_progress
      youtube_script     — run youtube analytics / script generation
      analytics_review   — CMO pulls & analyses platform metrics
      engagement_scan    — linkedin_engage_feed
      director_review    — end-of-week Director performance review

    assigned_to values:
      director | cmo | cto | social_crew | content_writer | publisher | analyst

    context (JSON) — extra parameters forwarded to the executing task:
      {
        "topic_hint": "AI agents replacing SaaS",
        "tone_override": "more technical this time",
        "platform": "linkedin",
        "angle_index": 3          # for post_tantra_progress
      }

    result (JSON) — set by the executing task on completion:
      {
        "post_urns": ["urn:li:share:xxx"],
        "draft_ids": ["uuid1", "uuid2"],
        "celery_task_id": "abc-123"
      }
    """
    __tablename__ = "agent_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("weekly_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )  # NULL = ad-hoc task not tied to a weekly plan

    # Task identity
    task_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    assigned_to: Mapped[str] = mapped_column(String(50), nullable=False)
    priority: Mapped[str] = mapped_column(
        String(10), nullable=False, default="medium"
    )  # high | medium | low

    # Status
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )  # pending | in_progress | completed | failed | skipped

    # Payload
    instructions: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # Director's natural-language instructions
    context: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )  # Structured parameters for the executing Celery task

    # Result
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Scheduling + tracking
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True
    )  # When to run this task
    celery_task_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # Celery AsyncResult ID

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AgentTask type={self.task_type!r} "
            f"assigned_to={self.assigned_to!r} "
            f"status={self.status!r}>"
        )
