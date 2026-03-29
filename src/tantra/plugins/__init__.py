"""
Tantra AI — Plugin Engine
तंत्र · Composable capability extensions

A plugin is a directory containing:
  PLUGIN.md  — manifest (YAML frontmatter + description)
  plugin.py  — optional Python entry point (register() function)

Plugins can register:
  - Celery tasks (new scheduled/background work)
  - FastAPI routes (new API endpoints)
  - CrewAI tools (new agent capabilities)
  - Skills (bundled SKILL.md files)
  - LiteLLM model providers

Compatible with OpenClaw plugin bundle format (.claude-plugin/, .cursor-plugin/).
"""
from tantra.plugins.loader import PluginLoader, Plugin
from tantra.plugins.registry import PluginRegistry

__all__ = ["PluginLoader", "Plugin", "PluginRegistry"]
