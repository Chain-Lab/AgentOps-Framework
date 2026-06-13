"""Tests for Phase 34 CLI: reload, events, and routing simulation commands."""
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


_BASE_CONFIG_34 = """
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

# Minimal config without change_events / reload manager / event store
_BASE_CONFIG_NO_PHASE34 = """
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
    """Write a full Phase 34 config (with change_events) and return its path."""
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
    return _write_config(tmp_path, _BASE_CONFIG_34.format(
        bundle_db=bundle_db, gate_db=gate_db, promo_db=promo_db,
        activation_db=activation_db, environment_db=environment_db,
        ring_db=ring_db, ring_assignment_db=ring_assignment_db,
        change_events_db=change_events_db,
    ))


def _write_test_config_no_phase34(tmp_path):
    """Write a config without change_events / reload / event_store."""
    bundle_db = str(tmp_path / "bundles.db")
    gate_db = str(tmp_path / "gates.db")
    promo_db = str(tmp_path / "promos.db")
    activation_db = str(tmp_path / "activations.db")
    environment_db = str(tmp_path / "environments.db")
    ring_db = str(tmp_path / "rings.db")
    ring_assignment_db = str(tmp_path / "ring_assignments.db")
    _cleanup_dbs(
        bundle_db, gate_db, promo_db, activation_db,
        environment_db, ring_db, ring_assignment_db,
    )
    return _write_config(tmp_path, _BASE_CONFIG_NO_PHASE34.format(
        bundle_db=bundle_db, gate_db=gate_db, promo_db=promo_db,
        activation_db=activation_db, environment_db=environment_db,
        ring_db=ring_db, ring_assignment_db=ring_assignment_db,
    ))


class TestPhase34ReloadCLI:
    """Tests for Phase 34 reload CLI commands."""

    def test_reload_request(self, tmp_path):
        """policy reload request prints JSON results."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "reload", "request",
            "--config", config,
            "--environment", "prod",
            "--ring", "canary",
            "--actor-id", "admin",
            "--reason", "Manual refresh",
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        # Should contain JSON output with reload results
        assert "refreshed" in out.lower() or "result" in out.lower() or "ring" in out.lower()

    def test_reload_status(self, tmp_path):
        """policy reload status prints cache_status JSON."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "reload", "status",
            "--config", config,
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        # Should contain cache status info
        assert "entries" in out.lower() or "cache" in out.lower() or "ttl" in out.lower()

    def test_reload_no_manager(self, tmp_path):
        """policy reload request without manager prints error."""
        config = _write_test_config_no_phase34(tmp_path)
        rc, out, err = _run_cli(
            "policy", "reload", "request",
            "--config", config,
            "--environment", "prod",
            "--ring", "canary",
            "--actor-id", "admin",
            "--reason", "Manual refresh",
        )
        assert rc != 0, f"Expected non-zero exit code, got {rc}"
        assert "reload" in err.lower() or "manager" in err.lower() or "not" in err.lower()


class TestPhase34EventsCLI:
    """Tests for Phase 34 events CLI commands."""

    def test_events_list(self, tmp_path):
        """policy events list prints JSON list."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "events", "list",
            "--config", config,
            "--environment", "prod",
            "--ring", "canary",
            "--limit", "10",
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        # Should contain events output (possibly empty list)
        assert "[" in out or "event" in out.lower() or "no " in out.lower()

    def test_events_no_store(self, tmp_path):
        """policy events list without store prints error."""
        config = _write_test_config_no_phase34(tmp_path)
        rc, out, err = _run_cli(
            "policy", "events", "list",
            "--config", config,
            "--environment", "prod",
            "--ring", "canary",
            "--limit", "10",
        )
        assert rc != 0, f"Expected non-zero exit code, got {rc}"
        assert "event" in err.lower() or "store" in err.lower() or "not" in err.lower()


class TestPhase34RoutingCLI:
    """Tests for Phase 34 routing CLI commands."""

    def test_routing_simulate(self, tmp_path):
        """policy routing simulate prints JSON with selected_ring."""
        config = _write_test_config(tmp_path)
        rc, out, err = _run_cli(
            "policy", "routing", "simulate",
            "--config", config,
            "--environment", "prod",
            "--actor-id", "user123",
        )
        assert rc == 0, f"stderr: {err}\nstdout: {out}"
        # Should contain routing result with selected_ring
        assert "selected_ring" in out.lower() or "ring" in out.lower() or "routing" in out.lower()
