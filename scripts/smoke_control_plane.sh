#!/usr/bin/env bash
# ============================================================================
# Phase 64 — Control plane smoke test
# ============================================================================
# Runs Phase 63 control plane unit tests to verify the persistent
# control plane store, approval store, audit store, and daemon
# control command integration still work correctly.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Project root is the parent of the worktree
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "=== Control plane smoke test ==="

# Find pytest
if command -v pytest &>/dev/null; then
    PYTEST="pytest"
elif [[ -x "${PROJECT_ROOT}/.venv/bin/pytest" ]]; then
    PYTEST="${PROJECT_ROOT}/.venv/bin/pytest"
elif [[ -x "$(dirname "${PROJECT_ROOT}")/.venv/bin/pytest" ]]; then
    PYTEST="$(dirname "${PROJECT_ROOT}")/.venv/bin/pytest"
elif [[ -n "${VIRTUAL_ENV:-}" ]] && [[ -x "${VIRTUAL_ENV}/bin/pytest" ]]; then
    PYTEST="${VIRTUAL_ENV}/bin/pytest"
else
    echo "FAIL: pytest not found"
    exit 1
fi

# Run Phase 63 control plane tests
echo "Running Phase 63 control plane tests..."
if ! "${PYTEST}" tests/unit/test_phase63_control_plane_store.py \
    tests/unit/test_phase63_approval_store.py \
    tests/unit/test_phase63_audit_store.py \
    tests/unit/test_phase63_control_server.py \
    tests/unit/test_phase63_daemon_control_commands.py \
    -q --tb=short 2>&1; then
    echo "FAIL: Phase 63 control plane tests failed"
    exit 1
fi

echo ""
echo "=== Control plane smoke test: ALL PASSED ==="
