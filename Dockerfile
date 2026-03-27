# =============================================================================
# Tantra AI — Multi-stage Dockerfile
# Targets: api (FastAPI), worker (Celery)
# Platform: linux/arm64 (Apple Silicon) + linux/amd64 (Linux/NVIDIA)
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Python dependencies builder — uses a venv for reliable dep resolution
# The --prefix=/install approach misses transitive deps; venv is the standard fix.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# System build deps (psycopg2, libmagic, OCR, media processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libmagic-dev \
    poppler-utils \
    tesseract-ocr \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtualenv — all deps install here, fully self-contained
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

RUN pip install --upgrade pip

# Copy project definition and install the tantra-ai package + all its deps
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install everything declared in pyproject.toml (resolves full dep tree)
RUN pip install .

# ---------------------------------------------------------------------------
# Stage 2: Base runtime image
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS base

# System runtime deps only (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the complete virtualenv from builder — every dep is present
COPY --from=builder /venv /venv

WORKDIR /app

# Create non-root user
RUN useradd -m -u 1000 tantra && chown -R tantra:tantra /app
USER tantra

# Use the venv Python + packages
ENV PATH="/venv/bin:$PATH"
ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV VIRTUAL_ENV=/venv


# ---------------------------------------------------------------------------
# Stage 3: API target (FastAPI + Uvicorn)
# ---------------------------------------------------------------------------
FROM base AS api

COPY --chown=tantra:tantra src/ ./src/
COPY --chown=tantra:tantra config/ ./config/

EXPOSE 8000

CMD ["uvicorn", "tantra.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--log-level", "info"]


# ---------------------------------------------------------------------------
# Stage 4: Worker target (Celery)
# ---------------------------------------------------------------------------
FROM base AS worker

COPY --chown=tantra:tantra src/ ./src/
COPY --chown=tantra:tantra config/ ./config/

CMD ["celery", "-A", "tantra.tasks.celery_app", "worker", \
     "--loglevel=info", "--concurrency=4", \
     "--queues=default,social,agents,scheduled"]
