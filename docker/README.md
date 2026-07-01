# Docker Deployment

This directory contains Docker-related artifacts for running the Agent App Delivery Retry Daemon in containers.

## Files

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage production image (python:3.12-slim, non-root user) |
| `.dockerignore` | Excludes `.git`, `.venv`, `__pycache__`, local DBs, logs |
| `entrypoint.sh` | Container entrypoint: config validation, version info, command dispatch |
| `healthcheck.sh` | Docker HEALTHCHECK using Python stdlib `urllib` (no curl) |

## Building

```bash
docker build -t agent-app:0.49.0 .
```

## Running

```bash
docker run --rm \
  -e AGENT_APP_CONFIG=/app/config/daemon.yaml \
  -e AGENT_APP_CONTROL_TOKEN=dev-token \
  -p 8080:8080 \
  -p 8090:8090 \
  -v $(pwd)/config:/app/config:ro \
  -v agent-app-data:/data \
  agent-app:0.49.0
```

## Ports

| Port | Purpose | Phase |
|------|---------|-------|
| 8080 | Health HTTP server (`/health`, `/ready`) | 62 |
| 8090 | Control HTTP server (`/control/commands`, `/approvals`, `/audit/events`) | 63 |

## Volumes

| Path | Purpose |
|------|---------|
| `/app/config` | Daemon YAML configuration (read-only recommended) |
| `/data` | SQLite control plane DB, writable |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AGENT_APP_CONFIG` | No | `/app/config/daemon.yaml` | Path to daemon YAML config |
| `AGENT_APP_CONTROL_TOKEN` | No | `""` | Bearer token for control API |
| `AGENT_APP_CONTROL_DB` | No | `/data/control_plane.db` | SQLite control plane DB path |

## Security

- Runs as non-root user `agent-app` (UID/GID created at build time)
- Read-only root filesystem compatible (all writes go to `/data` or `/app/logs`)
- No secrets baked into image
- Health check uses Python stdlib only

## Smoke Test

```bash
docker run --rm \
  -e AGENT_APP_CONFIG=/app/config/daemon.yaml \
  agent-app:0.49.0 --help
```
