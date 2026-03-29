"""
social-linkedin plugin entry point.
Called by PluginLoader.register_all() at application startup.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register(api: Any = None) -> None:
    """
    Register the social-linkedin plugin capabilities with the Tantra API.

    Currently: validates env vars and logs capabilities.
    Future: dynamically register routes and tasks via `api` object.
    """
    import os
    from pathlib import Path

    missing = []
    for var in ["ZERNIO_API_KEY"]:
        if not os.environ.get(var):
            missing.append(var)

    if missing:
        logger.warning(
            "social-linkedin plugin: missing env vars %s — some features will be disabled",
            missing,
        )
    else:
        logger.info("social-linkedin plugin: all required env vars present")

    # Register bundled skills with the skill loader
    skills_dir = Path(__file__).parents[2] / "skills"
    if skills_dir.exists():
        try:
            from tantra.skills.loader import get_loader
            loader = get_loader()
            loader.refresh()
            logger.info("social-linkedin plugin: skills reloaded")
        except Exception as exc:
            logger.warning("social-linkedin plugin: skill reload failed: %s", exc)

    logger.info(
        "social-linkedin plugin registered: 4 celery tasks, 2 API route groups, "
        "1 tool (ZernioClient), 5 skills"
    )
