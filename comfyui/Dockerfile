# =============================================================================
# ComfyUI — Dockerfile
# तंत्र  ·  Built from source using official PyTorch + CUDA base
#
# Base: pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime
#   - PyTorch 2.6 has full Blackwell (RTX 5000 series) support
#   - CUDA 12.6 required for RTX 5070 Ti
#
# Model layout inside container (bind-mounted from ./data/comfyui/models/):
#   /app/models/diffusion_models/flux1-dev.safetensors   (Flux UNET fp8)
#   /app/models/vae/ae.safetensors                       (Flux VAE)
#   /app/models/clip/t5xxl_fp8_e4m3fn.safetensors        (T5 text encoder fp8)
#   /app/models/clip/clip_l.safetensors                  (CLIP-L text encoder)
#
# Build: docker compose build comfyui   (~3-5 min, clones ComfyUI from GitHub)
# Run:   docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d comfyui
# GUI:   http://localhost:8188
# API:   http://localhost:8188/system_stats
# =============================================================================

FROM pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime

# ── System dependencies ────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ── Clone ComfyUI ──────────────────────────────────────────────────────────
WORKDIR /app
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git .

# ── Python dependencies ────────────────────────────────────────────────────
# ComfyUI requirements.txt installs torch/torchvision/torchaudio via its own
# logic, but since pytorch is already in the base image we just install the
# non-torch deps. The --extra-index-url is still fine — pip will skip any
# package that is already satisfied.
RUN pip install --no-cache-dir -r requirements.txt

# ── Working dirs ───────────────────────────────────────────────────────────
# These are overridden by bind mounts at runtime, but creating them ensures
# ComfyUI starts cleanly even without the bind mounts attached.
RUN mkdir -p \
    models/diffusion_models \
    models/unet \
    models/vae \
    models/clip \
    models/loras \
    models/controlnet \
    output \
    custom_nodes \
    input \
    temp

# ── Port ───────────────────────────────────────────────────────────────────
EXPOSE 8188

# ── Startup ────────────────────────────────────────────────────────────────
# --listen 0.0.0.0  → accept connections from tantra-net (not just localhost)
# --port 8188       → explicit port
# --cuda-malloc     → use CUDA unified memory manager (helps with large Flux
#                     models on 16 GB VRAM by reducing fragmentation)
CMD ["python", "main.py", \
     "--listen", "0.0.0.0", \
     "--port", "8188", \
     "--cuda-malloc"]
