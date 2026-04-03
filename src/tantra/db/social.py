"""
Tantra AI — Social platform DB models

Tables:
  social_connections  — Encrypted OAuth tokens per user per platform
  content_queue       — AI-drafted posts pending human approval + publishing

Lifecycle for content:
  draft → [n8n webhook sent] → approved | rejected → published | failed
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tantra.core.database import Base


# ---------------------------------------------------------------------------
# SocialConnection — OAuth token store per user per platform
# ---------------------------------------------------------------------------

class SocialConnection(Base):
    """
    Stores Fernet-encrypted OAuth tokens for social platforms.

    One row per (user_id, platform) pair.
    Tokens are encrypted at rest using the app's secret_key via Fernet.

    Supported platforms: linkedin | youtube
    """
    __tablename__ = "social_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # linkedin | youtube

    # Encrypted token fields (Fernet-encrypted, stored as Text)
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # OpenID Connect / profile identifiers
    profile_sub: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # OpenID 'sub' claim — unique per platform user
    profile_urn: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )  # urn:li:person:{sub}  (LinkedIn author URN)
    profile_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    profile_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)

    # Token lifecycle
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scopes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<SocialConnection platform={self.platform!r} "
            f"user={self.user_id} sub={self.profile_sub!r}>"
        )


# ---------------------------------------------------------------------------
# ContentQueueItem — AI-drafted content awaiting approval + publishing
# ---------------------------------------------------------------------------

class ContentQueueItem(Base):
    """
    Content queue row: one AI-drafted post per row.

    Status machine:
      draft       — created by research crew, not yet reviewed
      approved    — human approved via n8n, ready to publish
      rejected    — human rejected via n8n, will not publish
      published   — successfully posted to platform
      failed      — publish attempted but LinkedIn API returned error
    """
    __tablename__ = "content_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    platform: Mapped[str] = mapped_column(
        String(30), nullable=False, default="linkedin"
    )

    # Content
    draft_text: Mapped[str] = mapped_column(Text, nullable=False)
    research_context: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # Raw research brief used to generate the draft
    hashtags: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )  # Comma-separated hashtags extracted from draft

    # Status
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", index=True
    )

    # n8n tracking
    n8n_execution_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # n8n execution ID for the approval workflow

    # Rejection reason (from n8n / human reviewer)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Publishing result
    post_urn: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )  # LinkedIn post URN after successful publish
    publish_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )  # Optional: publish at this time instead of next beat run

    def __repr__(self) -> str:
        return (
            f"<ContentQueueItem id={self.id} status={self.status!r} "
            f"platform={self.platform!r}>"
        )


# ---------------------------------------------------------------------------
# YouTubeVideo — Phase 3 YouTube content pipeline
# ---------------------------------------------------------------------------

class YouTubeVideo(Base):
    """
    One row per YouTube video flowing through the production pipeline.

    Status machine:
      scripted   — YouTubeCrew generated the full scene script; awaiting human review
      approved   — Human approved script via n8n; production queued
      producing  — tantra-media is generating TTS + video/images + thumbnail
      produced   — All media assets generated; ready for upload
      uploading  — YouTube Data API upload in progress
      live       — Video is live on YouTube (youtube_video_id is set)
      rejected   — Human rejected the script via n8n
      failed     — Production or upload encountered an unrecoverable error

    The `script` JSON column is the single source of truth for downstream steps:
      - TTS: scene.narration per scene
      - Video/image gen: scene.visual_prompt per scene
      - Assembly: scene.duration_seconds for clip timing
      - Thumbnail: script.thumbnail_prompt
      - YouTube description: scenes[].b_roll_description summary

    Script JSON schema (YouTubeScript):
      {
        "duration_target_seconds": 480,
        "hook": "What if your AI system could plan and publish content while you sleep?",
        "scenes": [
          {
            "id": 1,
            "type": "hook",                  # hook | content | cta | outro
            "duration_seconds": 20,
            "narration": "30 days ago I started building Tantra AI...",
            "visual_prompt": "Developer at terminal, dark room, green text on screen",
            "b_roll_description": "Terminal showing tantra director chat streaming",
            "on_screen_text": "Day 1 — Just a Celery task"            # nullable
          }
        ],
        "call_to_action": "Subscribe — I ship weekly updates on what breaks and what works",
        "thumbnail_concept": "Split screen: empty terminal (Day 1) vs Director running (Day 30)",
        "thumbnail_prompt": "Futuristic terminal UI, two panes, dark neon aesthetic"
      }
    """
    __tablename__ = "youtube_videos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Links to planning layer (both nullable — video may be ad-hoc)
    agent_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("weekly_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Script (set by YouTubeCrew)
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # SEO description
    script: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)       # YouTubeScript JSON
    thumbnail_concept: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)         # list[str]
    topic_hint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)    # Director's original brief

    # Status state machine
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="scripted", index=True
    )  # scripted|approved|producing|produced|uploading|live|rejected|failed

    # Production file paths (populated by produce_youtube_video)
    audio_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)    # data/media/audio/{id}.mp3
    video_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)    # data/media/output/{id}.mp4
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # data/media/images/{id}_thumb.png

    # Upload result (populated by upload_youtube_video)
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    youtube_url: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # n8n tracking
    n8n_execution_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Production error (if status == failed)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Analytics (updated by youtube_analytics_pull)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analytics_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    produced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<YouTubeVideo id={str(self.id)[:8]} "
            f"title={self.title!r} "
            f"status={self.status!r}>"
        )
