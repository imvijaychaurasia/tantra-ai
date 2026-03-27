"""
Tantra AI — Pydantic schemas for auth requests / responses
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi_users import schemas
from pydantic import EmailStr, Field


class UserRead(schemas.BaseUser[uuid.UUID]):
    """Schema returned for GET /users/me and user listings."""
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    timezone: str = "UTC"
    bio: Optional[str] = None
    role: str = "member"
    plan: str = "free"


class UserCreate(schemas.BaseUserCreate):
    """Schema for POST /auth/register"""
    full_name: Optional[str] = Field(None, max_length=200)
    timezone: str = Field("UTC", max_length=50)


class UserUpdate(schemas.BaseUserUpdate):
    """Schema for PATCH /users/me"""
    full_name: Optional[str] = Field(None, max_length=200)
    avatar_url: Optional[str] = Field(None, max_length=500)
    timezone: Optional[str] = Field(None, max_length=50)
    bio: Optional[str] = Field(None, max_length=1000)
