"""
Tantra AI — Plugin Registry
तंत्र · Install, list, enable/disable plugins
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_USER_PLUGINS_DIR = Path.home() / ".tantra" / "plugins"
_REGISTRY_INDEX = _USER_PLUGINS_DIR / "registry.json"
_BUILTIN_PLUGINS_ROOT = Path(__file__).parents[4] / "plugins"


class PluginRegistry:

    def __init__(self, plugins_dir: Optional[Path] = None):
        self._dir = plugins_dir or _USER_PLUGINS_DIR
        self._index_path = self._dir / "registry.json"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _read_index(self) -> dict:
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text())
            except Exception:
                pass
        return {"version": "1", "plugins": {}}

    def _write_index(self, index: dict) -> None:
        self._index_path.write_text(json.dumps(index, indent=2, default=str))

    def list_all(self) -> list[dict]:
        """List built-in + installed plugins."""
        from tantra.plugins.loader import _parse_plugin_md

        plugins = []
        # Built-in
        if _BUILTIN_PLUGINS_ROOT.exists():
            for item in sorted(_BUILTIN_PLUGINS_ROOT.iterdir()):
                if item.is_dir():
                    p = _parse_plugin_md(item)
                    if p:
                        d = p.to_dict()
                        d["source"] = "builtin"
                        plugins.append(d)
        # User-installed
        index = self._read_index()
        for entry in index.get("plugins", {}).values():
            plugins.append({**entry, "source": "installed"})
        return plugins

    def install_from_path(self, source: Path, overwrite: bool = False) -> dict:
        from tantra.plugins.loader import _parse_plugin_md

        plugin = _parse_plugin_md(source)
        if plugin is None:
            raise ValueError(f"No valid PLUGIN.md found in {source}")

        dest = self._dir / plugin.name
        if dest.exists() and not overwrite:
            raise FileExistsError(f"Plugin '{plugin.name}' already installed.")

        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)

        index = self._read_index()
        index["plugins"][plugin.name] = {
            **plugin.to_dict(),
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source),
        }
        self._write_index(index)
        logger.info("Installed plugin '%s' v%s", plugin.name, plugin.version)
        return index["plugins"][plugin.name]

    def uninstall(self, name: str) -> bool:
        dest = self._dir / name
        if not dest.exists():
            return False
        shutil.rmtree(dest)
        index = self._read_index()
        index["plugins"].pop(name, None)
        self._write_index(index)
        return True
