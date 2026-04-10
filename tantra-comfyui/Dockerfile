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
# PyTorch is already installed in the base image — skip it from requirements
RUN pip install --no-cache-dir \
    aiohttp \
    einops \
    transformers>=4.28.1 \
    safetensors>=0.4.2 \
    kornia>=0.7.1 \
    torchsde \
    scipy \
    tqdm \
    psutil \
    pillow \
    spandrel

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
