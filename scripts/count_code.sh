#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

EXCLUDE_DIRS=(
  --exclude-dir=.git
  --exclude-dir=.venv
  --exclude-dir=__pycache__
  --exclude-dir=.pytest_cache
  --exclude-dir=.agent_app
  --exclude-dir=.claude
  --exclude-dir=.codegraph
  --exclude-dir=*.egg-info
  --exclude-dir=node_modules
  --exclude-dir=.opencode
)

EXCLUDE_FILES=(
  --exclude-ext=pyc
)

if [[ $# -eq 0 ]]; then
  cloc "${EXCLUDE_DIRS[@]}" "${EXCLUDE_FILES[@]}" .
else
  cloc "${EXCLUDE_DIRS[@]}" "${EXCLUDE_FILES[@]}" "$@"
fi
