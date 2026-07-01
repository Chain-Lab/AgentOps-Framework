#!/usr/bin/env bash
# ============================================================================
# Phase 64 — Kubernetes manifest smoke test
# ============================================================================
# Validates all K8s YAML manifests are parseable and structurally correct.
# Uses kubectl dry-run when available, PyYAML fallback otherwise.
# Does NOT require a running Kubernetes cluster.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
K8S_DIR="${PROJECT_ROOT}/deploy/kubernetes"

echo "=== Kubernetes manifest smoke test ==="

# ---- Required manifest files ----
REQUIRED_MANIFESTS=(
    "namespace.yaml"
    "serviceaccount.yaml"
    "configmap.yaml"
    "secret.yaml"
    "persistent-volume-claim.yaml"
    "deployment.yaml"
    "service.yaml"
    "networkpolicy.yaml"
    "poddisruptionbudget.yaml"
    "job-validate-config.yaml"
)

for manifest in "${REQUIRED_MANIFESTS[@]}"; do
    path="${K8S_DIR}/${manifest}"
    if [[ ! -f "${path}" ]]; then
        echo "FAIL: Missing manifest: ${manifest}"
        exit 1
    fi
done
echo "PASS: All ${#REQUIRED_MANIFESTS} required manifests exist"

# ---- YAML validation ----
if python3 -c "import yaml" 2>/dev/null; then
    echo "PASS: PyYAML available — validating YAML syntax"
    for manifest in "${REQUIRED_MANIFESTS[@]}"; do
        path="${K8S_DIR}/${manifest}"
        python3 -c "
import yaml, sys
try:
    with open('${path}') as f:
        yaml.safe_load(f)
    print('  PASS: ${manifest} is valid YAML')
except Exception as e:
    print(f'  FAIL: ${manifest} — {e}')
    sys.exit(1)
"
    done
else
    echo "WARN: PyYAML not available — skipping YAML syntax validation"
fi

# ---- kubectl dry-run (if available) ----
if command -v kubectl &>/dev/null; then
    echo "PASS: kubectl available — trying dry-run validation"
    for manifest in "${REQUIRED_MANIFESTS[@]}"; do
        path="${K8S_DIR}/${manifest}"
        if ! kubectl apply --dry-run=client -f "${path}" &>/dev/null; then
            echo "FAIL: kubectl dry-run failed for ${manifest}"
            exit 1
        fi
        echo "  PASS: ${manifest} passes kubectl dry-run"
    done
else
    echo "WARN: kubectl not available — skipping dry-run validation"
fi

# ---- Key structural checks ----
DEPLOYMENT="${K8S_DIR}/deployment.yaml"

if ! grep -q "replicas: 1" "${DEPLOYMENT}"; then
    echo "FAIL: Deployment must have replicas: 1"
    exit 1
fi
echo "PASS: Deployment has replicas: 1"

if ! grep -q "livenessProbe" "${DEPLOYMENT}"; then
    echo "FAIL: Deployment must have livenessProbe"
    exit 1
fi
echo "PASS: Deployment has livenessProbe"

if ! grep -q "readinessProbe" "${DEPLOYMENT}"; then
    echo "FAIL: Deployment must have readinessProbe"
    exit 1
fi
echo "PASS: Deployment has readinessProbe"

if ! grep -q "preStop" "${DEPLOYMENT}"; then
    echo "FAIL: Deployment must have preStop hook"
    exit 1
fi
echo "PASS: Deployment has preStop hook"

if ! grep -q "runAsNonRoot" "${DEPLOYMENT}"; then
    echo "FAIL: Deployment must use runAsNonRoot"
    exit 1
fi
echo "PASS: Deployment uses runAsNonRoot"

if ! grep -q 'mountPath: /data' "${DEPLOYMENT}"; then
    echo "FAIL: Deployment must mount /data"
    exit 1
fi
echo "PASS: Deployment mounts /data"

echo ""
echo "=== Kubernetes manifest smoke test: ALL PASSED ==="
