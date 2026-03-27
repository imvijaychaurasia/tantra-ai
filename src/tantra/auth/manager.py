"""
Tantra AI — UserManager
fastapi-users UserManager handles registration, password reset,
email verification, OAuth linking.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from tantra.auth.models import OAuthAccount, User
from tantra.core.config import settings
from tantra.core.database import get_db_dep

logger = logging.getLogger(__name__)

SECRET = settings.secret_key.get_secret_value()


# ---------------------------------------------------------------------------
# Database adapter
# ---------------------------------------------------------------------------

async def get_user_db(session: AsyncSession = Depends(get_db_dep)):
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


# ---------------------------------------------------------------------------
# UserManager — lifecycle hooks
# ---------------------------------------------------------------------------

class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        logger.info(f"New user registered: {user.email}")
        # TODO: send welcome email via background task

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        logger.info(f"Password reset requested for {user.email}")
        # TODO: send reset email

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        logger.info(f"Email verification requested for {user.email}")
        # TODO: send verification email

    async def on_after_login(
        self,
        user: User,
        request: Optional[Request] = None,
        response=None,
    ):
        from datetime import datetime
        from tantra.core.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            user.last_login_at = datetime.utcnow()
            session.add(user)
            await session.commit()

    async def on_after_oauth_associate(
        self, user: User, oauth_account: OAuthAccount, request: Optional[Request] = None
    ):
        logger.info(f"OAuth account {oauth_account.oauth_name} linked to {user.email}")


async def get_user_manager(user_db=Depends(get_user_db)):
    yield UserManager(user_db)


# ---------------------------------------------------------------------------
# JWT authentication backend
# ---------------------------------------------------------------------------

bearer_transport = BearerTransport(tokenUrl="/auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=SECRET,
        lifetime_seconds=settings.jwt_access_token_expire_minutes * 60,
        algorithm=settings.jwt_algorithm,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)


# ---------------------------------------------------------------------------
# FastAPIUsers instance — the main object used everywhere
# ---------------------------------------------------------------------------

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

# Dependency: get current active user (use in any protected route)
current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
