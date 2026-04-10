"""
Tantra AI — FastAPI application entry point
तंत्र  ·  Local Autonomous Agent Intelligence Stack

Run with:
    uvicorn tantra.main:app --reload --host 0.0.0.0 --port 8000
Or:
    tantra serve --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from tantra.core.config import settings
from tantra.core.database import close_db, init_db

# Import all ORM models so Base.metadata.create_all creates every table
import tantra.db  # noqa: F401  — registers User, OAuthAccount, SocialConnection, ContentQueueItem
import tantra.db.director  # noqa: F401  — registers WeeklyPlan, AgentTask (Phase 2)

# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(settings.log_level)
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    log.info("Tantra AI starting", environment=settings.environment.value, version="0.1.0")
    await init_db()
    log.info("Database ready")
    yield
    await close_db()
    log.info("Tantra AI shut down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Tantra AI",
    description="तंत्र — Local Autonomous Agent Intelligence Stack",
    version="0.1.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


# ---------------------------------------------------------------------------
# Auth routes (fastapi-users)
# ---------------------------------------------------------------------------
def _register_auth_routes() -> None:
    from tantra.auth.manager import auth_backend, fastapi_users
    from tantra.auth.oauth import github_oauth_client, google_oauth_client
    from tantra.auth.schemas import UserCreate, UserRead, UserUpdate

    # JWT login / logout
    app.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix="/auth/jwt",
        tags=["auth"],
    )

    # Registration
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix="/auth",
        tags=["auth"],
    )

    # Password reset
    app.include_router(
        fastapi_users.get_reset_password_router(),
        prefix="/auth",
        tags=["auth"],
    )

    # Email verification
    app.include_router(
        fastapi_users.get_verify_router(UserRead),
        prefix="/auth",
        tags=["auth"],
    )

    # User profile
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/users",
        tags=["users"],
    )

    # Google OAuth (only if configured)
    if google_oauth_client:
        app.include_router(
            fastapi_users.get_oauth_router(
                google_oauth_client,
                auth_backend,
                settings.secret_key.get_secret_value(),
                redirect_url=f"{settings.api_base_url}/auth/google/callback",
                associate_by_email=True,
            ),
            prefix="/auth/google",
            tags=["auth", "oauth"],
        )
        log.info("Google OAuth enabled")

    # GitHub OAuth (only if configured)
    if github_oauth_client:
        app.include_router(
            fastapi_users.get_oauth_router(
                github_oauth_client,
                auth_backend,
                settings.secret_key.get_secret_value(),
                redirect_url=f"{settings.api_base_url}/auth/github/callback",
                associate_by_email=True,
            ),
            prefix="/auth/github",
            tags=["auth", "oauth"],
        )
        log.info("GitHub OAuth enabled")


# ---------------------------------------------------------------------------
# App routes
# ---------------------------------------------------------------------------
def _register_app_routes() -> None:
    from tantra.api.routes import router as api_router
    app.include_router(api_router, prefix="/api/v1")


_register_auth_routes()
_register_app_routes()


# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------
@app.get("/health", tags=["system"], include_in_schema=False)
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "tantra-api", "version": "0.1.0"})


@app.get("/monitor", tags=["system"], include_in_schema=False)
async def monitor_redirect() -> RedirectResponse:
    """Convenience redirect — /monitor → /api/v1/monitor (live dashboard)."""
    return RedirectResponse(url="/api/v1/monitor", status_code=302)


@app.get("/agents", tags=["system"], include_in_schema=False)
async def agents_redirect() -> RedirectResponse:
    """Convenience redirect — /agents → /api/v1/agents (agent config dashboard)."""
    return RedirectResponse(url="/api/v1/agents", status_code=302)


@app.get("/", tags=["system"], include_in_schema=False)
async def root() -> JSONResponse:
    return JSONResponse({
        "service": "Tantra AI",
        "tagline": "तंत्र — Local Autonomous Agent Intelligence Stack",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
        "auth": {
            "login": "/auth/jwt/login",
            "register": "/auth/register",
            "google": "/auth/google/authorize" if settings.google_client_id else None,
            "github": "/auth/github/authorize" if settings.github_client_id else None,
        },
    })
