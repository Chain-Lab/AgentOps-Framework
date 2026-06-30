"""Phase 61 Task 7: CLI daemon health, validate-config, and metrics-export tests."""
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


class TestDaemonHealthCLI:
    """Tests for 'policy federation notification delivery daemon health' CLI command."""

    def test_health_requires_config(self):
        """daemon health requires --config."""
        exit_code, stdout, stderr = _run_cli(
            ["policy", "federation", "notification", "alerts", "delivery", "daemon", "health"]
        )
        assert exit_code != 0

    def test_health_no_daemon_configured(self, tmp_path):
        """daemon health exits 0 when daemon not configured."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text("app:\n  name: test\n")
        exit_code, stdout, stderr = _run_cli(
            [
                "policy", "federation", "notification", "alerts", "delivery", "daemon", "health",
                "--config", str(config_file),
            ]
        )
        assert exit_code == 0
        combined = stdout + stderr
        assert "Retry daemon not configured" in combined

    def test_health_json_output(self, tmp_path):
        """daemon health --json outputs JSON."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text("app:\n  name: test\n")
        exit_code, stdout, stderr = _run_cli(
            [
                "policy", "federation", "notification", "alerts", "delivery", "daemon", "health",
                "--config", str(config_file), "--json",
            ]
        )
        assert exit_code == 0


class TestDaemonValidateConfigCLI:
    """Tests for 'policy federation notification delivery daemon validate-config' CLI command."""

    def test_validate_config_requires_config(self):
        """daemon validate-config requires --config."""
        exit_code, stdout, stderr = _run_cli(
            ["policy", "federation", "notification", "alerts", "delivery", "daemon", "validate-config"]
        )
        assert exit_code != 0

    def test_validate_config_no_daemon(self, tmp_path):
        """daemon validate-config exits 0 when daemon not configured."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text("app:\n  name: test\n")
        exit_code, stdout, stderr = _run_cli(
            [
                "policy", "federation", "notification", "alerts", "delivery", "daemon", "validate-config",
                "--config", str(config_file),
            ]
        )
        assert exit_code == 0
        combined = stdout + stderr
        assert "Retry daemon not configured" in combined


class TestMetricsExportCLI:
    """Tests for 'policy federation notification metrics-export' CLI command."""

    def test_metrics_export_requires_args(self):
        """metrics-export requires --config and --output."""
        exit_code, stdout, stderr = _run_cli(
            ["policy", "federation", "notification", "metrics-export"]
        )
        assert exit_code != 0

    def test_metrics_export_no_metrics_configured(self, tmp_path):
        """metrics-export exits 1 when enhanced_metrics not configured."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text("app:\n  name: test\n")
        output_file = tmp_path / "metrics.prom"
        exit_code, stdout, stderr = _run_cli(
            [
                "policy", "federation", "notification", "metrics-export",
                "--config", str(config_file),
                "--output", str(output_file),
            ]
        )
        assert exit_code == 1
        combined = stdout + stderr
        assert "not configured" in combined
