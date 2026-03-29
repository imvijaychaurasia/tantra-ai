"""
Tantra AI — Skill Installer
तंत्र · High-level install entrypoint used by the CLI and API.

Wraps SkillRegistry with progress reporting and SkillLoader refresh.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from tantra.skills.loader import get_loader
from tantra.skills.registry import SkillRegistry


def install(
    slug: str,
    registry_dir: Optional[Path] = None,
    overwrite: bool = False,
) -> dict:
    """
    Install a skill by slug and refresh the in-process loader.

    Returns the installed skill metadata dict.
    """
    reg = SkillRegistry(registry_dir)
    result = reg.install_from_slug(slug)
    # Refresh the module-level loader so the new skill is immediately available
    get_loader().refresh()
    return result


def install_from_path(
    path: Path,
    registry_dir: Optional[Path] = None,
    overwrite: bool = False,
) -> dict:
    """Install a skill from a local directory path."""
    reg = SkillRegistry(registry_dir)
    result = reg.install_from_path(path, overwrite=overwrite)
    get_loader().refresh()
    return result


def uninstall(name: str, registry_dir: Optional[Path] = None) -> bool:
    """Uninstall a skill by name."""
    reg = SkillRegistry(registry_dir)
    result = reg.uninstall(name)
    if result:
        get_loader().refresh()
    return result
