"""
Tantra AI — User & OAuth account models
SQLAlchemy models compatible with fastapi-users.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi_users.db import SQLAlchemyBaseOAuthAccountTableUUID, SQLAlchemyBaseUserTableUUID
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tantra.core.database import Base


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    """
    Linked OAuth accounts (Google, GitHub, LinkedIn, etc.).
    One user can have multiple OAuth accounts.
    """
    __tablename__ = "oauth_accounts"

    # Extra fields beyond fastapi-users defaults
    provider_user_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)


class User(SQLAlchemyBaseUserTableUUID, Base):
    """
    Core user model.
    fastapi-users provides: id, email, hashed_password, is_active,
    is_superuser, is_verified.
    We extend with profile + tenant fields.
    """
    __tablename__ = "user"

    # Profile
    full_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Subscription / role (single-tenant: admin | member)
    role: Mapped[str] = mapped_column(String(20), default="member")  # admin | member
    plan: Mapped[str] = mapped_column(String(20), default="free")    # free | pro | enterprise

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # OAuth accounts (one user → many OAuth providers)
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(
        "OAuthAccount", lazy="selectin"
    )

    # Social platform tokens (encrypted, stored as reference IDs to vault)
    linkedin_token_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    youtube_token_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"
