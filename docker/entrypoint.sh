#!/bin/bash
# ============================================================================
# Phase 64 — Docker entrypoint
# ============================================================================
set -euo pipefail

# ---- Config validation ----
CONFIG="${AGENT_APP_CONFIG:-/app/config/daemon.yaml}"

if [[ ! -f "$CONFIG" ]]; then
    echo "[entrypoint] ERROR: config file not found: $CONFIG" >&2
    echo "[entrypoint] Set AGENT_APP_CONFIG env var or mount config to /app/config/daemon.yaml" >&2
    exit 1
fi

# ---- Version info ----
echo "[entrypoint] Agent App Delivery Retry Daemon"
echo "[entrypoint] Version: 0.49.0"
echo "[entrypoint] Config: $CONFIG"
echo "[entrypoint] Control DB: ${AGENT_APP_CONTROL_DB:-/data/control_plane.db}"

# ---- Control token warning ----
if [[ -z "${AGENT_APP_CONTROL_TOKEN:-}" ]]; then
    echo "[entrypoint] WARNING: AGENT_APP_CONTROL_TOKEN is empty — control HTTP API is unauthenticated" >&2
fi

# ---- Execute command ----
# If no args provided, use default CMD
if [[ $# -eq 0 ]]; then
    set -- daemon serve
fi

echo "[entrypoint] Executing: $*"
exec "$@"
