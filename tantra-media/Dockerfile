# =============================================================================
# tantra-media — Dockerfile
# तंत्र  ·  TTS + Slide Generation + ffmpeg Video Assembly
# =============================================================================
# Platform: linux/arm64 (Apple Silicon) + linux/amd64
#
# Pipeline:
#   1. edge-tts   → narration audio (.mp3) per scene
#   2. Pillow     → styled tech-slide image (1920×1080) per scene
#   3. ffmpeg     → image + audio → scene clip (.mp4)
#   4. ffmpeg     → concat all scene clips → final video (.mp4)
# =============================================================================

FROM python:3.11-slim

# ── System dependencies ────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-liberation \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ──────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ───────────────────────────────────────────────────────
COPY app/ ./app/

# ── Output directory (bind-mounted from host at ./data/media) ──────────────
RUN mkdir -p /data/media/audio /data/media/images /data/media/clips /data/media/output

# ── Run ────────────────────────────────────────────────────────────────────
EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
