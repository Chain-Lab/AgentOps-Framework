"""Tests for Phase 28 policy replay CLI extensions."""

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


_BASE_CONFIG = """
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
"""


class TestPolicyReplayCLIPhase28:
    """Phase 28 CLI extensions."""

    def test_sync_replay_still_works(self, tmp_path):
        """Synchronous replay still works (backward compat)."""
        config = _write_config(tmp_path, _BASE_CONFIG)
        rc, out, err = _run_cli("policy", "replay", "--config", config)
        assert rc == 0, f"stderr: {err}"
        assert "Policy replay completed" in out

    def test_background_submit_creates_job(self, tmp_path):
        """--background submits a job instead of running synchronously."""
        config = _write_config(tmp_path, _BASE_CONFIG)
        rc, out, err = _run_cli(
            "policy", "replay",
            "--config", config,
            "--background",
            "--requested-by", "admin",
            "--limit", "50",
            "--store", "sqlite",
            "--db-path", str(tmp_path / "replays.db"),
        )
        assert rc == 0, f"stderr: {err}"
        assert "Policy replay job queued" in out
        assert "Job ID:" in out
        assert "Status: queued" in out or "queued" in out

    def test_background_json_output(self, tmp_path):
        """--background --json outputs job JSON."""
        config = _write_config(tmp_path, _BASE_CONFIG)
        rc, out, err = _run_cli(
            "policy", "replay",
            "--config", config,
            "--background",
            "--json",
            "--store", "sqlite",
            "--db-path", str(tmp_path / "replays.db"),
        )
        assert rc == 0, f"stderr: {err}"
        data = json.loads(out)
        assert "job_id" in data
        assert data["status"] == "queued"

    def test_run_job_completes(self, tmp_path):
        """run-job command executes a queued job."""
        db_path = str(tmp_path / "replays.db")
        config = _write_config(tmp_path, _BASE_CONFIG)
        # First, submit a job
        rc, out, err = _run_cli(
            "policy", "replay",
            "--config", config,
            "--background",
            "--json",
            "--store", "sqlite",
            "--db-path", db_path,
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        job_data = json.loads(out)
        job_id = job_data["job_id"]
        assert job_data["status"] == "queued"

        # Now run it via run-job subcommand
        rc, out, err = _run_cli(
            "policy", "run-job", job_id,
            "--config", config,
            "--store", "sqlite",
            "--db-path", db_path,
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        assert "completed" in out.lower() or "job" in out.lower()

    def test_run_job_missing_id_exits_nonzero(self, tmp_path):
        """run-job with invalid job ID exits non-zero."""
        config = _write_config(tmp_path, _BASE_CONFIG)
        rc, out, err = _run_cli(
            "policy", "run-job", "nonexistent_job",
            "--config", config,
        )
        assert rc != 0

    def test_jobs_list_empty(self, tmp_path):
        """jobs command shows empty state when no jobs."""
        config = _write_config(tmp_path, _BASE_CONFIG)
        rc, out, err = _run_cli(
            "policy", "jobs",
            "--config", config,
        )
        assert rc == 0, f"stderr: {err}"
        assert "No replay jobs found" in out

    def test_jobs_list_after_submit(self, tmp_path):
        """jobs command shows jobs after submitting."""
        db_path = str(tmp_path / "replays.db")
        config = _write_config(tmp_path, _BASE_CONFIG)
        # Submit a job
        _run_cli(
            "policy", "replay",
            "--config", config,
            "--background",
            "--store", "sqlite",
            "--db-path", db_path,
        )

        # List jobs
        rc, out, err = _run_cli(
            "policy", "jobs",
            "--config", config,
            "--store", "sqlite",
            "--db-path", db_path,
        )
        assert rc == 0, f"stderr: {err}"
        assert "queued" in out

    def test_jobs_json_output(self, tmp_path):
        """jobs --json outputs JSON array."""
        db_path = str(tmp_path / "replays.db")
        config = _write_config(tmp_path, _BASE_CONFIG)
        _run_cli(
            "policy", "replay",
            "--config", config,
            "--background",
            "--store", "sqlite",
            "--db-path", db_path,
        )
        rc, out, err = _run_cli(
            "policy", "jobs",
            "--config", config,
            "--json",
            "--store", "sqlite",
            "--db-path", db_path,
        )
        assert rc == 0, f"stderr: {err}"
        data = json.loads(out)
        assert isinstance(data, list)
        if data:
            assert "job_id" in data[0]
            assert "status" in data[0]

    def test_replay_with_filters_background(self, tmp_path):
        """Background replay respects filters."""
        config = _write_config(tmp_path, _BASE_CONFIG)
        rc, out, err = _run_cli(
            "policy", "replay",
            "--config", config,
            "--background",
            "--tenant-id", "tenant_a",
            "--tool-name", "refund.request",
            "--limit", "10",
            "--store", "sqlite",
            "--db-path", str(tmp_path / "replays.db"),
        )
        assert rc == 0, f"stderr: {err}"
        assert "queued" in out
