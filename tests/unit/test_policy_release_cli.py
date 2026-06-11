"""Tests for Phase 29 policy release CLI commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def _run_cli(*args, cwd=None, db_dir=None):
    """Run the CLI and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "agent_app.cli", *args],
        capture_output=True,
        text=True,
        cwd=cwd or str(Path(__file__).resolve().parent.parent.parent),
    )
    return result.returncode, result.stdout, result.stderr


def _write_config(tmp_path, content: str, db_dir: str | None = None) -> str:
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
  policy_release:
    bundles:
      type: sqlite
      path: {bundle_db}
    gates:
      type: sqlite
      path: {gate_db}
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
"""


def _cleanup_dbs(bundle_db: str, gate_db: str):
    """Remove existing database files."""
    for p in [bundle_db, gate_db]:
        if os.path.exists(p):
            os.remove(p)


class TestPolicyReleaseCLI:
    """Tests for Phase 29 policy release CLI commands."""

    def test_bundle_create(self, tmp_path):
        """bundle create command succeeds."""
        bundle_db = str(tmp_path / "bundles.db")
        gate_db = str(tmp_path / "gates.db")
        _cleanup_dbs(bundle_db, gate_db)
        config = _write_config(tmp_path, _BASE_CONFIG.format(
            bundle_db=bundle_db, gate_db=gate_db
        ))
        rc, out, err = _run_cli(
            "policy", "bundle", "create",
            "--config", config,
            "--name", "test-bundle",
            "--version", "1.0.0",
            "--config-path", "examples/agentapp.yaml",
            "--description", "Test bundle",
            "--created-by", "admin",
        )
        assert rc == 0, f"stderr: {err}"
        assert "pb_" in out
        assert "test-bundle" in out

    def test_bundle_list(self, tmp_path):
        """bundle list command shows created bundles."""
        bundle_db = str(tmp_path / "bundles.db")
        gate_db = str(tmp_path / "gates.db")
        _cleanup_dbs(bundle_db, gate_db)
        config = _write_config(tmp_path, _BASE_CONFIG.format(
            bundle_db=bundle_db, gate_db=gate_db
        ))

        # Create a bundle first
        rc, out, err = _run_cli(
            "policy", "bundle", "create",
            "--config", config,
            "--name", "list-test",
            "--version", "1.0.0",
            "--config-path", "test.yaml",
        )
        assert rc == 0

        # List bundles
        rc, out, err = _run_cli(
            "policy", "bundle", "list",
            "--config", config,
        )
        assert rc == 0, f"stderr: {err}"
        assert "list-test" in out

    def test_bundle_active(self, tmp_path):
        """bundle active command shows active bundle."""
        bundle_db = str(tmp_path / "bundles.db")
        gate_db = str(tmp_path / "gates.db")
        _cleanup_dbs(bundle_db, gate_db)
        config = _write_config(tmp_path, _BASE_CONFIG.format(
            bundle_db=bundle_db, gate_db=gate_db
        ))
        rc, out, err = _run_cli(
            "policy", "bundle", "active",
            "--config", config,
        )
        assert rc == 0, f"stderr: {err}"

    def test_gate_run(self, tmp_path):
        """gate run command succeeds."""
        bundle_db = str(tmp_path / "bundles.db")
        gate_db = str(tmp_path / "gates.db")
        _cleanup_dbs(bundle_db, gate_db)
        config = _write_config(tmp_path, _BASE_CONFIG.format(
            bundle_db=bundle_db, gate_db=gate_db
        ))

        # Create a bundle first
        rc, out, err = _run_cli(
            "policy", "bundle", "create",
            "--config", config,
            "--name", "gate-test",
            "--version", "1.0.0",
            "--config-path", "test.yaml",
        )
        assert rc == 0, f"stderr: {err}"
        bundle_id = None
        for line in out.split("\n"):
            if line.startswith("Bundle ID:"):
                bundle_id = line.split(":", 1)[1].strip()
                break
        assert bundle_id is not None

        rc, out, err = _run_cli(
            "policy", "gate", "run",
            "--config", config,
            "--bundle-id", bundle_id,
        )
        assert rc == 0, f"stderr: {err}"
        assert "passed" in out.lower()

    def test_gate_list(self, tmp_path):
        """gate list command succeeds."""
        bundle_db = str(tmp_path / "bundles.db")
        gate_db = str(tmp_path / "gates.db")
        _cleanup_dbs(bundle_db, gate_db)
        config = _write_config(tmp_path, _BASE_CONFIG.format(
            bundle_db=bundle_db, gate_db=gate_db
        ))
        rc, out, err = _run_cli(
            "policy", "gate", "list",
            "--config", config,
        )
        assert rc == 0, f"stderr: {err}"

    def test_promote_success(self, tmp_path):
        """promote command succeeds when gate passes."""
        bundle_db = str(tmp_path / "bundles.db")
        gate_db = str(tmp_path / "gates.db")
        _cleanup_dbs(bundle_db, gate_db)
        config = _write_config(tmp_path, _BASE_CONFIG.format(
            bundle_db=bundle_db, gate_db=gate_db
        ))

        # Create bundle
        rc, out, err = _run_cli(
            "policy", "bundle", "create",
            "--config", config,
            "--name", "promote-test",
            "--version", "1.0.0",
            "--config-path", "test.yaml",
        )
        assert rc == 0
        bundle_id = None
        for line in out.split("\n"):
            if line.startswith("Bundle ID:"):
                bundle_id = line.split(":", 1)[1].strip()
                break

        # Run gate
        rc, _, _ = _run_cli(
            "policy", "gate", "run",
            "--config", config,
            "--bundle-id", bundle_id,
        )
        assert rc == 0

        # Promote
        rc, out, err = _run_cli(
            "policy", "bundle", "promote",
            "--config", config,
            "--bundle-id", bundle_id,
        )
        assert rc == 0, f"stderr: {err}"
        assert "active" in out

    def test_rollback_success(self, tmp_path):
        """rollback command succeeds."""
        bundle_db = str(tmp_path / "bundles.db")
        gate_db = str(tmp_path / "gates.db")
        _cleanup_dbs(bundle_db, gate_db)
        config = _write_config(tmp_path, _BASE_CONFIG.format(
            bundle_db=bundle_db, gate_db=gate_db
        ))

        # Create and promote b1
        rc, out, err = _run_cli(
            "policy", "bundle", "create",
            "--config", config,
            "--name", "rollback-b1",
            "--version", "1.0.0",
            "--config-path", "test.yaml",
        )
        assert rc == 0
        b1_id = None
        for line in out.split("\n"):
            if line.startswith("Bundle ID:"):
                b1_id = line.split(":", 1)[1].strip()
                break
        _run_cli("policy", "gate", "run", "--config", config, "--bundle-id", b1_id)
        _run_cli("policy", "bundle", "promote", "--config", config, "--bundle-id", b1_id)

        # Create b2 and promote it
        rc, out, err = _run_cli(
            "policy", "bundle", "create",
            "--config", config,
            "--name", "rollback-b2",
            "--version", "2.0.0",
            "--config-path", "test.yaml",
        )
        assert rc == 0
        b2_id = None
        for line in out.split("\n"):
            if line.startswith("Bundle ID:"):
                b2_id = line.split(":", 1)[1].strip()
                break
        _run_cli("policy", "gate", "run", "--config", config, "--bundle-id", b2_id)
        _run_cli("policy", "bundle", "promote", "--config", config, "--bundle-id", b2_id)

        # Rollback to b1
        rc, out, err = _run_cli(
            "policy", "bundle", "rollback",
            "--config", config,
            "--bundle-id", b1_id,
        )
        assert rc == 0, f"stderr: {err}"
        assert "active" in out

    def test_invalid_bundle_id_exits_nonzero(self, tmp_path):
        """Invalid bundle ID causes non-zero exit."""
        bundle_db = str(tmp_path / "bundles.db")
        gate_db = str(tmp_path / "gates.db")
        _cleanup_dbs(bundle_db, gate_db)
        config = _write_config(tmp_path, _BASE_CONFIG.format(
            bundle_db=bundle_db, gate_db=gate_db
        ))
        rc, out, err = _run_cli(
            "policy", "bundle", "promote",
            "--config", config,
            "--bundle-id", "pb_nonexistent",
        )
        assert rc != 0


# Phase 30: policy promotion CLI tests
_BASE_CONFIG_30 = """
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
    require_promotion_approval: true
    allow_gate_bypass: false
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
"""


def _cleanup_dbs_30(bundle_db: str, gate_db: str, promo_db: str):
    """Remove existing database files for Phase 30 tests."""
    for p in [bundle_db, gate_db, promo_db]:
        if os.path.exists(p):
            os.remove(p)


class TestPolicyPromotionCLI:
    """Tests for Phase 30 promotion CLI commands."""

    def _write_config(self, tmp_path):
        bundle_db = str(tmp_path / "bundles.db")
        gate_db = str(tmp_path / "gates.db")
        promo_db = str(tmp_path / "promos.db")
        _cleanup_dbs_30(bundle_db, gate_db, promo_db)
        config = _write_config(tmp_path, _BASE_CONFIG_30.format(
            bundle_db=bundle_db, gate_db=gate_db, promo_db=promo_db,
        ))
        return config

    def test_promotion_request_success(self, tmp_path):
        """promotion request command succeeds."""
        config = self._write_config(tmp_path)
        # Create bundle first
        rc, out, err = _run_cli("policy", "bundle", "create",
            "--config", config, "--name", "promo-test",
            "--version", "1.0.0", "--config-path", "test.yaml")
        assert rc == 0, f"stderr: {err}"
        bundle_id = None
        for line in out.split("\n"):
            if line.startswith("Bundle ID:"):
                bundle_id = line.split(":", 1)[1].strip()
                break
        assert bundle_id is not None

        rc, out, err = _run_cli("policy", "promotion", "request",
            "--config", config, "--bundle-id", bundle_id,
            "--actor-id", "alice", "--permissions", "policy.promotion.request",
            "--reason", "Ready for release")
        assert rc == 0, f"stderr: {err}"
        assert "Promotion ID:" in out
        assert "pending" in out

    def test_promotion_request_permission_denied(self, tmp_path):
        """promotion request fails without proper permission."""
        config = self._write_config(tmp_path)
        rc, out, err = _run_cli("policy", "promotion", "request",
            "--config", config, "--bundle-id", "pb_test",
            "--actor-id", "alice", "--permissions", "policy.bundle.create",
            "--reason", "hacking")
        assert rc != 0
        assert "Permission denied" in err or "Permission denied" in out

    def test_promotion_list_empty(self, tmp_path):
        """promotion list shows message when no requests exist."""
        config = self._write_config(tmp_path)
        rc, out, err = _run_cli("policy", "promotion", "list", "--config", config)
        assert rc == 0
        assert "No promotion requests" in out

    def test_promotion_approve(self, tmp_path):
        """promotion approve command succeeds."""
        config = self._write_config(tmp_path)
        # Create bundle first
        rc, out, err = _run_cli("policy", "bundle", "create",
            "--config", config, "--name", "approve-test",
            "--version", "1.0.0", "--config-path", "test.yaml")
        assert rc == 0, f"stderr: {err}"
        bundle_id = None
        for line in out.split("\n"):
            if line.startswith("Bundle ID:"):
                bundle_id = line.split(":", 1)[1].strip()
                break
        assert bundle_id is not None

        rc, out, _ = _run_cli("policy", "promotion", "request",
            "--config", config, "--bundle-id", bundle_id,
            "--actor-id", "alice", "--permissions", "policy.promotion.request",
            "--reason", "release")
        assert rc == 0
        promo_id = None
        for line in out.split("\n"):
            if line.startswith("Promotion ID:"):
                promo_id = line.split(":", 1)[1].strip()
                break
        assert promo_id is not None
        rc, out, err = _run_cli("policy", "promotion", "approve",
            "--config", config, "--promotion-id", promo_id,
            "--actor-id", "reviewer", "--permissions", "policy.promotion.approve",
            "--reason", "Looks good")
        assert rc == 0, f"stderr: {err}"
        assert "approved" in out

    def test_promotion_reject(self, tmp_path):
        """promotion reject command succeeds."""
        config = self._write_config(tmp_path)
        # Create bundle first
        rc, out, err = _run_cli("policy", "bundle", "create",
            "--config", config, "--name", "reject-test",
            "--version", "1.0.0", "--config-path", "test.yaml")
        assert rc == 0, f"stderr: {err}"
        bundle_id = None
        for line in out.split("\n"):
            if line.startswith("Bundle ID:"):
                bundle_id = line.split(":", 1)[1].strip()
                break
        assert bundle_id is not None

        rc, out, _ = _run_cli("policy", "promotion", "request",
            "--config", config, "--bundle-id", bundle_id,
            "--actor-id", "alice", "--permissions", "policy.promotion.request")
        assert rc == 0, f"stderr: {err}"
        promo_id = None
        for line in out.split("\n"):
            if line.startswith("Promotion ID:"):
                promo_id = line.split(":", 1)[1].strip()
                break
        assert promo_id is not None
        rc, out, err = _run_cli("policy", "promotion", "reject",
            "--config", config, "--promotion-id", promo_id,
            "--actor-id", "reviewer", "--permissions", "policy.promotion.reject",
            "--reason", "Too risky")
        assert rc == 0, f"stderr: {err}"
        assert "rejected" in out

    def test_promotion_execute_pending_fails(self, tmp_path):
        """promotion execute fails on pending (not approved) request."""
        config = self._write_config(tmp_path)
        # Create bundle first
        rc, out, err = _run_cli("policy", "bundle", "create",
            "--config", config, "--name", "execute-test",
            "--version", "1.0.0", "--config-path", "test.yaml")
        assert rc == 0, f"stderr: {err}"
        bundle_id = None
        for line in out.split("\n"):
            if line.startswith("Bundle ID:"):
                bundle_id = line.split(":", 1)[1].strip()
                break
        assert bundle_id is not None

        rc, out, _ = _run_cli("policy", "promotion", "request",
            "--config", config, "--bundle-id", bundle_id,
            "--actor-id", "alice", "--permissions", "policy.promotion.request")
        assert rc == 0, f"stderr: {err}"
        promo_id = None
        for line in out.split("\n"):
            if line.startswith("Promotion ID:"):
                promo_id = line.split(":", 1)[1].strip()
                break
        assert promo_id is not None
        rc, out, err = _run_cli("policy", "promotion", "execute",
            "--config", config, "--promotion-id", promo_id,
            "--actor-id", "release_manager", "--permissions", "policy.promotion.execute")
        assert rc != 0
        assert "approved" in err or "approved" in out
