#!/usr/bin/env bash
# =============================================================================
# Tantra AI — validate.sh
# Full health check on all running services.
# Run: make validate  (after  make up)
# =============================================================================
set -uo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'
BOLD='\033[1m'

PASS=0
FAIL=0

check() {
    local name="$1"
    local url="$2"
    local expected="${3:-200}"
    local result

    result=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")

    if [ "$result" = "$expected" ] || ([ "$expected" = "200" ] && [ "$result" = "200" ]); then
        echo -e "  ${GREEN}✓${RESET} ${name}  ${CYAN}(${url})${RESET}"
        PASS=$((PASS+1))
    else
        echo -e "  ${RED}✗${RESET} ${name}  ${CYAN}(${url})${RESET}  → HTTP $result"
        FAIL=$((FAIL+1))
    fi
}

check_docker() {
    local name="$1"
    local container="$2"
    local health

    health=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "not_found")

    if [ "$health" = "healthy" ]; then
        echo -e "  ${GREEN}✓${RESET} ${name} container  ${CYAN}[healthy]${RESET}"
        PASS=$((PASS+1))
    elif [ "$health" = "not_found" ]; then
        echo -e "  ${YELLOW}?${RESET} ${name} container  ${CYAN}[not found — may not be running]${RESET}"
    else
        echo -e "  ${RED}✗${RESET} ${name} container  ${CYAN}[${health}]${RESET}"
        FAIL=$((FAIL+1))
    fi
}

echo ""
echo -e "${BOLD}${CYAN}=================================================================${RESET}"
echo -e "${BOLD}${CYAN}   Tantra AI — Service Validation                                ${RESET}"
echo -e "${BOLD}${CYAN}=================================================================${RESET}"
echo ""

# ---------------------------------------------------------------------------
echo -e "${CYAN}→ Docker container health${RESET}"
# ---------------------------------------------------------------------------
check_docker "Postgres"     "tantra-postgres"
check_docker "Redis"        "tantra-redis"
check_docker "Qdrant"       "tantra-qdrant"
check_docker "Ollama"       "tantra-ollama"
check_docker "LiteLLM"      "tantra-litellm"
check_docker "Tantra API"   "tantra-api"
check_docker "Open WebUI"   "tantra-open-webui"

echo ""

# ---------------------------------------------------------------------------
echo -e "${CYAN}→ HTTP endpoint checks${RESET}"
# ---------------------------------------------------------------------------
check "Tantra API /health"      "http://localhost:8000/health"
check "Tantra API /docs"        "http://localhost:8000/docs"
check "Tantra API /"            "http://localhost:8000/"
check "Ollama /api/tags"        "http://localhost:11434/api/tags"
check "LiteLLM /health"         "http://localhost:4000/health"
check "Qdrant /healthz"         "http://localhost:6333/healthz"
check "Open WebUI"              "http://localhost:3000"
check "n8n"                     "http://localhost:5678"
check "Flower"                  "http://localhost:5555"
check "Web Terminal (ttyd)"     "http://localhost:7681"

echo ""

# ---------------------------------------------------------------------------
echo -e "${CYAN}→ Ollama models${RESET}"
# ---------------------------------------------------------------------------
if docker exec tantra-ollama ollama list &>/dev/null; then
    MODELS=$(docker exec tantra-ollama ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')
    if [ -z "$MODELS" ]; then
        echo -e "  ${YELLOW}!${RESET} No models pulled yet — run: ${CYAN}make pull-models-small${RESET}"
    else
        echo "$MODELS" | while IFS= read -r model; do
            echo -e "  ${GREEN}✓${RESET} $model"
        done
    fi
else
    echo -e "  ${YELLOW}?${RESET} Ollama not reachable"
fi

echo ""

# ---------------------------------------------------------------------------
echo -e "${CYAN}→ Database connectivity${RESET}"
# ---------------------------------------------------------------------------
if docker exec tantra-postgres pg_isready -U tantra -d tantra &>/dev/null; then
    echo -e "  ${GREEN}✓${RESET} Postgres accepts connections"

    TABLE_COUNT=$(docker exec tantra-postgres psql -U tantra -d tantra -t \
        -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema');" \
        2>/dev/null | tr -d ' ')
    echo -e "  ${GREEN}✓${RESET} Tables created: ${TABLE_COUNT:-0}"
else
    echo -e "  ${RED}✗${RESET} Postgres not reachable"
    FAIL=$((FAIL+1))
fi

echo ""

# ---------------------------------------------------------------------------
echo -e "${CYAN}→ Auth API smoke test${RESET}"
# ---------------------------------------------------------------------------
AUTH_RESP=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST http://localhost:8000/auth/register \
    -H "Content-Type: application/json" \
    -d '{"email":"validate@test.com","password":"ValidateTest123!"}' \
    --max-time 5 2>/dev/null || echo "000")

if [ "$AUTH_RESP" = "201" ] || [ "$AUTH_RESP" = "400" ]; then
    # 400 = user already exists = API is working
    echo -e "  ${GREEN}✓${RESET} Auth /register endpoint responding (HTTP ${AUTH_RESP})"
    PASS=$((PASS+1))
else
    echo -e "  ${RED}✗${RESET} Auth /register endpoint not responding (HTTP ${AUTH_RESP})"
    FAIL=$((FAIL+1))
fi

# ---------------------------------------------------------------------------
echo ""
echo -e "${BOLD}${CYAN}=================================================================${RESET}"
echo -e "  Results: ${GREEN}${PASS} passed${RESET}  /  ${RED}${FAIL} failed${RESET}"
echo -e "${BOLD}${CYAN}=================================================================${RESET}"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo -e "  ${YELLOW}Troubleshooting:${RESET}"
    echo "   make logs        → tail all container logs"
    echo "   make ps          → show container status"
    echo "   make pull-models-small  → pull minimal models for testing"
    echo ""
    exit 1
else
    echo -e "  ${GREEN}All checks passed — Tantra AI is fully operational!${RESET}"
    echo ""
fi
