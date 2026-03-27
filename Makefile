# =============================================================================
# Tantra AI — Makefile
# तंत्र  ·  Every command you need
# =============================================================================
# Usage:  make <target>     (e.g. make up, make logs, make test)
#         make help         — show all targets
# =============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help
.PHONY: help up up-core up-dev up-all up-nvidia down down-volumes restart \
        build push logs logs-api logs-worker ps \
        setup install install-dev pull-models \
        db-init db-migrate db-revision db-shell db-reset \
        redis-cli qdrant-ui webui n8n api-docs flower terminal \
        lint format typecheck test test-cov test-fast \
        validate clean nuke \
        create-admin

CYAN  := \033[0;36m
GREEN := \033[0;32m
RESET := \033[0m

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
help: ## Show all available targets
	@echo ""
	@echo "  $(CYAN)Tantra AI (तंत्र) — Make targets$(RESET)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ { printf "  $(CYAN)%-26s$(RESET) %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""

# ---------------------------------------------------------------------------
# Docker — starting services
# ---------------------------------------------------------------------------

up-core: ## Start infrastructure only (Postgres, Redis, Qdrant, Ollama, LiteLLM)
	docker compose up -d postgres redis qdrant ollama litellm
	@echo ""
	@echo "  $(GREEN)Core services starting. Run 'make pull-models' next.$(RESET)"

up: ## Start all services (standard)
	docker compose up -d
	@echo ""
	@make _open-links

up-dev: ## Start in dev mode (hot-reload, debug ports, verbose logs)
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
	@echo "  $(CYAN)Dev mode: API hot-reloads on src/ changes$(RESET)"

up-all: up ## Alias for 'make up'

up-nvidia: ## Start with NVIDIA GPU support (Linux only)
	docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d

_open-links:
	@echo "  $(GREEN)Services started:$(RESET)"
	@echo "   Chat UI:     http://localhost:3000"
	@echo "   API docs:    http://localhost:8000/docs"
	@echo "   n8n:         http://localhost:5678"
	@echo "   Shell:       http://localhost:7681"
	@echo "   Flower:      http://localhost:5555"
	@echo "   LiteLLM:     http://localhost:4000"

# ---------------------------------------------------------------------------
# Docker — stopping / cleaning
# ---------------------------------------------------------------------------

down: ## Stop containers (volumes preserved)
	docker compose down

down-volumes: ## Stop containers AND delete all data volumes (DESTRUCTIVE)
	@echo "⚠️  Deleting all local data in 5 seconds. Ctrl+C to abort."
	@sleep 5
	docker compose down -v

restart: ## Restart all containers
	docker compose restart

restart-api: ## Restart tantra-api only (fast dev cycle)
	docker compose restart tantra-api

build: ## Build custom images (tantra-api, celery-worker)
	docker compose build --no-cache

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

logs: ## Tail logs from all services
	docker compose logs -f --tail=50

logs-api: ## Tail tantra-api logs
	docker compose logs -f tantra-api

logs-worker: ## Tail celery worker logs
	docker compose logs -f celery-worker

logs-beat: ## Tail celery beat logs
	docker compose logs -f celery-beat

ps: ## Show running containers with health status
	docker compose ps

# ---------------------------------------------------------------------------
# First-time setup
# ---------------------------------------------------------------------------

setup: ## Full first-time setup (run this once after cloning)
	@bash scripts/setup.sh

install: ## Install Python dependencies into .venv
	@test -d .venv || python3 -m venv .venv
	.venv/bin/pip install --upgrade pip --quiet
	.venv/bin/pip install -e "." --quiet
	@echo "  $(GREEN)✓ Installed. Activate: source .venv/bin/activate$(RESET)"

install-dev: ## Install with dev extras
	@test -d .venv || python3 -m venv .venv
	.venv/bin/pip install --upgrade pip --quiet
	.venv/bin/pip install -e ".[dev]" --quiet
	.venv/bin/pre-commit install --quiet 2>/dev/null || true
	@echo "  $(GREEN)✓ Dev dependencies installed$(RESET)"

pull-models: ## Pull Ollama models (run after up-core, ~43 GB)
	@bash scripts/pull_models.sh

pull-models-small: ## Pull small models only for quick testing (~9 GB)
	@echo "  $(CYAN)Pulling small models for quick testing...$(RESET)"
	docker compose exec ollama ollama pull mistral-nemo:12b
	docker compose exec ollama ollama pull phi4:14b
	docker compose exec ollama ollama pull nomic-embed-text
	@echo "  $(GREEN)✓ Small models ready$(RESET)"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

db-init: ## Create tables (runs automatically in dev mode)
	docker compose exec tantra-api python -c \
	  "import asyncio; from tantra.core.database import init_db; asyncio.run(init_db())"

db-migrate: ## Apply all pending Alembic migrations
	docker compose exec tantra-api alembic upgrade head

db-revision: ## Create a new Alembic migration (prompts for message)
	@read -p "Migration message: " msg; \
	docker compose exec tantra-api alembic revision --autogenerate -m "$$msg"

db-shell: ## Open psql shell
	docker compose exec postgres psql -U $${POSTGRES_USER:-tantra} -d $${POSTGRES_DB:-tantra}

db-reset: ## Drop and recreate the database (DESTRUCTIVE)
	@echo "⚠️  Dropping tantra database in 3 seconds. Ctrl+C to abort."
	@sleep 3
	docker compose exec postgres psql -U $${POSTGRES_USER:-tantra} \
	  -c "DROP DATABASE IF EXISTS tantra; CREATE DATABASE tantra;"

create-admin: ## Create the first admin user (interactive)
	docker compose exec tantra-api python -c "\
import asyncio, os; \
from tantra.auth.manager import get_user_manager, get_user_db; \
from tantra.auth.schemas import UserCreate; \
print('Creating admin user...'); \
"
	@echo "  $(CYAN)Use POST /auth/register to create users via API$(RESET)"
	@echo "  $(CYAN)Or open http://localhost:8000/docs and use /auth/register$(RESET)"

# ---------------------------------------------------------------------------
# Dev tools
# ---------------------------------------------------------------------------

redis-cli: ## Open Redis CLI
	docker compose exec redis redis-cli -a $${REDIS_PASSWORD:-tantra_redis_secret}

qdrant-ui: ## Open Qdrant dashboard in browser
	open http://localhost:6333/dashboard

webui: ## Open Open WebUI (chat)
	open http://localhost:3000

n8n: ## Open n8n (visual workflows)
	open http://localhost:5678

api-docs: ## Open Tantra API docs
	open http://localhost:8000/docs

flower: ## Open Flower (Celery monitoring)
	open http://localhost:5555

terminal: ## Open web terminal
	open http://localhost:7681

litellm: ## Open LiteLLM dashboard
	open http://localhost:4000

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

validate: ## Run full health check on all services
	@bash scripts/validate.sh

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

lint: ## Run ruff linter
	.venv/bin/ruff check src/ tests/

format: ## Auto-format with ruff
	.venv/bin/ruff format src/ tests/
	.venv/bin/ruff check --fix src/ tests/

typecheck: ## Run mypy type checker
	.venv/bin/mypy src/tantra/ --ignore-missing-imports

test: ## Run test suite (no Docker required)
	.venv/bin/pytest tests/ -v --tb=short

test-fast: ## Run only fast tests (no async, no network)
	.venv/bin/pytest tests/ -v --tb=short -m "not slow"

test-cov: ## Run tests with HTML coverage report
	.venv/bin/pytest tests/ --cov=src/tantra --cov-report=html --cov-report=term-missing
	open htmlcov/index.html

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean: ## Remove Python build artifacts + caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .coverage .ruff_cache .mypy_cache dist build
	@echo "  $(GREEN)✓ Clean$(RESET)"

nuke: clean down-volumes ## Full reset: wipe code cache + Docker volumes
	@echo "  $(GREEN)✓ Full reset complete$(RESET)"
