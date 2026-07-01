#!/bin/bash
# ============================================================================
# Phase 64 — Docker health check
# ============================================================================
# Calls /health on the local health HTTP port.
# Returns 0 if healthy, 1 otherwise.
# Uses Python stdlib only — no curl/wget dependency.
# Does NOT send the control token (avoids leaking credentials in logs).

set -euo pipefail

PORT="${AGENT_APP_HEALTH_PORT:-8080}"
HOST="${AGENT_APP_HEALTH_HOST:-127.0.0.1}"
URL="http://${HOST}:${PORT}/health"

python3 - "$URL" <<'PYEOF'
import sys
import urllib.request

url = sys.argv[1]
try:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=3) as resp:
        if resp.status == 200:
            sys.exit(0)
except Exception:
    pass

sys.exit(1)
PYEOF
