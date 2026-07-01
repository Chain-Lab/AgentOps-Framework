"""
Phase 64 — Docker artifact validation tests.

Validates Dockerfile, .dockerignore, entrypoint.sh, and healthcheck.sh
exist and contain required content. No Docker daemon required.
"""
from __future__ import annotations

import os
import stat

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOCKER_DIR = os.path.join(PROJECT_ROOT, "docker")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class TestDockerfile:
    """Dockerfile existence and content checks."""

    def test_dockerfile_exists(self):
        path = os.path.join(PROJECT_ROOT, "Dockerfile")
        assert os.path.isfile(path), f"Dockerfile not found at {path}"

    def test_dockerfile_uses_non_root_user(self):
        content = _read(os.path.join(PROJECT_ROOT, "Dockerfile"))
        assert "USER agent-app" in content, "Dockerfile must set USER agent-app (non-root)"

    def test_dockerfile_has_healthcheck(self):
        content = _read(os.path.join(PROJECT_ROOT, "Dockerfile"))
        assert "HEALTHCHECK" in content, "Dockerfile must contain HEALTHCHECK instruction"

    def test_dockerfile_exposes_health_port(self):
        content = _read(os.path.join(PROJECT_ROOT, "Dockerfile"))
        assert "EXPOSE 8080" in content, "Dockerfile must expose port 8080 (health)"

    def test_dockerfile_exposes_control_port(self):
        content = _read(os.path.join(PROJECT_ROOT, "Dockerfile"))
        assert "EXPOSE 8090" in content, "Dockerfile must expose port 8090 (control)"

    def test_dockerfile_has_workdir(self):
        content = _read(os.path.join(PROJECT_ROOT, "Dockerfile"))
        assert "WORKDIR /app" in content, "Dockerfile must set WORKDIR /app"


class TestDockerignore:
    """.dockerignore existence and content checks."""

    def test_dockerignore_exists(self):
        path = os.path.join(PROJECT_ROOT, ".dockerignore")
        assert os.path.isfile(path), f".dockerignore not found at {path}"

    def test_dockerignore_excludes_git(self):
        content = _read(os.path.join(PROJECT_ROOT, ".dockerignore"))
        assert ".git" in content, ".dockerignore must exclude .git"

    def test_dockerignore_excludes_venv(self):
        content = _read(os.path.join(PROJECT_ROOT, ".dockerignore"))
        assert ".venv" in content, ".dockerignore must exclude .venv"

    def test_dockerignore_excludes_pycache(self):
        content = _read(os.path.join(PROJECT_ROOT, ".dockerignore"))
        assert "__pycache__" in content, ".dockerignore must exclude __pycache__"


class TestDockerScripts:
    """docker/ entrypoint and healthcheck existence and permissions."""

    def test_entrypoint_exists(self):
        path = os.path.join(DOCKER_DIR, "entrypoint.sh")
        assert os.path.isfile(path), f"entrypoint.sh not found at {path}"

    def test_entrypoint_is_executable(self):
        path = os.path.join(DOCKER_DIR, "entrypoint.sh")
        mode = os.stat(path).st_mode
        assert mode & stat.S_IXUSR, "entrypoint.sh must be executable"

    def test_entrypoint_has_set_e(self):
        content = _read(os.path.join(DOCKER_DIR, "entrypoint.sh"))
        assert "set -e" in content, "entrypoint.sh must use 'set -e'"

    def test_healthcheck_exists(self):
        path = os.path.join(DOCKER_DIR, "healthcheck.sh")
        assert os.path.isfile(path), f"healthcheck.sh not found at {path}"

    def test_healthcheck_is_executable(self):
        path = os.path.join(DOCKER_DIR, "healthcheck.sh")
        mode = os.stat(path).st_mode
        assert mode & stat.S_IXUSR, "healthcheck.sh must be executable"

    def test_healthcheck_calls_health(self):
        content = _read(os.path.join(DOCKER_DIR, "healthcheck.sh"))
        assert "/health" in content, "healthcheck.sh must call /health endpoint"
