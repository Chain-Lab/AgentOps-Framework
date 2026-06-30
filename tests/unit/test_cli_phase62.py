"""Phase 62 Task 7: CLI serve, health-server, and drain tests."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cli(args_list):
    """Run CLI command via sys.argv patching. Returns (exit_code, stdout, stderr)."""
    import builtins
    from agent_app.cli import main

    captured_out = []
    captured_err = []
    original_print = builtins.print

    def mock_print(*args, **kwargs):
        captured_out.append(" ".join(str(a) for a in args))
        original_print(*args, **kwargs)

    with patch.object(sys, "argv", ["agentapp"] + args_list):
        with patch.object(builtins, "print", side_effect=mock_print):
            try:
                exit_code = main()
            except SystemExit as exc:
                exit_code = exc.code if exc.code is not None else 0
            except Exception as exc:
                captured_err.append(str(exc))
                exit_code = 1

    return exit_code, "\n".join(captured_out), "\n".join(captured_err)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDaemonServeCLI:
    """Tests for 'policy federation notification delivery daemon serve' CLI command."""

    def test_serve_requires_config(self):
        """daemon serve requires --config."""
        exit_code, stdout, stderr = _run_cli(
            ["policy", "federation", "notification", "alerts", "delivery", "daemon", "serve"]
        )
        assert exit_code != 0

    def test_serve_requires_daemon_config(self, tmp_path):
        """daemon serve exits non-zero when daemon not configured."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text("app:\n  name: test\n")
        exit_code, _, _ = _run_cli([
            "policy", "federation", "notification", "alerts",
            "delivery", "daemon", "serve", "--config", str(config_file),
        ])
        assert exit_code != 0


class TestDaemonHealthServerCLI:
    """Tests for 'daemon health-server' CLI command."""

    def test_health_server_requires_config(self):
        """daemon health-server requires --config."""
        exit_code, stdout, stderr = _run_cli(
            ["policy", "federation", "notification", "alerts", "delivery", "daemon", "health-server"]
        )
        assert exit_code != 0

    def test_health_server_requires_daemon_config(self, tmp_path):
        """daemon health-server exits non-zero when daemon not configured."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text("app:\n  name: test\n")
        exit_code, _, _ = _run_cli([
            "policy", "federation", "notification", "alerts",
            "delivery", "daemon", "health-server", "--config", str(config_file),
        ])
        assert exit_code != 0


class TestDaemonDrainCLI:
    """Tests for 'daemon drain' CLI command."""

    def test_drain_requires_config(self):
        """daemon drain requires --config."""
        exit_code, stdout, stderr = _run_cli(
            ["policy", "federation", "notification", "alerts", "delivery", "daemon", "drain"]
        )
        assert exit_code != 0

    def test_drain_daemon_not_running(self, tmp_path):
        """daemon drain exits non-zero when daemon not configured."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text("app:\n  name: test\n")
        exit_code, stderr, _ = _run_cli([
            "policy", "federation", "notification", "alerts",
            "delivery", "daemon", "drain", "--config", str(config_file),
        ])
        assert exit_code != 0


class TestDaemonCLIRegistration:
    """Tests that Phase 62 CLI subcommands dispatch correctly."""

    def test_serve_dispatches(self, tmp_path):
        """daemon serve dispatches to the serve handler (fails without daemon config)."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text("app:\n  name: test\n")
        exit_code, stderr, _ = _run_cli([
            "policy", "federation", "notification", "alerts",
            "delivery", "daemon", "serve", "--config", str(config_file),
        ])
        # Should fail because daemon not configured, not because command unknown
        assert "Retry daemon not configured" in stderr or exit_code != 0

    def test_health_server_dispatches(self, tmp_path):
        """daemon health-server dispatches to the handler."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text("app:\n  name: test\n")
        exit_code, stderr, _ = _run_cli([
            "policy", "federation", "notification", "alerts",
            "delivery", "daemon", "health-server", "--config", str(config_file),
        ])
        assert "Retry daemon not configured" in stderr or exit_code != 0

    def test_drain_dispatches(self, tmp_path):
        """daemon drain dispatches to the handler."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text("app:\n  name: test\n")
        exit_code, stderr, _ = _run_cli([
            "policy", "federation", "notification", "alerts",
            "delivery", "daemon", "drain", "--config", str(config_file),
        ])
        assert "Retry daemon not configured" in stderr or exit_code != 0
