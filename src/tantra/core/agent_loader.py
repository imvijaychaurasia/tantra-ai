"""
Tantra AI — Agent Config Loader
Reads agent config files (soul.md, skills.md, policy.md, memory.md, tools.json,
reflection.md, learning.md, feedback.md, evaluation.md) from the `agents/` directory.

HOT-RELOAD DESIGN:
  Files are read fresh on EVERY call to build_system_prompt().
  There is NO Python-level caching. The OS page cache handles performance.
  Because /app is bind-mounted into all Docker containers, any host file edit
  is immediately visible inside the container. Edit → next LLM turn picks it up.
  Zero restarts required.

VERSION HISTORY:
  Every write (dashboard edit, agent auto-write) stores a versioned copy in:
    agents/<agent-path>/.history/<filename>/
      CHANGELOG.json           — index: [{ts, comment, version_file, size}, ...]
      20260409_143000_<slug>.md — full file content at that point
  The live file is always agents/<agent-path>/<filename>.
  Restore: copy any version file back to the live path (saves its own history entry).
  git tracks only static config files; .history/ is gitignored (fine-grained versioning).
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Fallback: check env var for non-standard layouts
_AGENTS_ROOT_ENV = os.environ.get("TANTRA_AGENTS_ROOT")


def _agents_root() -> Path:
    """Return the resolved agents/ root, raising clearly if not found."""
    # 1. Env override (highest priority — useful for tests and non-standard layouts)
    if _AGENTS_ROOT_ENV and Path(_AGENTS_ROOT_ENV).is_dir():
        return Path(_AGENTS_ROOT_ENV)
    # 2. Docker layout: /app/agents (primary production path)
    docker_candidate = Path("/app/agents")
    if docker_candidate.is_dir():
        return docker_candidate
    # 3. Host dev layout: repo_root/agents (relative to this file)
    #    __file__ = <repo>/src/tantra/core/agent_loader.py
    #    parents: [0]=core, [1]=tantra, [2]=src, [3]=<repo root>
    host_candidate = Path(__file__).resolve().parents[3] / "agents"
    if host_candidate.is_dir():
        return host_candidate
    raise FileNotFoundError(
        f"agents/ directory not found. Tried: env={_AGENTS_ROOT_ENV!r}, "
        f"{docker_candidate}, {host_candidate}. "
        "Set TANTRA_AGENTS_ROOT env var to override."
    )


def _read(path: Path, default: str = "") -> str:
    """Read a file, returning default if it doesn't exist. Always fresh (no cache)."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.debug(f"AgentConfigLoader: file not found (using default): {path}")
        return default
    except Exception as exc:
        logger.warning(f"AgentConfigLoader: error reading {path}: {exc}")
        return default


# ---------------------------------------------------------------------------
# Version history helpers
# ---------------------------------------------------------------------------

def _slugify(text: str, max_len: int = 40) -> str:
    """Convert a comment string to a safe filename slug."""
    text = text.strip().lower()
    # Normalize unicode
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    # Replace non-alphanumeric with hyphens
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:max_len] or "update"


def _now_ts() -> str:
    """Return current UTC timestamp as filename-safe string: 20260409_143000."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _iso_ts() -> str:
    """Return current UTC timestamp as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_file_with_history(
    live_path: Path,
    content: str,
    comment: str = "update",
    actor: str = "user",
) -> dict:
    """
    Write content to live_path AND save a versioned copy with a CHANGELOG entry.

    History structure (inside agents/<agent>/.history/<filename>/):
      CHANGELOG.json          — ordered list of all versions (newest first)
      <ts>_<slug>.md          — full file content snapshot

    Args:
        live_path:  Absolute path to the live config file.
        content:    New file content.
        comment:    Human-readable description of the change (stored in CHANGELOG).
        actor:      Who made the change: "user" | "agent" | "system".

    Returns:
        dict with keys: version_file, timestamp, comment, size
    """
    # Build .history directory path: agents/<agent>/.history/<filename>/
    history_dir = live_path.parent / ".history" / live_path.name
    history_dir.mkdir(parents=True, exist_ok=True)

    # Build versioned filename: <ts>_<slug><ext>
    ts = _now_ts()
    iso = _iso_ts()
    slug = _slugify(comment)
    version_filename = f"{ts}_{slug}{live_path.suffix}"
    version_path = history_dir / version_filename

    # Write versioned copy
    version_path.write_text(content, encoding="utf-8")

    # Update CHANGELOG.json
    changelog_path = history_dir / "CHANGELOG.json"
    try:
        changelog: list[dict] = json.loads(changelog_path.read_text(encoding="utf-8")) if changelog_path.exists() else []
    except (json.JSONDecodeError, Exception):
        changelog = []

    entry = {
        "ts": iso,
        "comment": comment,
        "actor": actor,
        "version_file": version_filename,
        "size": len(content),
    }
    # Newest first
    changelog.insert(0, entry)
    changelog_path.write_text(json.dumps(changelog, indent=2, ensure_ascii=False), encoding="utf-8")

    # Write the live file
    live_path.write_text(content, encoding="utf-8")

    logger.debug(f"save_with_history: {live_path.name} v={version_filename} comment={comment!r}")
    return entry


def list_file_history(live_path: Path) -> list[dict]:
    """
    Return the version history for a file, newest first.
    Each entry: {ts, comment, actor, version_file, size}
    Returns [] if no history exists.
    """
    changelog_path = live_path.parent / ".history" / live_path.name / "CHANGELOG.json"
    if not changelog_path.exists():
        return []
    try:
        return json.loads(changelog_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def read_file_version(live_path: Path, version_file: str) -> Optional[str]:
    """
    Read the content of a specific historical version.
    version_file: filename from CHANGELOG.json (e.g. '20260409_143000_initial-setup.md')
    Returns None if not found.
    """
    version_path = live_path.parent / ".history" / live_path.name / version_file
    if not version_path.exists():
        return None
    return version_path.read_text(encoding="utf-8")


def restore_file_version(live_path: Path, version_file: str, comment: Optional[str] = None) -> Optional[dict]:
    """
    Restore a historical version to the live file (creates a new history entry).
    Returns the new history entry, or None if version_file not found.
    """
    content = read_file_version(live_path, version_file)
    if content is None:
        return None
    restore_comment = comment or f"restored from {version_file}"
    return save_file_with_history(live_path, content, comment=restore_comment, actor="user")


# ---------------------------------------------------------------------------
# AgentConfigLoader
# ---------------------------------------------------------------------------

class AgentConfigLoader:
    """
    Loads all config files for a single agent from the agents/ directory.

    Usage:
        loader = AgentConfigLoader("director")
        system_prompt = loader.build_system_prompt()

        # YouTube crew agent:
        loader = AgentConfigLoader("youtube-crew/script-writer")
        backstory = loader.build_crewai_backstory()

    All read properties return fresh disk content — no caching.
    All write methods save history automatically.
    """

    def __init__(self, agent_path: str) -> None:
        """
        Args:
            agent_path: Relative path inside agents/ directory.
                        Examples: "director", "youtube-crew/researcher"
        """
        self.agent_path = agent_path
        self._dir: Optional[Path] = None

    @property
    def dir(self) -> Path:
        """Resolve and cache (once) the agent directory path."""
        if self._dir is None:
            root = _agents_root()
            candidate = root / self.agent_path
            if not candidate.is_dir():
                logger.warning(
                    f"AgentConfigLoader: agent dir not found: {candidate}. "
                    "Returning empty configs."
                )
            self._dir = candidate
        return self._dir

    # ------------------------------------------------------------------
    # Static config files (soul, skills, policy, memory, tools) — READ
    # ------------------------------------------------------------------

    @property
    def soul(self) -> str:
        """Identity, personality, purpose — read fresh from soul.md."""
        return _read(self.dir / "soul.md")

    @property
    def skills(self) -> str:
        """Capabilities and output formats — read fresh from skills.md."""
        return _read(self.dir / "skills.md")

    @property
    def policy(self) -> str:
        """Rules and content policy — read fresh from policy.md."""
        return _read(self.dir / "policy.md")

    @property
    def memory(self) -> str:
        """Persistent context and stack state — read fresh from memory.md."""
        return _read(self.dir / "memory.md")

    @property
    def tools(self) -> dict:
        """Tool bindings — read fresh from tools.json. Returns {} if missing/invalid."""
        raw = _read(self.dir / "tools.json")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(f"AgentConfigLoader: invalid tools.json for {self.agent_path}: {exc}")
            return {}

    # ------------------------------------------------------------------
    # Dynamic config files (reflection, learning, feedback, evaluation) — READ
    # ------------------------------------------------------------------

    @property
    def reflection(self) -> str:
        """Per-task reflection log — read fresh from reflection.md."""
        return _read(self.dir / "reflection.md")

    @property
    def learning(self) -> str:
        """Consolidated learnings — read fresh from learning.md."""
        return _read(self.dir / "learning.md")

    @property
    def feedback(self) -> str:
        """Feedback log — read fresh from feedback.md."""
        return _read(self.dir / "feedback.md")

    @property
    def evaluation(self) -> str:
        """Evaluation metrics — read fresh from evaluation.md."""
        return _read(self.dir / "evaluation.md")

    # ------------------------------------------------------------------
    # Write methods — all save history automatically
    # ------------------------------------------------------------------

    def write_file(self, filename: str, content: str, comment: str = "update", actor: str = "user") -> dict:
        """
        Write any config file with automatic history tracking.

        Args:
            filename: e.g. "soul.md", "reflection.md", "tools.json"
            content:  New file content.
            comment:  Short description of the change (stored in history).
            actor:    "user" | "agent" | "system"

        Returns:
            History entry dict: {ts, comment, actor, version_file, size}
        """
        live_path = self.dir / filename
        return save_file_with_history(live_path, content, comment=comment, actor=actor)

    def append_reflection(self, content: str, comment: str = "task reflection") -> None:
        """
        Append a reflection entry to reflection.md with history tracking.
        Called automatically after significant task completions.
        """
        path = self.dir / "reflection.md"
        try:
            existing = _read(path)
            separator = "---\n"
            if separator in existing:
                idx = existing.index(separator) + len(separator)
                new_content = existing[:idx] + "\n" + content + "\n" + existing[idx:]
            else:
                new_content = existing + "\n" + content
            save_file_with_history(path, new_content, comment=comment, actor="agent")
            logger.debug(f"AgentConfigLoader: appended reflection for {self.agent_path}")
        except Exception as exc:
            logger.warning(f"AgentConfigLoader: failed to write reflection for {self.agent_path}: {exc}")

    def get_history(self, filename: str) -> list[dict]:
        """Return version history for a config file (newest first)."""
        return list_file_history(self.dir / filename)

    def read_version(self, filename: str, version_file: str) -> Optional[str]:
        """Read the content of a specific historical version."""
        return read_file_version(self.dir / filename, version_file)

    def restore_version(self, filename: str, version_file: str, comment: Optional[str] = None) -> Optional[dict]:
        """Restore a historical version as the new live content (saves a history entry)."""
        return restore_file_version(self.dir / filename, version_file, comment=comment)

    # ------------------------------------------------------------------
    # System prompt builder — the main entry point
    # ------------------------------------------------------------------

    def build_system_prompt(
        self,
        include_reflection: bool = False,
        include_learning: bool = True,
        include_feedback: bool = False,
        include_evaluation: bool = False,
        extra_context: Optional[str] = None,
    ) -> str:
        """
        Assemble a complete system prompt from config files.

        Reads ALL included files fresh from disk on every call.
        File changes take effect on the NEXT call — no restart needed.

        Args:
            include_reflection: Include the reflection log (verbose, not needed usually).
            include_learning:   Include consolidated learnings (recommended: True).
            include_feedback:   Include feedback log (useful for coaching).
            include_evaluation: Include evaluation metrics (useful for self-improvement loops).
            extra_context:      Optional extra context injected at the end.

        Returns:
            A complete system prompt string ready to send as {"role": "system"}.
        """
        parts: list[str] = []

        soul = self.soul
        if soul:
            parts.append(soul)

        skills = self.skills
        if skills:
            parts.append(skills)

        policy = self.policy
        if policy:
            parts.append(policy)

        memory = self.memory
        if memory:
            parts.append(memory)

        if include_learning:
            learning = self.learning
            # Only include learnings if there's actual content (skip the empty placeholder)
            if learning and "_No learnings" not in learning:
                parts.append("---\n## Applied Learnings\n" + learning)

        if include_reflection:
            reflection = self.reflection
            if reflection and "_No reflections" not in reflection:
                parts.append("---\n## Recent Reflections\n" + reflection)

        if include_feedback:
            feedback = self.feedback
            if feedback and "_No feedback" not in feedback:
                parts.append("---\n## Feedback History\n" + feedback)

        if include_evaluation:
            evaluation = self.evaluation
            if evaluation and "_No evaluations" not in evaluation:
                parts.append("---\n## Performance Metrics\n" + evaluation)

        if extra_context:
            parts.append(f"---\n## Session Context\n{extra_context}")

        if not parts:
            logger.warning(
                f"AgentConfigLoader: no config files found for '{self.agent_path}'. "
                "Using generic fallback prompt."
            )
            return (
                "You are a specialised agent in the Tantra AI system. "
                "Reason clearly, plan step-by-step, and produce actionable output."
            )

        return "\n\n".join(parts)

    def build_crewai_backstory(self) -> str:
        """
        Build a CrewAI backstory string from soul.md + skills.md.
        Used when instantiating crewai.Agent(backstory=...).
        """
        parts = []
        soul = self.soul
        if soul:
            parts.append(soul)
        skills = self.skills
        if skills:
            parts.append(skills)
        if not parts:
            return f"A specialised agent in the Tantra AI system ({self.agent_path})."
        return "\n\n".join(parts)

    def __repr__(self) -> str:
        return f"<AgentConfigLoader path={self.agent_path!r} dir={self.dir}>"


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def load_agent_config(agent_path: str) -> AgentConfigLoader:
    """
    Shorthand factory. Returns a loader for the given agent path.

    Example:
        config = load_agent_config("youtube-crew/researcher")
        researcher = Agent(
            role="YouTube Content Research Analyst",
            backstory=config.build_crewai_backstory(),
            ...
        )
    """
    return AgentConfigLoader(agent_path)
