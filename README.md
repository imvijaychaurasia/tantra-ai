# तंत्र — Tantra AI

**Local Autonomous Agent Intelligence Stack**

> *Tantra (तंत्र)* — Sanskrit for "system", "framework", "the loom that weaves everything together."

Tantra AI is a **fully local**, hierarchical multi-agent platform that autonomously generates income through social media, digital content, and online services — no cloud dependency required.

---

## What it does

- **Thinks and acts autonomously** — a CAIO (Chief AI Intelligence Officer) leads 5 C-suite agents (CMO, CTO, CFO, CRO, CCO), each heading a team of director and worker agents
- **Writes and publishes content** — LinkedIn posts, YouTube scripts, social media campaigns, fully automated
- **Remembers everything** — 4-layer memory (short-term context, long-term facts, episodic log, semantic search)
- **Runs on your machine** — Ollama models (Llama 3.3 70B, Qwen 2.5 72B, Phi-4, DeepSeek Coder) via Metal on Apple Silicon
- **Plugs into the cloud when needed** — Claude 3.5 Sonnet or GPT-4o for frontier tasks via LiteLLM

---

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────┐
│                    Open WebUI / CLI                     │  ← You talk here
├─────────────────────────────────────────────────────────┤
│              FastAPI  (REST + WebSocket)                │  ← Tantra API
├─────────────────────────────────────────────────────────┤
│   CAIO (Frontier)  →  C-Suite Leaders (Director tier)  │
│   └─ Research / Content / Publish / Analyze Workers    │  ← Agent Hierarchy
├─────────────────────────────────────────────────────────┤
│   CrewAI crews  │  LangGraph graphs  │  AutoGen debate  │  ← Orchestration
├─────────────────────────────────────────────────────────┤
│      mem0  │  Qdrant  │  PostgreSQL  │  Redis           │  ← Memory & Data
├─────────────────────────────────────────────────────────┤
│        LiteLLM Proxy  (frontier / director / worker)   │  ← Model Gateway
├─────────────────────────────────────────────────────────┤
│   Ollama (local)  +  Anthropic / OpenAI / Groq (cloud) │  ← Models
└─────────────────────────────────────────────────────────┘
```

---

## Quick start

### Prerequisites

- macOS (Apple Silicon M-series) or Linux (x86-64 / NVIDIA)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) 4.x+
- Python 3.11+
- ~60 GB free disk space (models)

### 1. Clone and set up

```bash
git clone https://github.com/yourhandle/tantra-ai.git
cd tantra-ai
bash scripts/setup.sh
```

The setup script will:
- Create a `.env` file (fill in your API keys)
- Install Python dependencies
- Start core Docker services
- Guide you to pull Ollama models

### 2. Add your API keys (optional but recommended)

Edit `.env`:

```env
# For frontier-tier strategic tasks (at least one recommended)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# For Phase 1 social platforms
LINKEDIN_CLIENT_ID=...
LINKEDIN_CLIENT_SECRET=...
YOUTUBE_API_KEY=...
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...
```

All keys are optional — Tantra runs 100% locally on Ollama if no keys are provided.

### 3. Pull local models

```bash
make pull-models
```

This downloads ~43 GB of models. Go make some chai ☕

### 4. Start everything

```bash
make up-all
```

| Interface | URL |
|-----------|-----|
| Open WebUI (chat) | http://localhost:3000 |
| Tantra API docs | http://localhost:8000/docs |
| n8n (workflows) | http://localhost:5678 |
| LiteLLM dashboard | http://localhost:4000 |

---

## Using the CLI

```bash
# Activate virtual env first
source .venv/bin/activate

# Run a single agent task
tantra run "Research top AI trends this week and summarise in 5 bullets"

# Run a specific model tier
tantra run "Write a LinkedIn post about agentic AI" --model director

# Run the full social media crew
tantra crew social "Create a week's worth of LinkedIn content on AI automation"

# Save and search memory
tantra memory save "User prefers data-backed LinkedIn posts with real examples"
tantra memory search "What LinkedIn content style should I use?"

# Check all service statuses
tantra status

# Start the API server
tantra serve --reload
```

---

## Model tiers

| Alias | Default model | Used for |
|-------|--------------|---------|
| `frontier` | Claude 3.5 Sonnet / GPT-4o | Strategic decisions, CAIO |
| `director` | Llama 3.3 70B (local) | Department leaders (CMO, CTO…) |
| `manager` | Qwen 2.5 72B (local) | Mid-level planning, complex tasks |
| `worker` | Phi-4 14B (local) | Single focused tasks |
| `coder` | DeepSeek Coder V2 16B (local) | Code generation |
| `fast` | Mistral Nemo 12B (local) | Routing, classification, quick ops |
| `embedder` | nomic-embed-text (local) | RAG & semantic memory |

Swap any model by editing `config/litellm_config.yaml` — zero code changes needed.

---

## Agent hierarchy

```
CAIO  (frontier model — strategic oversight)
│
├── CMO — Chief Marketing Officer  (director tier)
│   ├── LinkedIn Campaign Manager  (manager)
│   ├── Content Writer             (worker)
│   ├── Social Media Publisher     (worker)
│   └── Analytics Specialist       (worker)
│
├── CTO — Chief Technology Officer  (director tier)
│   ├── DevOps Lead               (manager / coder)
│   ├── API Integration Dev       (coder)
│   └── Infrastructure Monitor    (worker)
│
├── CFO — Chief Finance Officer   (director tier)
├── CRO — Chief Research Officer  (director tier)
└── CCO — Chief Creative Officer  (director tier)
```

LEADER agents read all subordinate memories. Workers only access their own namespace.

---

## Operational modes

| Mode | How | When |
|------|-----|------|
| **Autonomous** | Single agent works solo | Quick one-off tasks |
| **Team** | CrewAI hierarchical crew | Complex multi-step work |
| **Pipeline** | LangGraph DAG | Sequential processing chains |
| **Scheduled** | Celery Beat cron | Daily posts, weekly reports |

---

## Phase 1 — Social platforms

- **LinkedIn** — OAuth 2.0, post creation, image posts, analytics
- **YouTube** — Data API v3, video search, channel stats, upload

Phase 2 (planned): Twitter/X, Instagram, Facebook, Substack, Medium

---

## Project structure

```
tantra-ai/
├── docker-compose.yml          # All services (Ollama, LiteLLM, Postgres, Redis, Qdrant, n8n)
├── Dockerfile                  # Multi-stage: api + worker targets
├── Makefile                    # make up / make pull-models / make test
├── pyproject.toml              # Python deps + tool config
├── alembic.ini                 # DB migration config
├── config/
│   └── litellm_config.yaml     # Model tier aliases → actual models
├── scripts/
│   ├── setup.sh                # First-time setup
│   └── pull_models.sh          # Download Ollama models
├── src/tantra/
│   ├── main.py                 # FastAPI app entry point
│   ├── cli.py                  # Typer CLI (tantra command)
│   ├── core/
│   │   ├── config.py           # Centralised settings (pydantic-settings)
│   │   ├── llm.py              # LiteLLM gateway (chat / embed / stream)
│   │   └── database.py         # Async SQLAlchemy + session factory
│   ├── agents/
│   │   ├── base.py             # TantraAgent abstract base
│   │   ├── leader.py           # LeaderAgent (plan + delegate + synthesise)
│   │   └── worker.py           # WorkerAgent + preset factory functions
│   ├── memory/
│   │   └── manager.py          # MemoryManager + LeaderMemoryManager (Qdrant)
│   ├── rag/
│   │   └── pipeline.py         # KnowledgeBase (LlamaIndex + Qdrant)
│   ├── crews/
│   │   └── social_crew.py      # CrewAI social media crew (CMO + 4 workers)
│   ├── tasks/
│   │   └── celery_app.py       # Celery app + beat schedule + task definitions
│   ├── tools/
│   │   ├── linkedin.py         # LinkedIn API client + post/analytics tools
│   │   ├── youtube.py          # YouTube API client + search/upload tools
│   │   └── mcp/
│   │       └── social_mcp_server.py  # MCP-compatible HTTP server
│   └── api/
│       └── routes.py           # FastAPI routes (agent, auth, social, memory)
├── tests/
│   └── test_core.py            # Unit tests (no Docker required)
└── data/                       # Local persistent storage (gitignored)
    ├── models/                 # Ollama model cache
    ├── postgres/               # PostgreSQL data + init SQL
    ├── qdrant/                 # Qdrant vector storage
    └── uploads/                # File uploads
```

---

## Linux + NVIDIA (future)

When you move to a Linux machine with an NVIDIA GPU, create `docker-compose.nvidia.yml`:

```yaml
services:
  ollama:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
```

Then run: `docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d`

---

## Development

```bash
make lint        # ruff linter
make format      # ruff auto-format
make typecheck   # mypy
make test        # pytest
make test-cov    # pytest + coverage report
```

---

## License

MIT — build freely, earn freely.

---

*"तंत्र is not magic. It is a system. Build the system."*
