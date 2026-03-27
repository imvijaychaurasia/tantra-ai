#!/usr/bin/env bash
# =============================================================================
# Tantra AI — First-time setup script
# तंत्र  ·  Run this once after cloning the repo
# =============================================================================
set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

TANTRA_DIR="$(cd "$(dirname "$0")/.." && pwd)"

info()    { echo -e "${CYAN}[tantra]${RESET} $*"; }
success() { echo -e "${GREEN}[✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }
error()   { echo -e "${RED}[✗]${RESET} $*"; exit 1; }

echo ""
echo -e "${CYAN}=================================================================${RESET}"
echo -e "${CYAN}   तंत्र (Tantra AI) — Setup Script                             ${RESET}"
echo -e "${CYAN}=================================================================${RESET}"
echo ""

cd "$TANTRA_DIR"

# ---------------------------------------------------------------------------
# 1. Check prerequisites
# ---------------------------------------------------------------------------
info "Checking prerequisites..."

command -v docker >/dev/null 2>&1 || error "Docker is not installed. Get it at https://docker.com"
command -v docker compose >/dev/null 2>&1 || \
    docker compose version >/dev/null 2>&1 || \
    error "Docker Compose v2 not found. Update Docker Desktop."
command -v python3 >/dev/null 2>&1 || error "Python 3.11–3.13 is required (not found)"

# ---------------------------------------------------------------------------
# Auto-select a compatible Python (>=3.11, <3.14).
# Python 3.14 is not yet supported by crewai-tools and several other deps.
# ---------------------------------------------------------------------------
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        _maj=$("$candidate" -c 'import sys; print(sys.version_info.major)')
        _min=$("$candidate" -c 'import sys; print(sys.version_info.minor)')
        if [ "$_maj" -eq 3 ] && [ "$_min" -ge 11 ] && [ "$_min" -le 13 ]; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo ""
    error "No compatible Python found (need 3.11–3.13, not 3.14+).
  Install Python 3.13 via:  brew install python@3.13
  Then re-run this script."
fi

PYTHON_VERSION=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
success "Docker ✓   Python $PYTHON_VERSION ✓  (using $PYTHON_BIN)"

# ---------------------------------------------------------------------------
# 2. Create .env from template
# ---------------------------------------------------------------------------
info "Creating .env file..."
if [ -f .env ]; then
    warn ".env already exists — skipping (delete it to regenerate)"
else
    cp .env.example .env
    # Generate a random SECRET_KEY
    SECRET=$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(32))')
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/change-me-to-a-long-random-string-in-production/$SECRET/" .env
        sed -i '' "s/change-me-in-production/$SECRET/" .env
    else
        sed -i "s/change-me-to-a-long-random-string-in-production/$SECRET/" .env
        sed -i "s/change-me-in-production/$SECRET/" .env
    fi
    success ".env created with random SECRET_KEY"
    warn "→ Open .env and fill in your API keys (ANTHROPIC_API_KEY, LINKEDIN_CLIENT_ID, etc.)"
fi

# ---------------------------------------------------------------------------
# 3. Create Python virtual environment
# ---------------------------------------------------------------------------
info "Setting up Python virtual environment (using $PYTHON_BIN)..."
if [ -d .venv ]; then
    warn ".venv already exists — skipping (delete .venv/ to rebuild)"
else
    "$PYTHON_BIN" -m venv .venv
    success "Virtual environment created at .venv/  (Python $PYTHON_VERSION)"
fi

info "Installing Python dependencies (this may take a few minutes)..."
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -e "." --quiet
success "Python dependencies installed"

# ---------------------------------------------------------------------------
# 4. Create required data directories
# ---------------------------------------------------------------------------
info "Creating data directories..."
mkdir -p data/{models,qdrant,postgres,redis,uploads}
# Create placeholder init SQL for Postgres
if [ ! -f data/postgres/init.sql ]; then
    cat > data/postgres/init.sql << 'SQL'
-- Tantra AI — Postgres initialisation
CREATE SCHEMA IF NOT EXISTS n8n;
CREATE SCHEMA IF NOT EXISTS tantra;
SQL
fi
success "Data directories ready"

# ---------------------------------------------------------------------------
# 5. Start core services
# ---------------------------------------------------------------------------
info "Starting core Docker services (Ollama, LiteLLM, Postgres, Redis, Qdrant)..."
docker compose up -d ollama litellm postgres redis qdrant

info "Waiting for services to be healthy..."
sleep 10

# Check health
for service in postgres redis qdrant litellm; do
    STATUS=$(docker compose ps "$service" --format "{{.Health}}" 2>/dev/null || echo "unknown")
    if [[ "$STATUS" == "healthy" ]] || [[ "$STATUS" == "" ]]; then
        success "$service is running"
    else
        warn "$service status: $STATUS (may still be starting)"
    fi
done

# ---------------------------------------------------------------------------
# 6. Done
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}=================================================================${RESET}"
echo -e "${GREEN}   Tantra AI setup complete!                                      ${RESET}"
echo -e "${GREEN}=================================================================${RESET}"
echo ""
echo "  Next steps:"
echo "  1. Edit .env and add your API keys"
echo "  2. Run:  make pull-models     ← pull Ollama models (~40 GB)"
echo "  3. Run:  make up-all          ← start all services"
echo "  4. Open: http://localhost:3000  (Open WebUI)"
echo "           http://localhost:8000/docs  (Tantra API)"
echo "           http://localhost:5678  (n8n workflows)"
echo ""
echo -e "  Activate Python env:  ${CYAN}source .venv/bin/activate${RESET}"
echo ""
