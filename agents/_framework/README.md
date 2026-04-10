# Tantra AI — Agent Config Framework

## Overview

Each agent has its own directory containing 9 config files.
All files are hot-reloaded — changes apply immediately to the running agent (no restart needed).

## File Types

| File | Type | Purpose | Hot-reload |
|------|------|---------|-----------|
| `soul.md` | Static | Identity, personality, purpose | ✅ |
| `skills.md` | Static | Capabilities, output formats, constraints | ✅ |
| `policy.md` | Static | Rules, content policy, escalation paths | ✅ |
| `memory.md` | Static | Persistent context, stack state, URLs | ✅ |
| `tools.json` | Static | Tool bindings with schemas | ✅ |
| `reflection.md` | Dynamic | Per-task reflection log (auto-written) | ✅ |
| `learning.md` | Dynamic | Distilled insights from reflections | ✅ |
| `feedback.md` | Dynamic | Vijay's feedback on agent performance | ✅ |
| `evaluation.md` | Dynamic | KPI metrics, weekly scorecards | ✅ |

## Directory Structure

```
agents/
  director/               # Chief AI Intelligence Officer
  youtube-crew/
    researcher/           # Trend + gap analysis
    script-writer/        # Scene-by-scene script
    seo-optimizer/        # Title, description, tags, thumbnail
    quality-reviewer/     # Validation + final JSON output
  social-crew/
    researcher/           # LinkedIn topic research
    drafter/              # Post drafting
  media/
    video-agent/          # LTX-Video / HunyuanVideo (Phase 3e)
    image-agent/          # Flux.1-dev (Phase 3e)
  _framework/             # Templates for new agents
  core/                   # Cross-agent system config
```

## How Hot-Reload Works

`AgentConfigLoader` (in `src/tantra/core/agent_loader.py`) reads markdown files
fresh on every `build_system_prompt()` call. There is no Python-level caching.

The `/app` directory is bind-mounted into all Docker containers. When you edit a
file on the host, the change is immediately visible inside every container.

Result: Edit `soul.md` on the host → next LLM turn uses the new version. Zero restart.

## Creating a New Agent

1. Create directory: `agents/<crew>/<agent-name>/`
2. Copy templates from `_framework/`
3. Fill in `soul.md`, `skills.md`, `policy.md`, `memory.md`, `tools.json`
4. Add loader call in the Python agent class

## Browser Dashboard

Visit http://localhost:8000/agents to:
- Browse the full agent tree
- Read/edit any config file in the browser
- See reflection/learning/feedback/evaluation for each agent
