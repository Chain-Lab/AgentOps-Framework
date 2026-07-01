#!/usr/bin/env bash
# ============================================================================
# Phase 64 — Docker smoke test
# ============================================================================
# Validates Dockerfile, .dockerignore, and entrypoint behavior.
# Does NOT require a running Docker daemon (static checks only).
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== Docker smoke test ==="

# ---- Dockerfile checks ----
DOCKERFILE="${PROJECT_ROOT}/Dockerfile"
if [[ ! -f "${DOCKERFILE}" ]]; then
    echo "FAIL: Dockerfile not found at ${DOCKERFILE}"
    exit 1
fi
echo "PASS: Dockerfile exists"

if ! grep -q "USER agent-app" "${DOCKERFILE}"; then
    echo "FAIL: Dockerfile must use non-root USER"
    exit 1
fi
echo "PASS: Dockerfile uses non-root user"

if ! grep -q "HEALTHCHECK" "${DOCKERFILE}"; then
    echo "FAIL: Dockerfile must contain HEALTHCHECK"
    exit 1
fi
echo "PASS: Dockerfile has HEALTHCHECK"

if ! grep -q "EXPOSE 8080" "${DOCKERFILE}"; then
    echo "FAIL: Dockerfile must EXPOSE 8080"
    exit 1
fi
echo "PASS: Dockerfile exposes port 8080"

if ! grep -q "EXPOSE 8090" "${DOCKERFILE}"; then
    echo "FAIL: Dockerfile must EXPOSE 8090"
    exit 1
fi
echo "PASS: Dockerfile exposes port 8090"

# ---- .dockerignore checks ----
DOCKERIGNORE="${PROJECT_ROOT}/.dockerignore"
if [[ ! -f "${DOCKERIGNORE}" ]]; then
    echo "FAIL: .dockerignore not found"
    exit 1
fi
echo "PASS: .dockerignore exists"

for pattern in ".git" ".venv" "__pycache__"; do
    if ! grep -q "^${pattern}" "${DOCKERIGNORE}"; then
        echo "FAIL: .dockerignore must exclude ${pattern}"
        exit 1
    fi
done
echo "PASS: .dockerignore excludes .git, .venv, __pycache__"

# ---- entrypoint.sh checks ----
ENTRYPOINT="${PROJECT_ROOT}/docker/entrypoint.sh"
if [[ ! -f "${ENTRYPOINT}" ]]; then
    echo "FAIL: entrypoint.sh not found"
    exit 1
fi
echo "PASS: entrypoint.sh exists"

if [[ ! -x "${ENTRYPOINT}" ]]; then
    echo "FAIL: entrypoint.sh must be executable"
    exit 1
fi
echo "PASS: entrypoint.sh is executable"

if ! grep -q 'set -e' "${ENTRYPOINT}"; then
    echo "FAIL: entrypoint.sh must use 'set -e'"
    exit 1
fi
echo "PASS: entrypoint.sh uses 'set -e'"

# ---- healthcheck.sh checks ----
HEALTHCHECK="${PROJECT_ROOT}/docker/healthcheck.sh"
if [[ ! -f "${HEALTHCHECK}" ]]; then
    echo "FAIL: healthcheck.sh not found"
    exit 1
fi
echo "PASS: healthcheck.sh exists"

if [[ ! -x "${HEALTHCHECK}" ]]; then
    echo "FAIL: healthcheck.sh must be executable"
    exit 1
fi
echo "PASS: healthcheck.sh is executable"

if ! grep -q '/health' "${HEALTHCHECK}"; then
    echo "FAIL: healthcheck.sh must call /health endpoint"
    exit 1
fi
echo "PASS: healthcheck.sh calls /health"

echo ""
echo "=== Docker smoke test: ALL PASSED ==="
