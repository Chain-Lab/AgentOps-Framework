"""Tests for Phase 32 CLI: environment disable/enable/list and activation rollback."""
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


_BASE_CONFIG_32 = """
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
    require_promotion_approval: true
    allow_gate_bypass: false
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
"""


def _cleanup_dbs(bundle_db: str, gate_db: str, promo_db: str, activation_db: str, environment_db: str):
    """Remove existing database files."""
    for p in [bundle_db, gate_db, promo_db, activation_db, environment_db]:
        if os.path.exists(p):
            os.remove(p)


def _write_test_config(tmp_path):
    """Write a minimal Phase 32 config to tmp_path and return its path."""
    bundle_db = str(tmp_path / "bundles.db")
    gate_db = str(tmp_path / "gates.db")
    promo_db = str(tmp_path / "promos.db")
    activation_db = str(tmp_path / "activations.db")
    environment_db = str(tmp_path / "environments.db")
    _cleanup_dbs(bundle_db, gate_db, promo_db, activation_db, environment_db)
    return _write_config(tmp_path, _BASE_CONFIG_32.format(
        bundle_db=bundle_db, gate_db=gate_db, promo_db=promo_db,
        activation_db=activation_db, environment_db=environment_db,
    ))


class TestPhase32EnvironmentCLI:
    """Tests for Phase 32 environment CLI commands."""

    def test_environment_list(self, tmp_path):
        """environment list command runs without error."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "environment", "list",
            "--config", config,
        )
        assert rc == 0, f"stderr: {err}"
        # Should show either environments or "No" message
        assert "environment" in out.lower() or "No" in out or "enabled" in out.lower()

    def test_environment_disable_success(self, tmp_path):
        """environment disable succeeds with required options."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "environment", "disable",
            "--config", config,
            "--environment", "prod",
            "--actor-id", "admin",
            "--reason", "Maintenance window",
            "--permissions", "policy.environment.disable",
        )
        assert rc == 0, f"stderr: {err}"
        assert "disabled" in out.lower()

    def test_environment_disable_without_reason_fails(self, tmp_path):
        """environment disable without --reason exits non-zero (argparse required)."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "environment", "disable",
            "--config", config,
            "--environment", "prod",
            "--actor-id", "admin",
            # --reason is missing; argparse should reject this
            "--permissions", "policy.environment.disable",
        )
        assert rc != 0

    def test_environment_enable_success(self, tmp_path):
        """environment enable succeeds after disable."""
        config = _write_test_config(tmp_path)
        # Disable first
        rc, out, err = _run_cli(
            "policy", "environment", "disable",
            "--config", config,
            "--environment", "prod",
            "--actor-id", "admin",
            "--reason", "Maintenance",
            "--permissions", "policy.environment.disable",
        )
        assert rc == 0, f"stderr: {err}"
        # Now enable
        rc, out, err = _run_cli(
            "policy", "environment", "enable",
            "--config", config,
            "--environment", "prod",
            "--actor-id", "admin",
            "--permissions", "policy.environment.enable",
        )
        assert rc == 0, f"stderr: {err}"
        assert "enabled" in out.lower()

    def test_environment_disable_permission_denied(self, tmp_path):
        """environment disable without proper permission fails."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "environment", "disable",
            "--config", config,
            "--environment", "prod",
            "--actor-id", "unauthorized",
            "--reason", "Trying to disable",
            "--permissions", "policy.bundle.create",
        )
        assert rc != 0
        assert "permission denied" in err.lower() or "permission denied" in out.lower()


class TestPhase32ActivationRollbackCLI:
    """Tests for Phase 32 activation rollback CLI command."""

    def test_activation_rollback_success(self, tmp_path):
        """activation rollback succeeds with required options and prior activations."""
        config = _write_test_config(tmp_path)

        # Create bundle 1
        rc, out, err = _run_cli(
            "policy", "bundle", "create",
            "--config", config,
            "--name", "rollback-b1",
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

        # Run gate and promote bundle 1
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
        _run_cli("policy", "promotion", "execute",
                  "--config", config, "--promotion-id", promo_id,
                  "--actor-id", "release_mgr", "--permissions", "policy.promotion.execute",
                  "--environment", "prod", "--reason", "Deploy v1")

        # Create bundle 2
        rc, out, err = _run_cli(
            "policy", "bundle", "create",
            "--config", config,
            "--name", "rollback-b2",
            "--version", "2.0.0",
            "--config-path", "test.yaml",
        )
        assert rc == 0, f"stderr: {err}"
        b2_id = None
        for line in out.split("\n"):
            if line.startswith("Bundle ID:"):
                b2_id = line.split(":", 1)[1].strip()
                break
        assert b2_id is not None

        # Promote bundle 2
        _run_cli("policy", "gate", "run", "--config", config, "--bundle-id", b2_id)
        rc, out, err = _run_cli(
            "policy", "promotion", "request",
            "--config", config, "--bundle-id", b2_id,
            "--actor-id", "alice", "--permissions", "policy.promotion.request",
        )
        assert rc == 0, f"stderr: {err}"
        promo2_id = None
        for line in out.split("\n"):
            if line.startswith("Promotion ID:"):
                promo2_id = line.split(":", 1)[1].strip()
                break
        assert promo2_id is not None
        _run_cli("policy", "promotion", "approve",
                  "--config", config, "--promotion-id", promo2_id,
                  "--actor-id", "reviewer", "--permissions", "policy.promotion.approve")
        _run_cli("policy", "promotion", "execute",
                  "--config", config, "--promotion-id", promo2_id,
                  "--actor-id", "release_mgr", "--permissions", "policy.promotion.execute",
                  "--environment", "prod", "--reason", "Deploy v2")

        # Now rollback
        rc, out, err = _run_cli(
            "policy", "activation", "rollback",
            "--config", config,
            "--environment", "prod",
            "--actor-id", "admin",
            "--reason", "Revert to v1",
            "--permissions", "policy.rollback.execute",
        )
        assert rc == 0, f"stderr: {err}"
        assert "rollback" in out.lower() or "pa_" in out

    def test_activation_rollback_no_previous_fails(self, tmp_path):
        """activation rollback with no previous activation fails."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "activation", "rollback",
            "--config", config,
            "--environment", "prod",
            "--actor-id", "admin",
            "--permissions", "policy.rollback.execute",
        )
        # No activations exist, so should fail
        assert rc != 0

    def test_activation_rollback_permission_denied(self, tmp_path):
        """activation rollback without permission fails."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "activation", "rollback",
            "--config", config,
            "--environment", "prod",
            "--actor-id", "unauthorized",
            "--permissions", "policy.bundle.create",
        )
        assert rc != 0
        assert "permission denied" in err.lower() or "permission denied" in out.lower()
