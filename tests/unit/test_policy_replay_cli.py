"""Tests for policy replay CLI command."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest


def _run_cli(*args, cwd=None):
    """Run the CLI and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "agent_app.cli", *args],
        capture_output=True,
        text=True,
        cwd=cwd or "/home/ymj68520/projects/Python/AgentOps Framework",
    )
    return result.returncode, result.stdout, result.stderr


def _write_config(tmp_path, content: str) -> str:
    path = tmp_path / "agentapp.yaml"
    path.write_text(content)
    return str(path)


class TestPolicyReplayCLI:
    def test_replay_command_succeeds(self, tmp_path):
        """policy replay command succeeds with valid config."""
        config = _write_config(tmp_path, """
app:
  name: test
  environment: dev
governance:
  policies:
    enabled: true
    default_action: allow
    rules: []
  policy_decisions:
    type: memory
""")
        rc, out, err = _run_cli("policy", "replay", "--config", config)
        assert rc == 0, f"stderr: {err}"
        assert "Policy replay completed" in out

    def test_replay_command_with_filters(self, tmp_path):
        """policy replay supports tenant-id and tool-name filters."""
        config = _write_config(tmp_path, """
app:
  name: test
  environment: dev
governance:
  policies:
    enabled: true
    default_action: allow
    rules: []
  policy_decisions:
    type: memory
""")
        rc, out, err = _run_cli(
            "policy", "replay",
            "--config", config,
            "--tenant-id", "tenant_a",
            "--tool-name", "refund.request",
            "--limit", "10",
        )
        assert rc == 0, f"stderr: {err}"

    def test_replay_command_invalid_config_exits_nonzero(self, tmp_path):
        """policy replay exits non-zero on invalid config path."""
        rc, out, err = _run_cli(
            "policy", "replay",
            "--config", "/nonexistent/path.yaml",
        )
        assert rc != 0

    def test_replay_command_json_output(self, tmp_path):
        """policy replay --json outputs JSON."""
        config = _write_config(tmp_path, """
app:
  name: test
  environment: dev
governance:
  policies:
    enabled: true
    default_action: allow
    rules: []
  policy_decisions:
    type: memory
""")
        rc, out, err = _run_cli(
            "policy", "replay",
            "--config", config,
            "--json",
        )
        assert rc == 0, f"stderr: {err}"
        data = json.loads(out)
        assert "replay_id" in data
        assert "changed_count" in data
        assert "unchanged_count" in data
        assert "failed_count" in data
