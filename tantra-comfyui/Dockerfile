# =============================================================================
# tantra-comfyui — ComfyUI for Flux.1-dev AI background generation
# तंत्र  ·  Phase 3d image generation service
# =============================================================================
# Base: nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04
#
# Why NOT pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime:
#   PyTorch cu124 is compiled for sm_50–sm_90 only. RTX 5070 Ti is sm_120
#   (Blackwell). Running any CUDA kernel crashes with:
#     "CUDA error: no kernel image is available for execution on the device"
#   There is no workaround — sm_120 kernels simply don't exist in cu124.
#
# Fix: use CUDA 12.8 base + install PyTorch from the cu128 wheel index.
#   PyTorch ≥ 2.7 with cu128 includes compiled kernels for sm_120.
#   This is the minimum requirement for RTX 5070 Ti (Blackwell) GPU usage.
#
# ComfyUI API is exposed on port 8188.
# Models are mounted from host at /root/ComfyUI/models — no weights baked in.
#
# Build: docker compose build comfyui       (~10-15 min first time)
# Run:   docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d comfyui
# =============================================================================

FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

# ── System dependencies ────────────────────────────────────────────────────
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-dev \
    python3-pip \
    git \
    wget \
    curl \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Make python3.11 the default python/pip
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
 && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
 && python -m pip install --upgrade pip

# ── PyTorch with CUDA 12.8 (sm_120 / Blackwell support) ───────────────────
# cu128 wheels include compiled kernels for sm_120 (RTX 5070 Ti / Blackwell).
# This is the critical difference from cu124 which had no sm_120 kernels.
RUN pip install --no-cache-dir \
    torch \
    torchvision \
    torchaudio \
    --index-url https://download.pytorch.org/whl/cu128

# ── Clone ComfyUI ──────────────────────────────────────────────────────────
WORKDIR /root
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git

WORKDIR /root/ComfyUI

# ── Install ComfyUI Python dependencies ───────────────────────────────────
# Use ComfyUI's own requirements.txt to pick up all current deps.
# Torch is already installed above (cu128), so requirements.txt will skip it
# (it checks if torch is importable before reinstalling).
# The --extra-index-url ensures pip can resolve any remaining torch deps.
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cu128 \
    -r requirements.txt

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
