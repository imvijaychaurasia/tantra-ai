#!/usr/bin/env python3
"""
Tantra AI — LiteLLM Config Generator
तंत्र  ·  Renders litellm_config.yaml from tantra_models.yaml + template

Usage:
    python scripts/generate_litellm_config.py                  # dry-run (print to stdout)
    python scripts/generate_litellm_config.py --apply          # write config/litellm_config.yaml
    python scripts/generate_litellm_config.py --check-ollama   # verify pulled models
    python scripts/generate_litellm_config.py --apply --restart-litellm  # apply + restart container

Called by:
    tantra config model <tier> <tag>   — after updating tantra_models.yaml
    tantra config sync-models          — verify + optionally apply
    make sync-models                   — shorthand
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — relative to repo root
# TANTRA_ROOT env var overrides __file__-based resolution for machines where
# the script lives in a read-only bind-mount (e.g. Docker /app/scripts).
# ---------------------------------------------------------------------------
import os as _os
REPO_ROOT = Path(_os.environ["TANTRA_ROOT"]).resolve() if _os.environ.get("TANTRA_ROOT") else Path(__file__).parent.parent
REGISTRY_PATH = REPO_ROOT / "config" / "tantra_models.yaml"
TEMPLATE_PATH = REPO_ROOT / "config" / "litellm_config.template.yaml"
OUTPUT_PATH = REPO_ROOT / "config" / "litellm_config.yaml"
OLLAMA_API = "http://localhost:11434"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_registry() -> dict:
    """Load and validate tantra_models.yaml."""
    try:
        import yaml
    except ImportError:
        print("ERROR: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    if not REGISTRY_PATH.exists():
        print(f"ERROR: Registry not found: {REGISTRY_PATH}", file=sys.stderr)
        sys.exit(1)

    with REGISTRY_PATH.open() as f:
        data = yaml.safe_load(f)

    tiers = data.get("tiers")
    if not tiers:
        print("ERROR: tantra_models.yaml has no 'tiers' section", file=sys.stderr)
        sys.exit(1)

    return data


def _render_template(registry: dict) -> str:
    """Render litellm_config.template.yaml with the registry data."""
    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined
    except ImportError:
        print("ERROR: Jinja2 not installed. Run: pip install jinja2", file=sys.stderr)
        sys.exit(1)

    if not TEMPLATE_PATH.exists():
        print(f"ERROR: Template not found: {TEMPLATE_PATH}", file=sys.stderr)
        sys.exit(1)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_PATH.parent)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(TEMPLATE_PATH.name)

    return template.render(
        tiers=registry["tiers"],
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _get_ollama_models() -> set[str]:
    """Query Ollama's /api/tags to get all pulled model tags."""
    try:
        url = f"{OLLAMA_API}/api/tags"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        # Ollama returns models as [{"name": "qwen3:14b", ...}, ...]
        return {m["name"] for m in data.get("models", [])}
    except Exception as exc:
        print(f"WARNING: Could not reach Ollama at {OLLAMA_API}: {exc}", file=sys.stderr)
        return set()


def _check_registry_vs_ollama(registry: dict, pulled: set[str]) -> list[dict]:
    """
    Compare registry primary/fallback models against Ollama pulled models.
    Returns list of issues: [{tier, model, severity, message}, ...]
    """
    issues = []
    tiers = registry.get("tiers", {})

    for tier_name, tier in tiers.items():
        if tier_name == "embedder":
            # Embedder models are short-name (bge-m3, nomic-embed-text)
            # Ollama may return them with or without ':latest'
            local_models = [tier.get("primary"), tier.get("fallback")]
        else:
            local_models = [tier.get("primary"), tier.get("fallback")]

        for model in local_models:
            if not model:
                continue
            # Check both "model:tag" and "model:latest" forms
            tag = model if ":" in model else f"{model}:latest"
            bare = model.split(":")[0]
            pulled_names = {m.split(":")[0] for m in pulled}

            if model not in pulled and tag not in pulled and bare not in pulled_names:
                severity = "ERROR" if model == tier.get("primary") else "WARN"
                issues.append({
                    "tier": tier_name,
                    "model": model,
                    "severity": severity,
                    "message": (
                        f"{'Primary' if severity == 'ERROR' else 'Fallback'} model "
                        f"'{model}' for tier '{tier_name}' is NOT pulled in Ollama. "
                        f"Run: ollama pull {model}"
                    ),
                })
    return issues


def _restart_litellm() -> bool:
    """Restart the tantra-litellm Docker container."""
    try:
        result = subprocess.run(
            ["docker", "compose", "restart", "litellm"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True
        else:
            print(f"WARNING: docker compose restart failed: {result.stderr}", file=sys.stderr)
            return False
    except Exception as exc:
        print(f"WARNING: Could not restart litellm: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global OLLAMA_API  # must be declared before any use of OLLAMA_API in this scope

    parser = argparse.ArgumentParser(
        description="Generate litellm_config.yaml from tantra_models.yaml"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write generated config to config/litellm_config.yaml (default: dry-run)"
    )
    parser.add_argument(
        "--check-ollama", action="store_true",
        help="Query Ollama and verify all registry models are pulled"
    )
    parser.add_argument(
        "--restart-litellm", action="store_true",
        help="Restart the tantra-litellm container after applying (requires --apply)"
    )
    parser.add_argument(
        "--ollama-url", default=OLLAMA_API,
        help=f"Ollama API base URL (default: {OLLAMA_API})"
    )
    args = parser.parse_args()
    OLLAMA_API = args.ollama_url

    # ── Load registry ────────────────────────────────────────────────────────
    registry = _load_registry()
    tiers = registry.get("tiers", {})

    # ── Check Ollama if requested ────────────────────────────────────────────
    if args.check_ollama:
        print("\n── Checking Ollama pulled models ──────────────────────────────")
        pulled = _get_ollama_models()
        if pulled:
            print(f"Pulled models ({len(pulled)}):")
            for m in sorted(pulled):
                print(f"  ✓  {m}")
        else:
            print("  (No models found or Ollama unreachable)")

        print("\n── Registry vs Ollama validation ──────────────────────────────")
        issues = _check_registry_vs_ollama(registry, pulled)
        if not issues:
            print("  ✓  All registry models are available in Ollama")
        else:
            for issue in issues:
                icon = "✗" if issue["severity"] == "ERROR" else "⚠"
                print(f"  {icon}  [{issue['severity']}] {issue['message']}")
        print()

    # ── Render template ──────────────────────────────────────────────────────
    rendered = _render_template(registry)

    if not args.apply:
        # Dry-run: print to stdout
        print("── Generated litellm_config.yaml (dry-run) ───────────────────")
        print(rendered)
        print("── To apply: python scripts/generate_litellm_config.py --apply ─")
        return

    # ── Write config ─────────────────────────────────────────────────────────
    OUTPUT_PATH.write_text(rendered)
    print(f"✓  Written: {OUTPUT_PATH}")

    # Print summary table
    print("\nActive tier mapping:")
    for tier_name, tier in tiers.items():
        primary = tier.get("primary", "(none)")
        cloud = tier.get("cloud", "")
        cloud_str = f"  |  cloud: {cloud}" if cloud else ""
        print(f"  {tier_name:<12} →  {primary}{cloud_str}")

    # ── Restart litellm if requested ─────────────────────────────────────────
    if args.restart_litellm:
        print("\nRestarting tantra-litellm...")
        if _restart_litellm():
            print("✓  tantra-litellm restarted")
        else:
            print("✗  Restart failed — run manually: docker compose restart litellm")


if __name__ == "__main__":
    main()
