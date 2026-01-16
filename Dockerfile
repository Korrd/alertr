# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    lvm2 \
    smartmontools \
    util-linux \
    systemd \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for dashboard mode
RUN useradd --create-home --shell /bin/bash hsm

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml LICENSE README.md ./
COPY homelab_storage_monitor/ ./homelab_storage_monitor/

# Install Python package
RUN pip install --no-cache-dir -e .

# Create data directory
RUN mkdir -p /var/lib/hsm && chown hsm:hsm /var/lib/hsm

# Create config directory
RUN mkdir -p /config

# Default environment
ENV TZ=UTC
ENV HSM_DB_PATH=/var/lib/hsm/hsm.sqlite

# Expose dashboard port
EXPOSE 8088

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8088/health || exit 1

# Default entrypoint
ENTRYPOINT ["hsm"]

# Default command (show help)
CMD ["--help"]
