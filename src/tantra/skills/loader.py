"""
Tantra AI — Skill Loader
तंत्र · Reads SKILL.md files, applies load-time gates, builds the skill index.

SKILL.md frontmatter fields (AgentSkills spec):
  name:         string  — unique slug (kebab-case)
  version:      string  — semver (default: "1.0.0")
  description:  string  — one-line description (used in model prompt)
  author:       string  — author handle
  category:     string  — e.g. social, writing, research, code
  tags:         list    — searchable tags
  platform:     string  — target platform (linkedin, youtube, etc.) or "any"
  enabled:      bool    — false disables the skill globally (default: true)
  user-invocable: bool  — expose as /skill-name slash command (default: true)
  homepage:     string  — URL for docs / registry page
  metadata:     JSON    — gates + Tantra-specific config (single-line JSON object)

metadata.tantra fields:
  requires.env    — list of env vars that must be set
  requires.bins   — list of binaries that must be on PATH
  tier            — model tier to use: frontier/director/manager/worker/fast
  inject_context  — "prompt" | "none" (default: "prompt")
  priority        — integer load priority (lower = injected first)
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Directories searched in precedence order (highest first)
# User can extend via TANTRA_SKILL_DIRS env var (colon-separated paths)
_BUILTIN_SKILLS_ROOT = Path(__file__).parents[4] / "skills"   # <repo>/skills/
_USER_SKILLS_DIR = Path.home() / ".tantra" / "skills"         # ~/.tantra/skills/
_WORKSPACE_SKILLS_DIR = Path.cwd() / "skills"                  # ./skills/


@dataclass
class SkillMeta:
    """Parsed tantra-specific metadata from frontmatter."""
    requires_env: list[str] = field(default_factory=list)
    requires_bins: list[str] = field(default_factory=list)
    tier: str = "worker"
    inject_context: str = "prompt"   # "prompt" | "none"
    priority: int = 50


@dataclass
class Skill:
    """A loaded, gate-passed skill ready for injection."""
    name: str
    version: str
    description: str
    author: str
    category: str
    tags: list[str]
    platform: str
    enabled: bool
    user_invocable: bool
    homepage: str
    meta: SkillMeta
    instructions: str        # Body of SKILL.md (everything after frontmatter)
    path: Path               # Absolute path to the skill directory

    @property
    def slug(self) -> str:
        return self.name

    def to_prompt_xml(self) -> str:
        """Format skill for injection into agent system prompt (compact XML)."""
        return (
            f"<skill>"
            f"<name>{self.name}</name>"
            f"<description>{self.description}</description>"
            f"<location>{self.path}</location>"
            f"</skill>"
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "category": self.category,
            "tags": self.tags,
            "platform": self.platform,
            "enabled": self.enabled,
            "homepage": self.homepage,
            "path": str(self.path),
        }


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _parse_skill_md(skill_dir: Path) -> Optional[Skill]:
    """Parse a SKILL.md file and return a Skill dataclass, or None if invalid."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None

    raw = skill_md.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        logger.warning("SKILL.md missing frontmatter: %s", skill_md)
        return None

    fm_raw, body = m.group(1), m.group(2).strip()

    # Parse YAML-like frontmatter (simple key: value, no nesting except metadata JSON)
    fm: dict = {}
    for line in fm_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            # Handle list values: [a, b, c] or - item
            if val.startswith("[") and val.endswith("]"):
                val = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            elif val.lower() in ("true", "yes"):
                val = True
            elif val.lower() in ("false", "no"):
                val = False
            fm[key] = val

    name = fm.get("name", "")
    if not name:
        logger.warning("Skill missing 'name' in frontmatter: %s", skill_md)
        return None

    # Parse metadata JSON (gates + Tantra config)
    meta = SkillMeta()
    raw_meta = fm.get("metadata", "{}")
    if isinstance(raw_meta, str):
        try:
            meta_dict = json.loads(raw_meta)
            tantra = meta_dict.get("tantra", meta_dict.get("openclaw", {}))
            requires = tantra.get("requires", {})
            meta.requires_env = requires.get("env", [])
            meta.requires_bins = requires.get("bins", [])
            meta.tier = tantra.get("tier", "worker")
            meta.inject_context = tantra.get("inject_context", "prompt")
            meta.priority = int(tantra.get("priority", 50))
        except json.JSONDecodeError:
            pass

    return Skill(
        name=name,
        version=str(fm.get("version", "1.0.0")),
        description=str(fm.get("description", "")),
        author=str(fm.get("author", "")),
        category=str(fm.get("category", "general")),
        tags=fm.get("tags", []) if isinstance(fm.get("tags"), list) else [],
        platform=str(fm.get("platform", "any")),
        enabled=bool(fm.get("enabled", True)),
        user_invocable=bool(fm.get("user-invocable", True)),
        homepage=str(fm.get("homepage", "")),
        meta=meta,
        instructions=body,
        path=skill_dir,
    )


def _check_gates(skill: Skill) -> tuple[bool, str]:
    """
    Apply load-time gates. Returns (eligible, reason).
    A gated-out skill is silently excluded from the prompt.
    """
    if not skill.enabled:
        return False, "disabled in frontmatter"

    for env_var in skill.meta.requires_env:
        if not os.environ.get(env_var):
            return False, f"missing env var: {env_var}"

    for bin_name in skill.meta.requires_bins:
        import shutil
        if not shutil.which(bin_name):
            return False, f"missing binary: {bin_name}"

    return True, "ok"


class SkillLoader:
    """
    Loads skills from multiple directories in precedence order.
    Thread-safe after initial load; call refresh() to reload.

    Precedence (highest wins on name conflict):
      1. workspace ./skills/
      2. ~/.tantra/skills/
      3. <repo>/skills/  (built-in)
      4. Extra dirs from TANTRA_SKILL_DIRS env var
    """

    def __init__(self, extra_dirs: Optional[list[Path]] = None):
        self._skills: dict[str, Skill] = {}
        self._extra_dirs: list[Path] = extra_dirs or []
        self._loaded = False

    def load(self, force: bool = False) -> "SkillLoader":
        """Load all skills. Idempotent unless force=True."""
        if self._loaded and not force:
            return self

        # Build search path (lowest precedence first, so higher-precedence dirs overwrite)
        search_dirs: list[Path] = []

        # Extra dirs from env (lowest precedence)
        for p in os.environ.get("TANTRA_SKILL_DIRS", "").split(":"):
            if p:
                search_dirs.append(Path(p))
        search_dirs.extend(self._extra_dirs)

        # Built-in skills
        if _BUILTIN_SKILLS_ROOT.exists():
            search_dirs.append(_BUILTIN_SKILLS_ROOT)

        # User-installed skills
        if _USER_SKILLS_DIR.exists():
            search_dirs.append(_USER_SKILLS_DIR)

        # Workspace skills (highest precedence)
        if _WORKSPACE_SKILLS_DIR.exists() and _WORKSPACE_SKILLS_DIR != _BUILTIN_SKILLS_ROOT:
            search_dirs.append(_WORKSPACE_SKILLS_DIR)

        loaded: dict[str, Skill] = {}
        for skills_dir in search_dirs:
            if not skills_dir.is_dir():
                continue
            for item in sorted(skills_dir.iterdir()):
                if not item.is_dir():
                    continue
                skill = _parse_skill_md(item)
                if skill is None:
                    continue
                eligible, reason = _check_gates(skill)
                if eligible:
                    loaded[skill.name] = skill
                    logger.debug("Loaded skill: %s (v%s) from %s", skill.name, skill.version, item)
                else:
                    logger.debug("Skipped skill %s: %s", skill.name, reason)

        self._skills = loaded
        self._loaded = True
        logger.info("SkillLoader: %d skills loaded", len(self._skills))
        return self

    def refresh(self) -> "SkillLoader":
        """Reload all skill directories (hot reload)."""
        return self.load(force=True)

    def get(self, name: str) -> Optional[Skill]:
        if not self._loaded:
            self.load()
        return self._skills.get(name)

    def list(self, category: Optional[str] = None, platform: Optional[str] = None) -> list[Skill]:
        if not self._loaded:
            self.load()
        skills = list(self._skills.values())
        if category:
            skills = [s for s in skills if s.category == category]
        if platform:
            skills = [s for s in skills if s.platform in (platform, "any")]
        return sorted(skills, key=lambda s: s.meta.priority)

    def build_system_prompt_block(
        self,
        category: Optional[str] = None,
        platform: Optional[str] = None,
        include_instructions: bool = True,
    ) -> str:
        """
        Build the XML skills block for injection into an agent system prompt.
        Compatible with the AgentSkills/OpenClaw formatSkillsForPrompt format.
        """
        skills = self.list(category=category, platform=platform)
        if not skills:
            return ""

        parts = ["<skills>"]
        for skill in skills:
            if skill.meta.inject_context == "none":
                continue
            parts.append(f"  <skill name=\"{skill.name}\" version=\"{skill.version}\">")
            parts.append(f"    <description>{skill.description}</description>")
            if include_instructions and skill.instructions:
                parts.append(f"    <instructions><![CDATA[{skill.instructions}]]></instructions>")
            parts.append("  </skill>")
        parts.append("</skills>")
        return "\n".join(parts)


# Module-level singleton for convenience
_default_loader: Optional[SkillLoader] = None


def get_loader() -> SkillLoader:
    """Get the module-level singleton SkillLoader (lazy-loaded)."""
    global _default_loader
    if _default_loader is None:
        _default_loader = SkillLoader()
    if not _default_loader._loaded:
        _default_loader.load()
    return _default_loader
