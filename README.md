# तंत्र — Tantra AI

**Local Autonomous Agent Intelligence Stack**

> *Tantra (तंत्र)* — Sanskrit for "system", "framework", "the loom that weaves everything together."

Tantra AI is a **fully local**, hierarchical multi-agent platform that autonomously generates income through social media, digital content, and online services — no cloud dependency required. Built in public, running in production.

---

## Current Status

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | LinkedIn content pipeline (research → draft → n8n approval → publish) | ✅ Live |
| Phase 2 | Director layer (weekly planning, CAIO chat, task dispatch, resilience) | ✅ Live |
| Phase 3 | Multi-platform earning engine (YouTube, Instagram, X) | 🔜 Next |

**As of Week 14/2026:** 8 tasks completed autonomously. LinkedIn posts live. Director chat operational with real-time DB context grounding.

---

## What it does

- **Thinks and plans autonomously** — a CAIO (Director) leads a weekly content plan, tracks tasks in Postgres, dispatches Celery jobs, and adapts based on performance
- **Interactive Director chat** — conversational REPL (`tantra director chat`) with streaming LLM, Redis session memory, and approval-gated task commitment
- **Writes and publishes content** — LinkedIn posts researched by a CrewAI social crew, drafted, sent through n8n approval workflow, then published
- **Remembers everything** — 4-layer memory (short-term context, long-term facts, episodic log, semantic search via Qdrant)
- **Resilient by design** — crash/restart recovery: stuck `in_progress` tasks auto-reset to `pending`; research drafts checkpoint to Redis so crew work isn't lost on restart
- **Runs on your machine** — Ollama models (qwen3:30b, Llama 3.3 70B, Qwen 2.5 72B) via Metal on Apple Silicon or NVIDIA on Linux
- **Plugs into the cloud when needed** — Claude 3.5/3.7 Sonnet or GPT-4o for frontier tasks via LiteLLM proxy

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│            tantra director chat  (Typer CLI + asyncio REPL)         │  ← You talk here
│            tantra director status / tantra task run / ...           │
├─────────────────────────────────────────────────────────────────────┤
│                     FastAPI  (REST + WebSocket)                     │  ← Tantra API
├─────────────────────────────────────────────────────────────────────┤
│              DirectorAgent (CAIO — qwen3:30b local)                 │
│              ├── weekly_planning  (Monday 6AM Celery beat)          │
│              ├── dispatch_due_tasks  (every 30min)                  │  ← Phase 2 core
│              ├── recover_stuck_tasks  (every 15min + on startup)    │
│              ├── cmo_review / cto_review  (Friday 5PM)              │
│              └── director chat  (interactive, Redis sessions DB3)   │
├─────────────────────────────────────────────────────────────────────┤
│   SocialCrew (CrewAI)          │  DirectorCrew                      │
│   ├── researcher (Tavily)      │  ├── cmo_agent                     │  ← Orchestration
│   ├── content_strategist       │  └── cto_agent                     │
│   ├── writer                   │                                    │
│   └── publisher                │                                    │
├─────────────────────────────────────────────────────────────────────┤
│   Celery Worker (agents queue) │  Celery Beat (scheduler)           │  ← Task engine
│   AgentTask state machine:  pending → in_progress → completed/failed│
├─────────────────────────────────────────────────────────────────────┤
│   PostgreSQL (WeeklyPlan, AgentTask, LinkedInPost, OAuthToken)      │
│   Redis DB1 (Celery broker/backend)  DB3 (app state, sessions)      │  ← Persistence
│   Qdrant (semantic memory vectors)                                  │
├─────────────────────────────────────────────────────────────────────┤
│   LiteLLM Proxy  (model tier aliases → actual models)               │  ← Model gateway
├─────────────────────────────────────────────────────────────────────┤
│   Ollama (local)   +   Anthropic / OpenAI / Groq (cloud optional)   │  ← Models
└─────────────────────────────────────────────────────────────────────┘
```

---

## Quick start

### Prerequisites

- macOS (Apple Silicon M-series) **or** Linux (x86-64 + NVIDIA GPU)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) 4.x+
- Python 3.11+
- ~60 GB free disk space (models)

### 1. Clone and set up

```bash
git clone https://github.com/yourhandle/tantra-ai.git
cd tantra-ai
bash scripts/setup.sh
```

The setup script creates `.env`, installs Python deps, starts core Docker services, and guides you through model pulls.

### 2. Add your API keys

Edit `.env`:

```env
# At least one frontier model key recommended for strategic planning
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# LinkedIn OAuth (for content publishing)
LINKEDIN_CLIENT_ID=...
LINKEDIN_CLIENT_SECRET=...

# YouTube (Phase 3)
YOUTUBE_API_KEY=...
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...

# n8n approval workflow webhook
N8N_WEBHOOK_URL=http://localhost:5678/webhook/...
N8N_WEBHOOK_SECRET=...

# Tavily (web research — free tier works)
TAVILY_API_KEY=tvly-...
```

All cloud keys are optional — Tantra runs 100% locally on Ollama if none are provided.

### 3. Pull local models

```bash
make pull-models
```

Downloads ~43–60 GB of models (qwen3:30b is the primary Director model). Go make some chai ☕

### 4. Start everything

```bash
make up-all
```

| Interface | URL |
|-----------|-----|
| Open WebUI (chat) | http://localhost:3000 |
| Tantra API docs | http://localhost:8000/docs |
| n8n (approval workflows) | http://localhost:5678 |
| LiteLLM dashboard | http://localhost:4000 |

---

## CLI reference

```bash
# Activate virtualenv first
source .venv/bin/activate
```

### Director commands

```bash
# Start an interactive chat session with the Director (CAIO)
# Streams responses, has session memory, grounds itself in live DB context
tantra director chat

# Resume a previous chat session
tantra director chat --resume <session-id>

# List all past Director chat sessions
tantra director sessions

# View current week's plan and all agent task statuses
tantra director status
```

### Task commands

```bash
# List all registered Celery tasks
tantra task list

# Run any task manually (by short name)
tantra task run weekly_planning
tantra task run dispatch_due_tasks
tantra task run research_and_draft_posts
tantra task run post_tantra_progress

# Run and wait for result (default timeout 120s)
tantra task run dispatch_due_tasks --wait

# Check status of a specific Celery task by ID
tantra task status <celery-task-uuid>
```

### Other commands

```bash
# Check all service statuses
tantra status

# Start the API server
tantra serve --reload
```

---

## Director chat — how it works

`tantra director chat` is an interactive REPL that gives you a direct line to the CAIO:

1. **Grounded in reality** — on every turn, the Director reads the active `WeeklyPlan` + all `AgentTask` states from Postgres and injects them as context. It always knows what week it is, what tasks are running, and what's completed.

2. **Session memory** — conversations are stored in Redis DB3 with a 30-day TTL. Resume any session with `--resume`.

3. **Approval-gated commits** — when you type an approval keyword (`approve`, `execute`, `go ahead`, `ship it`, etc.), the system:
   - Intercepts **before** calling the LLM (the keyword never reaches the model)
   - Asks the Director to extract tasks as structured JSON from the conversation
   - Validates task types against the 4 registered Celery handlers
   - Inserts valid rows as `AgentTask` records in Postgres
   - Appends a `[COMMITTED]` note to conversation history (persisted to Redis) so duplicate commits can't happen

4. **Valid task types** (what the Director can commit):

| Task type | Handler | Description |
|-----------|---------|-------------|
| `research_draft` | `social_crew` | CrewAI crew researches topic + drafts LinkedIn post |
| `progress_post` | `director` or `cmo` | Autonomous progress update post |
| `youtube_script` | `social_crew` | YouTube video script generation |
| `analytics_review` | `director` | Review published content performance |

---

## Model tiers

| Alias | Default model | Used for |
|-------|--------------|---------|
| `frontier` | Claude 3.5 Sonnet / GPT-4o | Strategic decisions, frontier tasks |
| `director` | qwen3:30b (local) | CAIO planning, Director chat, CMO/CTO agents |
| `manager` | Qwen 2.5 72B (local) | Mid-level planning, complex tasks |
| `worker` | Phi-4 14B (local) | Single focused tasks |
| `coder` | DeepSeek Coder V2 16B (local) | Code generation |
| `fast` | Mistral Nemo 12B (local) | Routing, classification, quick ops |
| `embedder` | nomic-embed-text (local) | RAG & semantic memory |

Swap any model by editing `config/litellm_config.yaml` — zero code changes needed.

---

## Agent task state machine

```
pending
  │
  ▼  (dispatch_due_tasks fires when scheduled_at <= now)
in_progress
  │
  ├──► completed   (success)
  ├──► failed      (error after retries)
  ├──► skipped     (cooldown active, pre-condition not met)
  │
  └──► pending     (recover_stuck_tasks resets tasks exceeding time budget)
       ↑ crash/restart recovery
```

Time budgets per task type:
- `research_draft` — 30 min (CrewAI crew with Tavily research)
- `progress_post` — 10 min
- `youtube_script` — 20 min
- `analytics_review` — 10 min

Redis checkpointing on `research_draft`: if a worker crashes after the crew completes but before DB insert, the next dispatch skips the LLM crew and resumes at the post-parsing step.

---

## Resilience design

Tantra is designed to survive crashes, Docker restarts, and hardware sleep/wake cycles without losing work or creating duplicate tasks.

**Shutdown/restart safety:**
- `docker compose restart` is safe at any time — `recover_stuck_tasks` runs on `worker_ready` signal and immediately rescues any in-progress tasks that were interrupted
- `docker compose down` (without `-v`) preserves all Postgres and Redis data in `data/` volumes
- **Warning:** `docker compose down -v` wipes volumes. If run while tasks are in-flight, all state is lost and tables are recreated on next start via `Base.metadata.create_all(engine)`

**Cooldown system:**
- `progress_post` has a 24h cooldown enforced via Redis key in DB3
- Prevents duplicate posts when `dispatch_due_tasks` fires multiple times

**Session integrity:**
- Director chat sessions survive worker restarts (stored in Redis DB3, not worker memory)
- `[COMMITTED]` note in conversation history prevents re-extraction of already-committed tasks in the same session

---

## Celery beat schedule

| Task | Schedule | Description |
|------|----------|-------------|
| `weekly_planning` | Monday 6:00 AM | Director plans the week |
| `dispatch_due_tasks` | Every 30 min | Fire pending tasks that are due |
| `recover_stuck_tasks` | Every 15 min | Reset crashed in-progress tasks |
| `cmo_review` | Friday 5:00 PM | CMO reviews content performance |
| `cto_review` | Friday 5:00 PM | CTO reviews technical progress |
| `research_and_draft_posts` | Mon/Wed/Fri 7:00 AM | Social crew researches + drafts |
| `post_tantra_progress` | Tue/Thu/Sat 9:30 AM | Director posts progress update |
| `publish_approved_linkedin_posts` | Every 2h (9AM–9PM) | Publishes n8n-approved posts |
| `linkedin_engage_feed` | Daily 8:00 AM | Engages with LinkedIn feed |
| `youtube_analytics_pull` | Daily 8:30 AM | Pulls YouTube channel stats |

---

## LinkedIn approval workflow (n8n)

Content does **not** post directly. Every LinkedIn post flows through n8n:

```
research_and_draft_posts (Celery)
  └─► LinkedInPost saved to Postgres (status: draft)
       └─► Webhook fires to n8n
            └─► n8n sends approval request to your channel/email
                 └─► You approve / edit / reject
                      └─► n8n calls back Tantra API
                           └─► publish_approved_linkedin_posts posts it live
```

Import the workflow: `bash n8n/import_workflow.sh`

---

## Agent hierarchy

```
DirectorAgent / CAIO  (qwen3:30b — strategic oversight + chat)
│
├── CMO — Chief Marketing Officer  (director tier)
│   ├── SocialCrew (CrewAI)
│   │   ├── Researcher        (Tavily web search)
│   │   ├── Content Strategist
│   │   ├── Writer
│   │   └── Publisher
│   └── Analytics review
│
├── CTO — Chief Technology Officer  (director tier)
│   └── Technical progress review
│
├── CFO — Chief Finance Officer   (planned)
├── CRO — Chief Research Officer  (planned)
└── CCO — Chief Creative Officer  (planned — Phase 3 media)
```

---

## Phase 3 roadmap — Multi-platform earning engine

Phase 3 adds autonomous income generation across platforms beyond LinkedIn. Each platform is an independent feature module:

| Platform | Mode | Planned capabilities |
|----------|------|---------------------|
| YouTube | Auto + manual | Script generation, thumbnail prompts, upload automation, analytics |
| Instagram | Auto + manual | Reels scripts, carousel posts, hashtag strategy |
| X (Twitter) | Auto + manual | Thread generation, engagement, growth loops |

**Phase 3 media stack** (local models):
- `Wan2.1-T2V-14B` — text-to-video generation
- `Wan2.1-I2V-14B` — image-to-video generation
- `CosyVoice2-0.5B` / `Kokoro 82M` — voice synthesis (voiceovers)
- `Whisper Large V3 Turbo` — transcription
- `FLUX.1-Schnell` — image generation (thumbnails, post graphics)
- `Remotion` — programmatic video assembly

**Planned Phase 3 task types** (will extend `_VALID_TASK_TYPES`):
- `youtube_script` → `youtube_publish` (upload + metadata)
- `instagram_reel` → `instagram_publish`
- `x_thread` → `x_publish`
- `image_generate` (FLUX.1 via local API)
- `video_generate` (Wan2.1 pipeline)

---

## Project structure

```
tantra-ai/
├── docker-compose.yml              # All services (Ollama, LiteLLM, Postgres, Redis, Qdrant, n8n)
├── docker-compose.nvidia.yml       # NVIDIA GPU overlay for Linux
├── docker-compose.dev.yml          # Dev overrides (hot reload, debug ports)
├── Dockerfile                      # Multi-stage: api + worker targets
├── Makefile                        # make up / make pull-models / make test
├── pyproject.toml                  # Python deps + tool config
├── alembic.ini                     # DB migration config
├── config/
│   └── litellm_config.yaml         # Model tier aliases → actual models
├── scripts/
│   ├── setup.sh                    # First-time setup
│   ├── pull_models.sh              # Download Ollama models
│   └── validate.sh                 # Validate environment + connectivity
├── n8n/
│   ├── tantra_linkedin_approval_workflow.json
│   └── import_workflow.sh
├── skills/                         # Director/agent skill prompts (SKILL.md files)
│   ├── humanizer/                  # Humanise LLM-generated text
│   ├── linkedin-human-post/        # LinkedIn post creation guidelines
│   ├── linkedin-human-comment/     # Engagement comment style
│   ├── social-researcher/          # Research + sourcing strategy
│   ├── post-approver/              # Editorial approval criteria
│   ├── tantra-build-context/       # Project context for Director grounding
│   └── ...
├── plugins/
│   └── social-linkedin/            # LinkedIn plugin (PLUGIN.md + plugin.py)
├── src/tantra/
│   ├── main.py                     # FastAPI app entry point
│   ├── cli.py                      # Typer CLI (tantra command + all subcommands)
│   ├── core/
│   │   ├── config.py               # Centralised settings (pydantic-settings)
│   │   ├── llm.py                  # LiteLLM gateway (chat / embed / stream)
│   │   └── database.py             # Async SQLAlchemy + session factory
│   ├── agents/
│   │   ├── base.py                 # TantraAgent abstract base
│   │   ├── director.py             # DirectorAgent (CAIO): plan_week, converse, get_live_context
│   │   ├── leader.py               # LeaderAgent (C-suite base)
│   │   └── worker.py               # WorkerAgent + preset factory functions
│   ├── memory/
│   │   └── manager.py              # MemoryManager + LeaderMemoryManager (Qdrant)
│   ├── rag/
│   │   └── pipeline.py             # KnowledgeBase (LlamaIndex + Qdrant)
│   ├── crews/
│   │   ├── social_crew.py          # CrewAI social crew (researcher + writer + publisher)
│   │   └── director_crew.py        # CrewAI director crew (CMO + CTO agents)
│   ├── tasks/
│   │   ├── celery_app.py           # Celery app + beat schedule
│   │   ├── director_tasks.py       # Phase 2 tasks (planning, dispatch, recovery, reviews)
│   │   └── social_tasks.py         # Phase 1 tasks (research_draft, progress_post, publish)
│   ├── db/
│   │   ├── director.py             # WeeklyPlan + AgentTask ORM models
│   │   └── social.py               # LinkedInPost + YouTubeVideo ORM models
│   ├── tools/
│   │   ├── linkedin.py             # LinkedIn API client (OAuth, post, analytics)
│   │   ├── youtube.py              # YouTube API client (search, stats, upload)
│   │   └── mcp/
│   │       └── social_mcp_server.py  # MCP-compatible HTTP server for tool access
│   ├── auth/
│   │   ├── manager.py              # Token storage + refresh logic
│   │   ├── oauth.py                # OAuth 2.0 flows
│   │   └── models.py               # OAuthToken ORM model
│   ├── api/
│   │   └── routes.py               # FastAPI routes (agent, auth, social, memory)
│   └── plugins/
│       ├── loader.py               # Plugin discovery + loading
│       └── registry.py             # Plugin registry
├── tests/
│   └── test_core.py                # Unit tests (no Docker required)
└── data/                           # Local persistent storage (gitignored)
    ├── postgres/                   # PostgreSQL data + init SQL
    ├── redis/                      # Redis persistence (RDB snapshot)
    ├── qdrant/                     # Qdrant vector storage
    └── uploads/                    # File uploads
```

---

## Redis key namespaces

| DB | Namespace | Contents |
|----|-----------|---------|
| DB1 | Celery default | Task broker + result backend |
| DB3 | `tantra:` | App state, cooldowns, checkpoints, Director chat sessions |
| DB3 | `tantra:director:chat:session:<id>` | Chat session history + metadata |
| DB3 | `tantra:director:chat:index` | Sorted set of all sessions (score = last_activity) |
| DB3 | `tantra:cooldown:progress_post` | 24h cooldown key |
| DB3 | `tantra:checkpoint:research_draft:<task_id>` | Crew output checkpoint |

---

## Linux + NVIDIA

```bash
docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d
```

The `docker-compose.nvidia.yml` overlay configures Ollama with `NVIDIA_VISIBLE_DEVICES=all` and the `nvidia` runtime. Requires [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html).

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
