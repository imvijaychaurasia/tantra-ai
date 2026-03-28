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

from sqlalchemy import DateTime, ForeignKey, String, Text
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
