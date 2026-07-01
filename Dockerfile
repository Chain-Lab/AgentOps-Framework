# ============================================================================
# Phase 64 — Docker image hardening
# ============================================================================
# Build:
#   docker build -t agent-app:0.49.0 .
# Run:
#   docker run --rm \
#     -e AGENT_APP_CONFIG=/app/config/daemon.yaml \
#     -e AGENT_APP_CONTROL_TOKEN=dev-token \
#     -p 8080:8080 \
#     -p 8090:8090 \
#     agent-app:0.49.0
# ============================================================================

FROM python:3.12-slim AS builder

# ---- Build-time dependencies ----
RUN pip install --no-cache-dir --prefix=/install \
    "pydantic>=2.0" \
    "pyyaml>=6.0" \
    "typing-extensions>=4.0"

# ---- Runtime image ----
FROM python:3.12-slim

# Labels
LABEL maintainer="AgentOps Team"
LABEL version="0.49.0"
LABEL description="Agent App Delivery Retry Daemon"

# Security: create non-root user
RUN groupadd -r agent-app && \
    useradd -r -g agent-app -d /app -s /sbin/nologin agent-app

# Working directory
WORKDIR /app

# Copy installed dependencies from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY agent_app/ ./agent_app/
COPY pyproject.toml ./

# Create writable directories
RUN mkdir -p /app/config /app/logs /data && \
    chown -R agent-app:agent-app /app /data

# Non-root user (REQUIRED for production)
USER agent-app

# Expose ports:
#   8080 — health HTTP server (Phase 62)
#   8090 — control HTTP server (Phase 63)
EXPOSE 8080
EXPOSE 8090

# Environment defaults
ENV AGENT_APP_CONFIG=/app/config/daemon.yaml \
    AGENT_APP_CONTROL_TOKEN="" \
    AGENT_APP_CONTROL_DB=/data/control_plane.db

# Health check (uses stdlib — no curl needed)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python /app/docker/healthcheck.sh || exit 1

# Default command
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["daemon", "serve"]
