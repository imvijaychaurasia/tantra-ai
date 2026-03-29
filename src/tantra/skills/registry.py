"""
Tantra AI — Skill Registry
तंत्र · Local index + remote TantraHub install/publish

The registry maintains a JSON index at ~/.tantra/skills/registry.json.
Skills can be installed from:
  - TantraHub (https://hub.tantra.ai) — future
  - GitHub (https://github.com/user/repo/tree/main/skills/slug)
  - Local path (file:///path/to/skill-dir)
  - ClawHub (clawhub.ai) — compatible format

Install flow:
  1. Fetch SKILL.md from source URL
  2. Verify frontmatter (name, description required)
  3. Copy to ~/.tantra/skills/<name>/
  4. Update registry.json index
  5. SkillLoader.refresh() to pick up new skill
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_REGISTRY_DIR = Path.home() / ".tantra" / "skills"
_REGISTRY_INDEX = _REGISTRY_DIR / "registry.json"
_BUILTIN_SKILLS_ROOT = Path(__file__).parents[4] / "skills"


class SkillRegistry:
    """Manages the local skill registry index and install/uninstall operations."""

    def __init__(self, registry_dir: Optional[Path] = None):
        self._dir = registry_dir or _REGISTRY_DIR
        self._index_path = self._dir / "registry.json"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _read_index(self) -> dict:
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text())
            except Exception:
                pass
        return {"version": "1", "skills": {}}

    def _write_index(self, index: dict) -> None:
        self._index_path.write_text(json.dumps(index, indent=2, default=str))

    def list_installed(self) -> list[dict]:
        """Return all entries from the registry index."""
        index = self._read_index()
        return list(index.get("skills", {}).values())

    def list_builtin(self) -> list[dict]:
        """Return built-in skills shipped with the repo."""
        from tantra.skills.loader import _parse_skill_md
        skills = []
        if _BUILTIN_SKILLS_ROOT.exists():
            for item in sorted(_BUILTIN_SKILLS_ROOT.iterdir()):
                if item.is_dir():
                    skill = _parse_skill_md(item)
                    if skill:
                        d = skill.to_dict()
                        d["source"] = "builtin"
                        skills.append(d)
        return skills

    def install_from_path(self, source: Path, overwrite: bool = False) -> dict:
        """
        Install a skill from a local directory.
        Returns the installed skill's metadata dict.
        """
        from tantra.skills.loader import _parse_skill_md

        skill = _parse_skill_md(source)
        if skill is None:
            raise ValueError(f"No valid SKILL.md found in {source}")

        dest = self._dir / skill.name
        if dest.exists() and not overwrite:
            raise FileExistsError(
                f"Skill '{skill.name}' already installed at {dest}. "
                "Use overwrite=True to replace."
            )

        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)

        index = self._read_index()
        index["skills"][skill.name] = {
            **skill.to_dict(),
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source),
        }
        self._write_index(index)
        logger.info("Installed skill '%s' v%s → %s", skill.name, skill.version, dest)
        return index["skills"][skill.name]

    def install_from_github(self, repo: str, skill_path: str = "", branch: str = "main") -> dict:
        """
        Install a skill from a GitHub repo.

        Args:
            repo: 'user/repo' format
            skill_path: path within repo to the skill directory (default: skills/<name>)
            branch: branch or tag (default: main)
        """
        import urllib.request
        import zipfile

        # Download repo as zip
        zip_url = f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"
        logger.info("Downloading %s ...", zip_url)

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "repo.zip"
            urllib.request.urlretrieve(zip_url, zip_path)

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp)

            repo_name = repo.split("/")[-1]
            extracted = Path(tmp) / f"{repo_name}-{branch}"

            # Find the skill directory
            if skill_path:
                skill_dir = extracted / skill_path
            else:
                # Auto-discover: look for any directory with SKILL.md
                candidates = list(extracted.rglob("SKILL.md"))
                if not candidates:
                    raise ValueError(f"No SKILL.md found in {repo}")
                skill_dir = candidates[0].parent

            return self.install_from_path(skill_dir, overwrite=True)

    def install_from_slug(self, slug: str) -> dict:
        """
        Install a skill by slug. Resolution order:
        1. Built-in skill (ships with Tantra) — just register it
        2. TantraHub registry (future: https://hub.tantra.ai/skills/{slug})
        3. ClawHub compatible (https://clawhub.ai/{slug}) — name format user/slug
        4. GitHub shorthand (user/repo or user/repo:path)
        """
        # Check if it's a built-in skill
        builtin_path = _BUILTIN_SKILLS_ROOT / slug
        if builtin_path.exists():
            return self.install_from_path(builtin_path, overwrite=True)

        # Check GitHub shorthand: user/repo or user/repo:path
        if "/" in slug:
            parts = slug.split(":")
            repo = parts[0]
            skill_path = parts[1] if len(parts) > 1 else ""
            return self.install_from_github(repo, skill_path)

        raise ValueError(
            f"Cannot resolve skill slug '{slug}'. "
            f"Use: built-in name, 'user/repo', or 'user/repo:path/to/skill'"
        )

    def uninstall(self, name: str) -> bool:
        """Remove a user-installed skill. Built-in skills cannot be uninstalled."""
        dest = self._dir / name
        if not dest.exists():
            logger.warning("Skill '%s' not found in user skills dir", name)
            return False

        shutil.rmtree(dest)
        index = self._read_index()
        index["skills"].pop(name, None)
        self._write_index(index)
        logger.info("Uninstalled skill '%s'", name)
        return True

    def update(self, name: str) -> dict:
        """Re-install a skill from its original source."""
        index = self._read_index()
        entry = index.get("skills", {}).get(name)
        if not entry:
            raise ValueError(f"Skill '{name}' not in registry. Install it first.")
        source = entry.get("source", "")
        if source.startswith("http"):
            raise NotImplementedError("Remote URL update not yet implemented")
        return self.install_from_path(Path(source), overwrite=True)
