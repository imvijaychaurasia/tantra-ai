"""
Tantra AI — Database models package
Import all models here so SQLAlchemy Base.metadata.create_all picks them up.

Every ORM model MUST be imported here so that:
  1. FastAPI lifespan init_db() creates all tables on startup
  2. Celery worker_ready create_all() creates all tables on startup
  3. The CLI can query any table immediately after container start
"""
from tantra.auth.models import OAuthAccount, User  # noqa: F401
from tantra.db.director import AgentTask, WeeklyPlan  # noqa: F401  — Phase 2 director tables
from tantra.db.social import ContentQueueItem, SocialConnection  # noqa: F401

__all__ = [
    "User",
    "OAuthAccount",
    "SocialConnection",
    "ContentQueueItem",
    "WeeklyPlan",
    "AgentTask",
]
