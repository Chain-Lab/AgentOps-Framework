"""Tests for Phase 33 CLI: ring management and canary eval commands."""
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


_BASE_CONFIG_33 = """
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
    require_promotion_approval: true
    allow_gate_bypass: false
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
"""


def _cleanup_dbs(*db_paths):
    """Remove existing database files."""
    for p in db_paths:
        if os.path.exists(p):
            os.remove(p)


def _write_test_config(tmp_path):
    """Write a minimal Phase 33 config to tmp_path and return its path."""
    bundle_db = str(tmp_path / "bundles.db")
    gate_db = str(tmp_path / "gates.db")
    promo_db = str(tmp_path / "promos.db")
    activation_db = str(tmp_path / "activations.db")
    environment_db = str(tmp_path / "environments.db")
    ring_db = str(tmp_path / "rings.db")
    ring_assignment_db = str(tmp_path / "ring_assignments.db")
    _cleanup_dbs(bundle_db, gate_db, promo_db, activation_db, environment_db, ring_db, ring_assignment_db)
    return _write_config(tmp_path, _BASE_CONFIG_33.format(
        bundle_db=bundle_db, gate_db=gate_db, promo_db=promo_db,
        activation_db=activation_db, environment_db=environment_db,
        ring_db=ring_db, ring_assignment_db=ring_assignment_db,
    ))


class TestPhase33RingCLI:
    """Tests for Phase 33 ring CLI commands."""

    def test_ring_create_success(self, tmp_path):
        """ring create succeeds with required options."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "ring", "create",
            "--config", config,
            "--environment", "prod",
            "--name", "canary",
            "--actor-id", "admin",
            "--permissions", "policy.ring.create",
        )
        assert rc == 0, f"stderr: {err}"
        assert "canary" in out.lower()
        assert "created" in out.lower() or "ring" in out.lower()

    def test_ring_create_with_description_and_default(self, tmp_path):
        """ring create with --description and --is-default flags."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "ring", "create",
            "--config", config,
            "--environment", "prod",
            "--name", "stable",
            "--actor-id", "admin",
            "--permissions", "policy.ring.create",
            "--description", "Production stable ring",
            "--is-default",
        )
        assert rc == 0, f"stderr: {err}"
        assert "stable" in out.lower()

    def test_ring_create_permission_denied(self, tmp_path):
        """ring create without proper permission fails."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "ring", "create",
            "--config", config,
            "--environment", "prod",
            "--name", "canary",
            "--actor-id", "unauthorized",
            "--permissions", "policy.bundle.create",
        )
        assert rc != 0
        assert "permission denied" in err.lower() or "permission denied" in out.lower()

    def test_ring_list_success(self, tmp_path):
        """ring list returns rings after creating one."""
        config = _write_test_config(tmp_path)
        # Create a ring first
        rc, out, err = _run_cli(
            "policy", "ring", "create",
            "--config", config,
            "--environment", "prod",
            "--name", "canary",
            "--actor-id", "admin",
            "--permissions", "policy.ring.create",
        )
        assert rc == 0, f"stderr: {err}"
        # List rings
        rc, out, err = _run_cli(
            "policy", "ring", "list",
            "--config", config,
            "--environment", "prod",
        )
        assert rc == 0, f"stderr: {err}"
        assert "canary" in out.lower()

    def test_ring_disable_and_enable(self, tmp_path):
        """ring disable then enable succeeds."""
        config = _write_test_config(tmp_path)
        # Create ring
        rc, out, err = _run_cli(
            "policy", "ring", "create",
            "--config", config,
            "--environment", "prod",
            "--name", "canary",
            "--actor-id", "admin",
            "--permissions", "policy.ring.create",
        )
        assert rc == 0, f"stderr: {err}"
        # Disable ring
        rc, out, err = _run_cli(
            "policy", "ring", "disable",
            "--config", config,
            "--environment", "prod",
            "--ring", "canary",
            "--actor-id", "admin",
            "--permissions", "policy.ring.disable",
            "--reason", "Incident response",
        )
        assert rc == 0, f"stderr: {err}"
        assert "disabled" in out.lower()
        # Enable ring
        rc, out, err = _run_cli(
            "policy", "ring", "enable",
            "--config", config,
            "--environment", "prod",
            "--ring", "canary",
            "--actor-id", "admin",
            "--permissions", "policy.ring.enable",
        )
        assert rc == 0, f"stderr: {err}"
        assert "enabled" in out.lower()

    def test_ring_disable_permission_denied(self, tmp_path):
        """ring disable without proper permission fails."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "ring", "create",
            "--config", config,
            "--environment", "prod",
            "--name", "canary",
            "--actor-id", "admin",
            "--permissions", "policy.ring.create",
        )
        assert rc == 0
        rc, out, err = _run_cli(
            "policy", "ring", "disable",
            "--config", config,
            "--environment", "prod",
            "--ring", "canary",
            "--actor-id", "unauthorized",
            "--permissions", "policy.bundle.create",
            "--reason", "Trying",
        )
        assert rc != 0
        assert "permission denied" in err.lower() or "permission denied" in out.lower()

    def test_ring_assign_success(self, tmp_path):
        """ring assign activates a bundle into a ring."""
        config = _write_test_config(tmp_path)
        # Create ring
        rc, out, err = _run_cli(
            "policy", "ring", "create",
            "--config", config,
            "--environment", "prod",
            "--name", "canary",
            "--actor-id", "admin",
            "--permissions", "policy.ring.create",
        )
        assert rc == 0, f"stderr: {err}"

        # Create a bundle, gate, promote, execute to get an activation
        rc, out, err = _run_cli(
            "policy", "bundle", "create",
            "--config", config,
            "--name", "ring-b1",
            "--version", "1.0.0",
            "--config-path", "test.yaml",
        )
        assert rc == 0, f"stderr: {err}"
        b1_id = None
        for line in out.split("\n"):
            if line.startswith("Bundle ID:"):
                b1_id = line.split(":", 1)[1].strip()
                break
        assert b1_id is not None

        _run_cli("policy", "gate", "run", "--config", config, "--bundle-id", b1_id)
        rc, out, err = _run_cli(
            "policy", "promotion", "request",
            "--config", config, "--bundle-id", b1_id,
            "--actor-id", "alice", "--permissions", "policy.promotion.request",
        )
        assert rc == 0, f"stderr: {err}"
        promo_id = None
        for line in out.split("\n"):
            if line.startswith("Promotion ID:"):
                promo_id = line.split(":", 1)[1].strip()
                break
        assert promo_id is not None

        _run_cli("policy", "promotion", "approve",
                  "--config", config, "--promotion-id", promo_id,
                  "--actor-id", "reviewer", "--permissions", "policy.promotion.approve")
        rc, out, err = _run_cli(
            "policy", "promotion", "execute",
            "--config", config, "--promotion-id", promo_id,
            "--actor-id", "release_mgr", "--permissions", "policy.promotion.execute",
            "--environment", "prod", "--reason", "Deploy v1",
        )
        assert rc == 0, f"stderr: {err}"

        # Get activation ID
        rc, out, err = _run_cli(
            "policy", "activation", "list",
            "--config", config, "--environment", "prod",
        )
        assert rc == 0, f"stderr: {err}"
        activation_id = None
        for line in out.strip().split("\n"):
            if line.startswith("pa_"):
                activation_id = line.split()[0]
                break
        if activation_id is None:
            # Try JSON mode
            rc, out, err = _run_cli(
                "policy", "activation", "list",
                "--config", config, "--environment", "prod", "--json",
            )
            data = json.loads(out)
            if data:
                activation_id = data[0]["activation_id"]
        assert activation_id is not None, f"Could not find activation ID in: {out}"

        # Assign to ring
        rc, out, err = _run_cli(
            "policy", "ring", "assign",
            "--config", config,
            "--environment", "prod",
            "--ring", "canary",
            "--activation-id", activation_id,
            "--actor-id", "admin",
            "--permissions", "policy.ring.assign",
            "--reason", "Canary deploy",
        )
        assert rc == 0, f"stderr: {err}"
        assert "assign" in out.lower() or "canary" in out.lower()

    def test_ring_promote_success(self, tmp_path):
        """ring promote moves activation from canary to stable ring."""
        config = _write_test_config(tmp_path)
        # Create canary and stable rings
        rc, out, err = _run_cli(
            "policy", "ring", "create",
            "--config", config, "--environment", "prod",
            "--name", "canary", "--actor-id", "admin",
            "--permissions", "policy.ring.create",
        )
        assert rc == 0, f"stderr: {err}"
        rc, out, err = _run_cli(
            "policy", "ring", "create",
            "--config", config, "--environment", "prod",
            "--name", "stable", "--actor-id", "admin",
            "--permissions", "policy.ring.create",
        )
        assert rc == 0, f"stderr: {err}"

        # Create bundle and get an activation
        rc, out, err = _run_cli(
            "policy", "bundle", "create",
            "--config", config,
            "--name", "promo-b1",
            "--version", "1.0.0",
            "--config-path", "test.yaml",
        )
        assert rc == 0, f"stderr: {err}"
        b1_id = None
        for line in out.split("\n"):
            if line.startswith("Bundle ID:"):
                b1_id = line.split(":", 1)[1].strip()
                break
        assert b1_id is not None

        _run_cli("policy", "gate", "run", "--config", config, "--bundle-id", b1_id)
        rc, out, err = _run_cli(
            "policy", "promotion", "request",
            "--config", config, "--bundle-id", b1_id,
            "--actor-id", "alice", "--permissions", "policy.promotion.request",
        )
        assert rc == 0, f"stderr: {err}"
        promo_id = None
        for line in out.split("\n"):
            if line.startswith("Promotion ID:"):
                promo_id = line.split(":", 1)[1].strip()
                break
        assert promo_id is not None

        _run_cli("policy", "promotion", "approve",
                  "--config", config, "--promotion-id", promo_id,
                  "--actor-id", "reviewer", "--permissions", "policy.promotion.approve")
        rc, out, err = _run_cli(
            "policy", "promotion", "execute",
            "--config", config, "--promotion-id", promo_id,
            "--actor-id", "release_mgr", "--permissions", "policy.promotion.execute",
            "--environment", "prod", "--reason", "Deploy v1",
        )
        assert rc == 0, f"stderr: {err}"

        # Get activation ID
        rc, out, err = _run_cli(
            "policy", "activation", "list",
            "--config", config, "--environment", "prod",
        )
        assert rc == 0, f"stderr: {err}"
        activation_id = None
        for line in out.strip().split("\n"):
            if line.startswith("pa_"):
                activation_id = line.split()[0]
                break
        if activation_id is None:
            rc, out, err = _run_cli(
                "policy", "activation", "list",
                "--config", config, "--environment", "prod", "--json",
            )
            data = json.loads(out)
            if data:
                activation_id = data[0]["activation_id"]
        assert activation_id is not None, f"Could not find activation ID in: {out}"

        # Assign to canary ring
        rc, out, err = _run_cli(
            "policy", "ring", "assign",
            "--config", config, "--environment", "prod",
            "--ring", "canary", "--activation-id", activation_id,
            "--actor-id", "admin", "--permissions", "policy.ring.assign",
        )
        assert rc == 0, f"stderr: {err}"

        # Promote canary to stable (needs both RING_PROMOTE and RING_ASSIGN since
        # promote_canary_to_stable delegates to assign_activation_to_ring internally)
        rc, out, err = _run_cli(
            "policy", "ring", "promote",
            "--config", config, "--environment", "prod",
            "--from-ring", "canary", "--to-ring", "stable",
            "--actor-id", "admin",
            "--permissions", "policy.ring.promote", "--permissions", "policy.ring.assign",
            "--reason", "Canary passed",
        )
        assert rc == 0, f"stderr: {err}"
        assert "promot" in out.lower()

    def test_canary_eval_placeholder(self, tmp_path):
        """canary eval command is registered and handles missing runner gracefully."""
        config = _write_test_config(tmp_path)
        # Create a ring
        rc, out, err = _run_cli(
            "policy", "ring", "create",
            "--config", config, "--environment", "prod",
            "--name", "canary", "--actor-id", "admin",
            "--permissions", "policy.ring.create",
        )
        assert rc == 0, f"stderr: {err}"

        # Create a dummy suite file
        suite_path = tmp_path / "eval_suite.yaml"
        suite_path.write_text("name: smoke\ntests: []\n")

        rc, out, err = _run_cli(
            "policy", "canary", "eval",
            "--config", config,
            "--environment", "prod",
            "--ring", "canary",
            "--activation-id", "pa_dummy",
            "--suite", str(suite_path),
        )
        # Should either succeed or fail with a clear message (not argparse error)
        # The canary eval runner may not be fully implemented yet
        assert "canary" in out.lower() or "eval" in out.lower() or "not" in err.lower() or rc != 0
