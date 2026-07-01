"""
Phase 64 — Deployment config validation tests.

Validates deploy/config/*.yaml are parseable and contain required fields.
"""
from __future__ import annotations

import os

import pytest
import yaml

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "deploy", "config")

CONFIG_FILES = {
    "example": os.path.join(CONFIG_DIR, "daemon.example.yaml"),
    "kubernetes": os.path.join(CONFIG_DIR, "daemon.kubernetes.yaml"),
    "systemd": os.path.join(CONFIG_DIR, "daemon.systemd.yaml"),
}

# Phase 62 graceful drain / metrics fields that must appear in all configs
PHASE_62_REQUIRED_FIELDS = [
    "graceful_shutdown_enabled",
    "drain_timeout_seconds",
    "cancel_inflight_on_timeout",
    "metrics_buffer_enabled",
    "flush_metrics_on_stop",
]

# Phase 63 control plane fields that must appear in all configs
PHASE_63_REQUIRED_FIELDS = [
    "control_plane_enabled",
    "control_http_enabled",
    "control_http_port",
    "control_plane_db_path",
]


def _load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _get_retry_daemon_config(config: dict) -> dict:
    """Extract retry_daemon config from full AppConfig structure."""
    runtime = config.get("runtime", {})
    alerts = runtime.get("alerts", {})
    delivery = alerts.get("delivery", {})
    return delivery.get("retry_daemon", {})


class TestConfigFilesExist:
    """All three config files must exist."""

    @pytest.mark.parametrize("name,path", CONFIG_FILES.items())
    def test_config_exists(self, name: str, path: str):
        assert os.path.isfile(path), f"Config file not found: {path}"


class TestConfigParseable:
    """All configs must be valid YAML."""

    @pytest.mark.parametrize("name,path", CONFIG_FILES.items())
    def test_config_parseable(self, name: str, path: str):
        config = _load_config(path)
        assert isinstance(config, dict), f"{name}: config must parse to a dict"


class TestConfigHasRetryDaemon:
    """All configs must contain retry_daemon section."""

    @pytest.mark.parametrize("name,path", CONFIG_FILES.items())
    def test_has_retry_daemon(self, name: str, path: str):
        config = _load_config(path)
        rd = _get_retry_daemon_config(config)
        assert rd, f"{name}: missing runtime.alerts.delivery.retry_daemon section"


class TestPhase62Fields:
    """All configs must include Phase 62 graceful drain / metrics fields."""

    @pytest.mark.parametrize("name,path", CONFIG_FILES.items())
    def test_phase62_fields_present(self, name: str, path: str):
        config = _load_config(path)
        rd = _get_retry_daemon_config(config)
        for field in PHASE_62_REQUIRED_FIELDS:
            assert field in rd, f"{name}: missing Phase 62 field '{field}'"


class TestPhase63Fields:
    """All configs must include Phase 63 control plane fields."""

    @pytest.mark.parametrize("name,path", CONFIG_FILES.items())
    def test_phase63_fields_present(self, name: str, path: str):
        config = _load_config(path)
        rd = _get_retry_daemon_config(config)
        for field in PHASE_63_REQUIRED_FIELDS:
            assert field in rd, f"{name}: missing Phase 63 field '{field}'"


class TestKubernetesConfig:
    """Kubernetes-specific config checks."""

    def test_k8s_control_db_path_is_data(self):
        config = _load_config(CONFIG_FILES["kubernetes"])
        rd = _get_retry_daemon_config(config)
        db_path = rd.get("control_plane_db_path", "")
        assert db_path == "/data/control_plane.db", (
            f"K8s config should use /data/control_plane.db, got: {db_path}"
        )

    def test_k8s_health_http_enabled(self):
        config = _load_config(CONFIG_FILES["kubernetes"])
        rd = _get_retry_daemon_config(config)
        assert rd.get("health_http_enabled") is True, "K8s config should enable health HTTP"

    def test_k8s_control_plane_enabled(self):
        config = _load_config(CONFIG_FILES["kubernetes"])
        rd = _get_retry_daemon_config(config)
        assert rd.get("control_plane_enabled") is True, "K8s config should enable control plane"


class TestSystemdConfig:
    """systemd-specific config checks."""

    def test_systemd_control_db_path(self):
        config = _load_config(CONFIG_FILES["systemd"])
        rd = _get_retry_daemon_config(config)
        db_path = rd.get("control_plane_db_path", "")
        assert db_path == "/var/lib/agent-app/control_plane.db", (
            f"systemd config should use /var/lib/agent-app/control_plane.db, got: {db_path}"
        )

    def test_systemd_health_http_enabled(self):
        config = _load_config(CONFIG_FILES["systemd"])
        rd = _get_retry_daemon_config(config)
        assert rd.get("health_http_enabled") is True, "systemd config should enable health HTTP"

    def test_systemd_control_plane_enabled(self):
        config = _load_config(CONFIG_FILES["systemd"])
        rd = _get_retry_daemon_config(config)
        assert rd.get("control_plane_enabled") is True, "systemd config should enable control plane"
