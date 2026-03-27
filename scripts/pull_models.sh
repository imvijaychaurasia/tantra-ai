#!/usr/bin/env bash
# =============================================================================
# Tantra AI — Pull Ollama models
# तंत्र  ·  Downloads all required local models
# =============================================================================
# Run this AFTER 'make up-core' (Ollama must be running)
#
# Total download size: ~40-50 GB
# Disk required:      ~60 GB (with overhead)
# Time:               30-90 min depending on connection
# =============================================================================
set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RESET='\033[0m'

# Always use localhost when running from host (never inherit OLLAMA_HOST from docker env)
OLLAMA_HOST="http://localhost:11434"

info()    { echo -e "${CYAN}[tantra]${RESET} $*"; }
success() { echo -e "${GREEN}[✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }

pull_model() {
    local name="$1"
    local tag="$2"
    local desc="$3"
    local size="$4"
    info "Pulling ${name}:${tag}  (${desc} — ~${size})..."
    if docker compose exec ollama ollama pull "${name}:${tag}"; then
        success "${name}:${tag} ready"
    else
        warn "Failed to pull ${name}:${tag} — skipping"
    fi
}

echo ""
echo -e "${CYAN}=================================================================${RESET}"
echo -e "${CYAN}   Tantra AI — Pulling Ollama Models                             ${RESET}"
echo -e "${CYAN}=================================================================${RESET}"
echo ""

# Verify Ollama is running (use docker exec — avoids curl dependency and env var issues)
if ! docker compose exec ollama ollama list > /dev/null 2>&1; then
    echo "Ollama container is not running or not ready."
    echo "Run 'make up-nvidia' (or 'make up') first, then retry."
    exit 1
fi

info "Ollama is running"
echo ""

# ---------------------------------------------------------------------------
# Required models (in order of importance)
# ---------------------------------------------------------------------------

# DIRECTOR tier — Llama 3.3 70B (primary reasoning model)
pull_model "llama3.3"         "70b"   "Director tier / strategic reasoning"  "~43 GB"

# MANAGER tier — Qwen 2.5 72B (instruction following, multilingual)
pull_model "qwen2.5"          "72b"   "Manager tier / complex tasks"          "~44 GB"

# WORKER tier — Phi-4 14B (efficient single-task)
pull_model "phi4"             "14b"   "Worker tier / focused execution"       "~9 GB"

# CODER tier — DeepSeek Coder V2 16B
pull_model "deepseek-coder-v2" "16b"  "Code generation / infra scripts"       "~9 GB"

# FAST tier — Mistral Nemo 12B (low latency)
pull_model "mistral-nemo"     "12b"   "Fast tier / classify & route"          "~7 GB"

# EMBEDDER — nomic-embed-text (RAG + semantic memory)
pull_model "nomic-embed-text" "latest" "Embeddings for Qdrant / mem0"        "~274 MB"

# ---------------------------------------------------------------------------
# Optional: smaller models for resource-constrained setups
# ---------------------------------------------------------------------------
# Uncomment if you're on < 32 GB RAM:
#
# pull_model "llama3.2"   "3b"    "Lightweight worker fallback"  "~2 GB"
# pull_model "phi3"       "mini"  "Tiny fast worker"             "~2 GB"
# pull_model "qwen2.5"    "7b"    "Lightweight manager"          "~5 GB"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}=================================================================${RESET}"
echo -e "${GREEN}   Model pull complete!                                           ${RESET}"
echo -e "${GREEN}=================================================================${RESET}"
echo ""
info "Installed models:"
docker compose exec ollama ollama list
echo ""
info "LiteLLM gateway test (director alias):"
curl -sf -X POST "${OLLAMA_HOST}/api/chat" \
    -H "Content-Type: application/json" \
    -d '{"model":"llama3.3:70b","messages":[{"role":"user","content":"Say hi in one word"}],"stream":false}' \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(' ✓ Model response:', d['message']['content'][:80])" \
    2>/dev/null || warn "Quick test failed — models may still be loading"
echo ""
