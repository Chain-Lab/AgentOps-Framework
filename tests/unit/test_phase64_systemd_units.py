"""
Phase 64 — systemd unit file validation tests.

Validates deploy/systemd/*.service contains required directives.
Static validation only — no systemd daemon required.
"""
from __future__ import annotations

import os

SYSTEMD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "systemd"))
SERVICE_FILE = os.path.join(SYSTEMD_DIR, "agent-app-daemon.service")
ENV_FILE = os.path.join(SYSTEMD_DIR, "agent-app-daemon.env")


def _read_service() -> str:
    with open(SERVICE_FILE, "r", encoding="utf-8") as fh:
        return fh.read()


class TestServiceFileExists:
    """Service file must exist."""

    def test_service_file_exists(self):
        assert os.path.isfile(SERVICE_FILE), f"Service file not found: {SERVICE_FILE}"


class TestServiceHasExecStart:
    """Service must have ExecStart directive."""

    def test_has_execstart(self):
        content = _read_service()
        assert "ExecStart=" in content, "Service must have ExecStart directive"

    def test_execstart_has_daemon_serve(self):
        content = _read_service()
        assert "daemon serve" in content, "ExecStart must run 'daemon serve'"


class TestServiceHasExecStopDrain:
    """Service must have ExecStop with drain command."""

    def test_has_execstop(self):
        content = _read_service()
        assert "ExecStop=" in content, "Service must have ExecStop directive"

    def test_execstop_has_drain(self):
        content = _read_service()
        assert "drain" in content, "ExecStop must send drain command"


class TestServiceHasRestartPolicy:
    """Service must have restart policy."""

    def test_restart_on_failure(self):
        content = _read_service()
        assert "Restart=on-failure" in content, "Service must have Restart=on-failure"

    def test_restart_sec(self):
        content = _read_service()
        assert "RestartSec=" in content, "Service must have RestartSec"


class TestServiceHasKillSignal:
    """Service must specify KillSignal."""

    def test_kill_signal_sigterm(self):
        content = _read_service()
        assert "KillSignal=SIGTERM" in content, "Service must use KillSignal=SIGTERM"


class TestServiceHasTimeoutStopSec:
    """Service must specify TimeoutStopSec."""

    def test_timeout_stop_sec(self):
        content = _read_service()
        assert "TimeoutStopSec=" in content, "Service must have TimeoutStopSec"
        # Extract value
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("TimeoutStopSec="):
                val = line.split("=", 1)[1]
                assert val.isdigit(), f"TimeoutStopSec must be numeric, got: {val}"
                assert int(val) >= 30, f"TimeoutStopSec must be >= 30, got: {val}"


class TestServiceHasHardening:
    """Service must have security hardening directives."""

    def test_no_new_privileges(self):
        content = _read_service()
        assert "NoNewPrivileges=true" in content, "Service must set NoNewPrivileges=true"

    def test_private_tmp(self):
        content = _read_service()
        assert "PrivateTmp=true" in content, "Service must set PrivateTmp=true"

    def test_protect_system(self):
        content = _read_service()
        assert "ProtectSystem=" in content, "Service must set ProtectSystem"

    def test_protect_home(self):
        content = _read_service()
        assert "ProtectHome=" in content, "Service must set ProtectHome"


class TestServiceNonRoot:
    """Service must run as non-root user."""

    def test_user_agent_app(self):
        content = _read_service()
        assert "User=agent-app" in content, "Service must run as User=agent-app"

    def test_group_agent_app(self):
        content = _read_service()
        assert "Group=agent-app" in content, "Service must run as Group=agent-app"


class TestEnvFile:
    """Environment file checks."""

    def test_env_file_exists(self):
        assert os.path.isfile(ENV_FILE), f"Env file not found: {ENV_FILE}"

    def test_env_file_has_config_path(self):
        with open(ENV_FILE, "r", encoding="utf-8") as fh:
            content = fh.read()
        assert "AGENT_APP_CONFIG=" in content, "Env file must set AGENT_APP_CONFIG"

    def test_env_file_has_control_db_path(self):
        with open(ENV_FILE, "r", encoding="utf-8") as fh:
            content = fh.read()
        assert "AGENT_APP_CONTROL_DB=" in content, "Env file must set AGENT_APP_CONTROL_DB"

    def test_env_file_no_real_token(self):
        """Env file must not contain a real token (commented out or placeholder)."""
        with open(ENV_FILE, "r", encoding="utf-8") as fh:
            content = fh.read()
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("#"):
                continue
            if line.startswith("AGENT_APP_CONTROL_TOKEN="):
                val = line.split("=", 1)[1].strip("\"'")
                assert val in ("", "replace-me-with-real-token"), (
                    f"Env file must not contain real token, got: {val!r}"
                )
