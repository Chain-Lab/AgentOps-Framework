"""Tests for Phase 35 CLI: rollout plan commands."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(*args, cwd=None):
    """Run the CLI and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "agent_app.cli", *args],
        capture_output=True,
        text=True,
        cwd=cwd or str(Path(__file__).resolve().parent.parent.parent),
    )
    return result.returncode, result.stdout, result.stderr


def _write_config(tmp_path, content: str) -> str:
    path = tmp_path / "agentapp.yaml"
    path.write_text(content)
    return str(path)


_BASE_CONFIG_35 = """
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
  policy_release:
    bundles:
      type: sqlite
      path: {bundle_db}
    gates:
      type: sqlite
      path: {gate_db}
    promotions:
      type: sqlite
      path: {promo_db}
    activations:
      type: sqlite
      path: {activation_db}
    environments:
      type: sqlite
      path: {environment_db}
    rings:
      type: sqlite
      path: {ring_db}
    ring_assignments:
      type: sqlite
      path: {ring_assignment_db}
    change_events:
      type: sqlite
      path: {change_events_db}
    rollouts:
      type: sqlite
      path: {rollout_db}
    require_promotion_approval: true
    allow_gate_bypass: false
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
"""

# Config without rollouts section
_BASE_CONFIG_NO_ROLLOUT = """
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
  policy_release:
    bundles:
      type: sqlite
      path: {bundle_db}
    gates:
      type: sqlite
      path: {gate_db}
    promotions:
      type: sqlite
      path: {promo_db}
    activations:
      type: sqlite
      path: {activation_db}
    environments:
      type: sqlite
      path: {environment_db}
    rings:
      type: sqlite
      path: {ring_db}
    ring_assignments:
      type: sqlite
      path: {ring_assignment_db}
    change_events:
      type: sqlite
      path: {change_events_db}
    require_promotion_approval: true
    allow_gate_bypass: false
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
"""

_STEPS_YAML = """
steps:
  - step_id: s1
    step_type: activate
    environment: prod
    ring_name: canary
  - step_id: s2
    step_type: assign_ring
    environment: prod
    ring_name: stable
    require_previous_step: s1
"""

# Permissions needed for rollout operations
_ROLLOUT_CREATE_PERM = "policy.rollout.create"
_ROLLOUT_START_PERM = "policy.rollout.start"
_ROLLOUT_EXECUTE_PERM = "policy.rollout.execute"
_ROLLOUT_CANCEL_PERM = "policy.rollout.cancel"


def _cleanup_dbs(*db_paths):
    """Remove existing database files."""
    for p in db_paths:
        if os.path.exists(p):
            os.remove(p)


def _write_test_config(tmp_path):
    """Write a full Phase 35 config (with rollouts) and return its path."""
    bundle_db = str(tmp_path / "bundles.db")
    gate_db = str(tmp_path / "gates.db")
    promo_db = str(tmp_path / "promos.db")
    activation_db = str(tmp_path / "activations.db")
    environment_db = str(tmp_path / "environments.db")
    ring_db = str(tmp_path / "rings.db")
    ring_assignment_db = str(tmp_path / "ring_assignments.db")
    change_events_db = str(tmp_path / "change_events.db")
    rollout_db = str(tmp_path / "rollouts.db")
    _cleanup_dbs(
        bundle_db, gate_db, promo_db, activation_db,
        environment_db, ring_db, ring_assignment_db, change_events_db,
        rollout_db,
    )
    return _write_config(tmp_path, _BASE_CONFIG_35.format(
        bundle_db=bundle_db, gate_db=gate_db, promo_db=promo_db,
        activation_db=activation_db, environment_db=environment_db,
        ring_db=ring_db, ring_assignment_db=ring_assignment_db,
        change_events_db=change_events_db, rollout_db=rollout_db,
    ))


def _write_test_config_no_rollout(tmp_path):
    """Write a config without rollouts section and return its path."""
    bundle_db = str(tmp_path / "bundles.db")
    gate_db = str(tmp_path / "gates.db")
    promo_db = str(tmp_path / "promos.db")
    activation_db = str(tmp_path / "activations.db")
    environment_db = str(tmp_path / "environments.db")
    ring_db = str(tmp_path / "rings.db")
    ring_assignment_db = str(tmp_path / "ring_assignments.db")
    change_events_db = str(tmp_path / "change_events.db")
    _cleanup_dbs(
        bundle_db, gate_db, promo_db, activation_db,
        environment_db, ring_db, ring_assignment_db, change_events_db,
    )
    return _write_config(tmp_path, _BASE_CONFIG_NO_ROLLOUT.format(
        bundle_db=bundle_db, gate_db=gate_db, promo_db=promo_db,
        activation_db=activation_db, environment_db=environment_db,
        ring_db=ring_db, ring_assignment_db=ring_assignment_db,
        change_events_db=change_events_db,
    ))


def _write_steps_file(tmp_path):
    """Write a steps YAML file and return its path."""
    steps_path = tmp_path / "rollout_steps.yaml"
    steps_path.write_text(_STEPS_YAML)
    return str(steps_path)


class TestPhase35RolloutCLI:
    """Tests for Phase 35 rollout CLI commands."""

    def test_rollout_create(self, tmp_path):
        """policy rollout create creates a rollout plan from a steps file."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        rc, out, err = _run_cli(
            "policy", "rollout", "create",
            "--config", config,
            "--name", "test-rollout",
            "--bundle-id", "bnd_test123",
            "--steps-file", steps_file,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_CREATE_PERM,
            "--reason", "Testing rollout CLI",
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert data["name"] == "test-rollout"
        assert data["bundle_id"] == "bnd_test123"
        assert data["status"] == "draft"
        assert data["step_count"] == 2

    def test_rollout_list(self, tmp_path):
        """policy rollout list lists rollout plans."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        # Create a plan first
        rc, out, err = _run_cli(
            "policy", "rollout", "create",
            "--config", config,
            "--name", "list-test",
            "--bundle-id", "bnd_list",
            "--steps-file", steps_file,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_CREATE_PERM,
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        # Now list
        rc, out, err = _run_cli(
            "policy", "rollout", "list",
            "--config", config,
            "--json",
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["name"] == "list-test"

    def test_rollout_show(self, tmp_path):
        """policy rollout show displays a specific rollout plan."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        # Create a plan first
        rc, out, err = _run_cli(
            "policy", "rollout", "create",
            "--config", config,
            "--name", "show-test",
            "--bundle-id", "bnd_show",
            "--steps-file", steps_file,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_CREATE_PERM,
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        rollout_id = json.loads(out)["rollout_id"]
        # Show the plan
        rc, out, err = _run_cli(
            "policy", "rollout", "show",
            "--config", config,
            "--rollout-id", rollout_id,
            "--json",
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert data["rollout_id"] == rollout_id
        assert data["name"] == "show-test"
        assert len(data["steps"]) == 2

    def test_rollout_start(self, tmp_path):
        """policy rollout start transitions a plan from DRAFT to ACTIVE."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        # Create a plan first
        rc, out, err = _run_cli(
            "policy", "rollout", "create",
            "--config", config,
            "--name", "start-test",
            "--bundle-id", "bnd_start",
            "--steps-file", steps_file,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_CREATE_PERM,
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        rollout_id = json.loads(out)["rollout_id"]
        # Start the plan
        rc, out, err = _run_cli(
            "policy", "rollout", "start",
            "--config", config,
            "--rollout-id", rollout_id,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_START_PERM,
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert data["rollout_id"] == rollout_id
        assert data["status"] == "active"

    def test_rollout_run_next(self, tmp_path):
        """policy rollout run-next runs the next step in a plan."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        # Create and start a plan
        rc, out, err = _run_cli(
            "policy", "rollout", "create",
            "--config", config,
            "--name", "runnext-test",
            "--bundle-id", "bnd_runnext",
            "--steps-file", steps_file,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_CREATE_PERM,
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        rollout_id = json.loads(out)["rollout_id"]
        rc, out, err = _run_cli(
            "policy", "rollout", "start",
            "--config", config,
            "--rollout-id", rollout_id,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_START_PERM,
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        # Run next step
        rc, out, err = _run_cli(
            "policy", "rollout", "run-next",
            "--config", config,
            "--rollout-id", rollout_id,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_EXECUTE_PERM,
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert data["rollout_id"] == rollout_id
        # The first step should have been attempted
        step_statuses = [s["status"] for s in data["steps"]]
        assert "running" in step_statuses or "succeeded" in step_statuses or "failed" in step_statuses or "blocked" in step_statuses

    def test_rollout_cancel(self, tmp_path):
        """policy rollout cancel cancels a rollout plan."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        # Create a plan
        rc, out, err = _run_cli(
            "policy", "rollout", "create",
            "--config", config,
            "--name", "cancel-test",
            "--bundle-id", "bnd_cancel",
            "--steps-file", steps_file,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_CREATE_PERM,
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        rollout_id = json.loads(out)["rollout_id"]
        # Cancel the plan
        rc, out, err = _run_cli(
            "policy", "rollout", "cancel",
            "--config", config,
            "--rollout-id", rollout_id,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_CANCEL_PERM,
            "--reason", "No longer needed",
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert data["rollout_id"] == rollout_id
        assert data["status"] == "cancelled"

    def test_rollout_no_service(self, tmp_path):
        """policy rollout create without rollout config prints error."""
        config = _write_test_config_no_rollout(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        rc, out, err = _run_cli(
            "policy", "rollout", "create",
            "--config", config,
            "--name", "no-service",
            "--bundle-id", "bnd_nosvc",
            "--steps-file", steps_file,
            "--actor-id", "admin",
        )
        assert rc != 0, f"Expected non-zero exit code, got {rc}"
        assert "rollout" in err.lower() or "not" in err.lower()
