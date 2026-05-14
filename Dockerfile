# ──────────────────────────────────────────────────────────────
# Multi-stage Dockerfile for Premium Bot
#
# Stage 1: "builder"  — install deps + compile C extensions
# Stage 2: "runtime"  — minimal image with only what's needed
#
# This produces a final image ~60% smaller than a single-stage
# build because gcc, headers, and pip cache are discarded.
# ──────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════
#  STAGE 1 — Builder
# ═══════════════════════════════════════════════════════════════
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build-time system dependencies (gcc for C extensions,
# libpq-dev for asyncpg's Cython compilation)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a virtual env so we can
# copy ONLY the venv to the runtime stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt


# ═══════════════════════════════════════════════════════════════
#  STAGE 2 — Runtime (minimal)
# ═══════════════════════════════════════════════════════════════
FROM python:3.11-slim AS runtime

# Labels for container metadata
LABEL maintainer="Premium Bot Team" \
      description="Production Telegram Bot — Aiogram 3.x" \
      version="1.0.0"

# Ensure the venv's bin is first on PATH
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install ONLY the runtime library for PostgreSQL (no headers/gcc)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq5 \
        curl && \
    rm -rf /var/lib/apt/lists/*

# Copy the pre-built virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY . .

# Create a non-root user for security
RUN groupadd --gid 1000 botuser && \
    useradd --uid 1000 --gid botuser --shell /bin/bash --create-home botuser && \
    chown -R botuser:botuser /app

USER botuser

# Expose the webhook port (used by aiohttp inside the bot)
EXPOSE 8443

# Health check — verifies the webhook server is responding
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8443}/health || exit 1

# Run the bot
CMD ["python", "-m", "bot"]
