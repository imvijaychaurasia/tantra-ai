# =============================================================================
# tantra-comfyui — ComfyUI for Flux.1-dev AI background generation
# तंत्र  ·  Phase 3d image generation service
# =============================================================================
# Base: official PyTorch image with CUDA 12.4 + cuDNN 9
#   Works on RTX 50xx (Blackwell) via CUDA driver compatibility layer
#   Requires NVIDIA driver >= 550 on host (already needed for Ollama)
#
# ComfyUI API is exposed on port 8188.
# Models are mounted from host at /root/ComfyUI/models — no weights baked in.
#
# Build: docker compose build comfyui       (~5-10 min, downloads ~8 GB PyTorch)
# Run:   docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d comfyui
# =============================================================================

FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

# ── System dependencies ────────────────────────────────────────────────────
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    wget \
    curl \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ── Clone ComfyUI ──────────────────────────────────────────────────────────
WORKDIR /root
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git

WORKDIR /root/ComfyUI

# ── Install ComfyUI Python dependencies ───────────────────────────────────
# Use ComfyUI's own requirements.txt so we automatically pick up all current
# and future deps (e.g. sqlalchemy, which was added in early 2026).
# PyTorch / torchvision lines in requirements.txt are skipped because they're
# already provided by the base image — we pass --extra-index-url as a no-op
# safety net but the base image's torch takes precedence.
RUN pip install --no-cache-dir -r requirements.txt

# ── Directories (bind-mounted from host at runtime) ───────────────────────
RUN mkdir -p \
    /root/ComfyUI/models/checkpoints \
    /root/ComfyUI/models/diffusion_models \
    /root/ComfyUI/models/vae \
    /root/ComfyUI/models/clip \
    /root/ComfyUI/models/loras \
    /root/ComfyUI/output \
    /root/ComfyUI/custom_nodes

# ── Port ───────────────────────────────────────────────────────────────────
EXPOSE 8188

# ── Volumes (overridden by docker-compose bind mounts) ─────────────────────
VOLUME ["/root/ComfyUI/models", "/root/ComfyUI/output", "/root/ComfyUI/custom_nodes"]

# ── Startup ────────────────────────────────────────────────────────────────
# CLI_ARGS is passed via docker-compose environment — supports --lowvram etc.
ENV CLI_ARGS="--listen 0.0.0.0 --port 8188"

CMD ["sh", "-c", "python main.py ${CLI_ARGS}"]
