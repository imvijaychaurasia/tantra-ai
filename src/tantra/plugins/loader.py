"""
Tantra AI — Plugin Loader
तंत्र · Reads PLUGIN.md manifests and optionally calls plugin.py register()

PLUGIN.md frontmatter fields:
  name:         string  — unique slug (kebab-case)
  version:      string  — semver
  description:  string  — one-line description
  author:       string  — author handle
  enabled:      bool    — default true
  homepage:     string  — URL
  capabilities: dict    — what this plugin registers (declarative)
    celery_tasks: list[str]
    api_routes:   list[str]  — route prefixes
    tools:        list[str]  — CrewAI tool names
    skills:       list[str]  — bundled skill names
    model_providers: list[str]
  dependencies: list[str]  — pip packages required
  metadata:     JSON    — gates (same format as SKILL.md)
"""
from __future__ import annotations

import importlib.util
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_BUILTIN_PLUGINS_ROOT = Path(__file__).parents[4] / "plugins"
_USER_PLUGINS_DIR = Path.home() / ".tantra" / "plugins"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


@dataclass
class PluginCapabilities:
    celery_tasks: list[str] = field(default_factory=list)
    api_routes: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    model_providers: list[str] = field(default_factory=list)


@dataclass
class Plugin:
    name: str
    version: str
    description: str
    author: str
    enabled: bool
    homepage: str
    capabilities: PluginCapabilities
    dependencies: list[str]
    path: Path
    _register_fn: Optional[Callable] = field(default=None, repr=False)

    def register(self, api: Any = None) -> None:
        """Call the plugin's register() function if it has one."""
        if self._register_fn:
            try:
                self._register_fn(api)
                logger.info("Plugin '%s' registered", self.name)
            except Exception as exc:
                logger.error("Plugin '%s' register() failed: %s", self.name, exc)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "enabled": self.enabled,
            "homepage": self.homepage,
            "capabilities": {
                "celery_tasks": self.capabilities.celery_tasks,
                "api_routes": self.capabilities.api_routes,
                "tools": self.capabilities.tools,
                "skills": self.capabilities.skills,
                "model_providers": self.capabilities.model_providers,
            },
            "dependencies": self.dependencies,
            "path": str(self.path),
        }


def _parse_plugin_md(plugin_dir: Path) -> Optional[Plugin]:
    """Parse a PLUGIN.md file and return a Plugin dataclass."""
    plugin_md = plugin_dir / "PLUGIN.md"
    if not plugin_md.exists():
        return None

    raw = plugin_md.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        logger.warning("PLUGIN.md missing frontmatter: %s", plugin_md)
        return None

    fm_raw, _ = m.group(1), m.group(2)
    fm: dict = {}
    for line in fm_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val.startswith("{") or val.startswith("["):
                try:
                    val = json.loads(val)
                except Exception:
                    pass
            elif val.lower() in ("true", "yes"):
                val = True
            elif val.lower() in ("false", "no"):
                val = False
            fm[key] = val

    name = fm.get("name", "")
    if not name:
        logger.warning("Plugin missing 'name': %s", plugin_md)
        return None

    # Parse capabilities
    caps_raw = fm.get("capabilities", {})
    if isinstance(caps_raw, str):
        try:
            caps_raw = json.loads(caps_raw)
        except Exception:
            caps_raw = {}
    caps = PluginCapabilities(
        celery_tasks=caps_raw.get("celery_tasks", []),
        api_routes=caps_raw.get("api_routes", []),
        tools=caps_raw.get("tools", []),
        skills=caps_raw.get("skills", []),
        model_providers=caps_raw.get("model_providers", []),
    )

    # Parse dependencies list
    deps = fm.get("dependencies", [])
    if isinstance(deps, str):
        deps = [d.strip() for d in deps.split(",") if d.strip()]

    # Try to load plugin.py register function
    register_fn = None
    plugin_py = plugin_dir / "plugin.py"
    if plugin_py.exists():
        try:
            spec = importlib.util.spec_from_file_location(f"tantra_plugin_{name}", plugin_py)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            register_fn = getattr(mod, "register", None)
        except Exception as exc:
            logger.warning("Could not load plugin.py for '%s': %s", name, exc)

    return Plugin(
        name=name,
        version=str(fm.get("version", "1.0.0")),
        description=str(fm.get("description", "")),
        author=str(fm.get("author", "")),
        enabled=bool(fm.get("enabled", True)),
        homepage=str(fm.get("homepage", "")),
        capabilities=caps,
        dependencies=deps,
        path=plugin_dir,
        _register_fn=register_fn,
    )


class PluginLoader:
    """Loads plugins from built-in and user directories."""

    def __init__(self):
        self._plugins: dict[str, Plugin] = {}
        self._loaded = False

    def load(self, force: bool = False) -> "PluginLoader":
        if self._loaded and not force:
            return self

        loaded: dict[str, Plugin] = {}
        for plugins_dir in [_BUILTIN_PLUGINS_ROOT, _USER_PLUGINS_DIR]:
            if not plugins_dir.is_dir():
                continue
            for item in sorted(plugins_dir.iterdir()):
                if not item.is_dir():
                    continue
                plugin = _parse_plugin_md(item)
                if plugin and plugin.enabled:
                    loaded[plugin.name] = plugin
                    logger.debug("Loaded plugin: %s v%s", plugin.name, plugin.version)

        self._plugins = loaded
        self._loaded = True
        logger.info("PluginLoader: %d plugins loaded", len(self._plugins))
        return self

    def get(self, name: str) -> Optional[Plugin]:
        if not self._loaded:
            self.load()
        return self._plugins.get(name)

    def list(self) -> list[Plugin]:
        if not self._loaded:
            self.load()
        return list(self._plugins.values())

    def register_all(self, api: Any = None) -> None:
        """Call register() on all loaded plugins."""
        for plugin in self.list():
            plugin.register(api)


_default_loader: Optional[PluginLoader] = None


def get_loader() -> PluginLoader:
    global _default_loader
    if _default_loader is None:
        _default_loader = PluginLoader()
    if not _default_loader._loaded:
        _default_loader.load()
    return _default_loader
