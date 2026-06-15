"""Tests for Phase 36 Task 7 / Phase 37 Task 6: CLI rollout approval commands."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

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


_BASE_CONFIG_36 = """
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
      approvals:
        type: sqlite
        path: {approval_db}
    require_promotion_approval: true
    allow_gate_bypass: false
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
"""

# Config with require_reason: true
_BASE_CONFIG_REQUIRE_REASON = """
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
      approvals:
        type: sqlite
        path: {approval_db}
        require_reason: true
    require_promotion_approval: true
    allow_gate_bypass: false
    rules:
      - name: safe_default
        max_changed_ratio: 0.10
        max_failed_replays: 0
"""

# Steps YAML with a step that requires approval
_STEPS_YAML_APPROVAL = """
steps:
  - step_id: s1
    step_type: activate
    environment: prod
    ring_name: canary
    requires_approval: true
  - step_id: s2
    step_type: assign_ring
    environment: prod
    ring_name: stable
    require_previous_step: s1
"""

# Permissions
_ROLLOUT_CREATE_PERM = "policy.rollout.create"
_ROLLOUT_START_PERM = "policy.rollout.start"
_ROLLOUT_EXECUTE_PERM = "policy.rollout.execute"
_ROLLOUT_APPROVAL_REQUEST_PERM = "policy.rollout.approval.request"
_ROLLOUT_APPROVAL_APPROVE_PERM = "policy.rollout.approval.approve"
_ROLLOUT_APPROVAL_REJECT_PERM = "policy.rollout.approval.reject"
_ROLLOUT_APPROVAL_VIEW_PERM = "policy.rollout.approval.view"


def _cleanup_dbs(*db_paths):
    """Remove existing database files."""
    for p in db_paths:
        if os.path.exists(p):
            os.remove(p)


def _write_test_config(tmp_path):
    """Write a full Phase 36 config (with rollouts + approvals) and return its path."""
    bundle_db = str(tmp_path / "bundles.db")
    gate_db = str(tmp_path / "gates.db")
    promo_db = str(tmp_path / "promos.db")
    activation_db = str(tmp_path / "activations.db")
    environment_db = str(tmp_path / "environments.db")
    ring_db = str(tmp_path / "rings.db")
    ring_assignment_db = str(tmp_path / "ring_assignments.db")
    change_events_db = str(tmp_path / "change_events.db")
    rollout_db = str(tmp_path / "rollouts.db")
    approval_db = str(tmp_path / "approvals.db")
    _cleanup_dbs(
        bundle_db, gate_db, promo_db, activation_db,
        environment_db, ring_db, ring_assignment_db, change_events_db,
        rollout_db, approval_db,
    )
    return _write_config(tmp_path, _BASE_CONFIG_36.format(
        bundle_db=bundle_db, gate_db=gate_db, promo_db=promo_db,
        activation_db=activation_db, environment_db=environment_db,
        ring_db=ring_db, ring_assignment_db=ring_assignment_db,
        change_events_db=change_events_db, rollout_db=rollout_db,
        approval_db=approval_db,
    ))


def _write_test_config_require_reason(tmp_path):
    """Write a config with require_reason: true and return its path."""
    bundle_db = str(tmp_path / "bundles.db")
    gate_db = str(tmp_path / "gates.db")
    promo_db = str(tmp_path / "promos.db")
    activation_db = str(tmp_path / "activations.db")
    environment_db = str(tmp_path / "environments.db")
    ring_db = str(tmp_path / "rings.db")
    ring_assignment_db = str(tmp_path / "ring_assignments.db")
    change_events_db = str(tmp_path / "change_events.db")
    rollout_db = str(tmp_path / "rollouts.db")
    approval_db = str(tmp_path / "approvals.db")
    _cleanup_dbs(
        bundle_db, gate_db, promo_db, activation_db,
        environment_db, ring_db, ring_assignment_db, change_events_db,
        rollout_db, approval_db,
    )
    return _write_config(tmp_path, _BASE_CONFIG_REQUIRE_REASON.format(
        bundle_db=bundle_db, gate_db=gate_db, promo_db=promo_db,
        activation_db=activation_db, environment_db=environment_db,
        ring_db=ring_db, ring_assignment_db=ring_assignment_db,
        change_events_db=change_events_db, rollout_db=rollout_db,
        approval_db=approval_db,
    ))


def _write_steps_file(tmp_path):
    """Write a steps YAML file with approval-required step and return its path."""
    steps_path = tmp_path / "rollout_steps.yaml"
    steps_path.write_text(_STEPS_YAML_APPROVAL)
    return str(steps_path)


def _create_and_start_plan(config, steps_file):
    """Create and start a rollout plan, return (rollout_id, step_id_for_approval)."""
    # Create
    rc, out, err = _run_cli(
        "policy", "rollout", "create",
        "--config", config,
        "--name", "approval-test",
        "--bundle-id", "bnd_approval",
        "--steps-file", steps_file,
        "--actor-id", "admin",
        "--permissions", _ROLLOUT_CREATE_PERM,
    )
    assert rc == 0, f"create failed: stderr={err}, stdout={out}"
    data = json.loads(out)
    rollout_id = data["rollout_id"]

    # Start
    rc, out, err = _run_cli(
        "policy", "rollout", "start",
        "--config", config,
        "--rollout-id", rollout_id,
        "--actor-id", "admin",
        "--permissions", _ROLLOUT_START_PERM,
    )
    assert rc == 0, f"start failed: stderr={err}, stdout={out}"

    return rollout_id, "s1"


class TestPhase36RolloutApprovalCLI:
    """Tests for Phase 36 rollout approval CLI commands."""

    def test_approval_list(self, tmp_path):
        """policy rollout approval list lists rollout step approvals as JSON."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        rollout_id, step_id = _create_and_start_plan(config, steps_file)

        # Request approval first
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "request",
            "--config", config,
            "--rollout-id", rollout_id,
            "--step-id", step_id,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_APPROVAL_REQUEST_PERM,
            "--reason", "Needs review",
        )
        assert rc == 0, f"request failed: stderr={err}, stdout={out}"

        # List approvals
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "list",
            "--config", config,
            "--json",
        )
        assert rc == 0, f"list failed: stderr={err}, stdout={out}"
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["status"] == "pending"
        assert data[0]["rollout_id"] == rollout_id
        assert data[0]["step_id"] == step_id

    def test_approval_request(self, tmp_path):
        """policy rollout approval request creates an approval for a step."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        rollout_id, step_id = _create_and_start_plan(config, steps_file)

        # Request approval
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "request",
            "--config", config,
            "--rollout-id", rollout_id,
            "--step-id", step_id,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_APPROVAL_REQUEST_PERM,
            "--reason", "Needs review",
        )
        assert rc == 0, f"request failed: stderr={err}, stdout={out}"
        data = json.loads(out)
        assert data["status"] == "pending"
        assert data["rollout_id"] == rollout_id
        assert data["step_id"] == step_id
        assert data["requested_by"] == "admin"
        assert "approval_id" in data

    def test_approval_approve(self, tmp_path):
        """policy rollout approval approve approves a pending approval and unblocks the step."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        rollout_id, step_id = _create_and_start_plan(config, steps_file)

        # Request approval
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "request",
            "--config", config,
            "--rollout-id", rollout_id,
            "--step-id", step_id,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_APPROVAL_REQUEST_PERM,
        )
        assert rc == 0, f"request failed: stderr={err}, stdout={out}"
        approval_id = json.loads(out)["approval_id"]

        # Approve
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "approve",
            "--config", config,
            "--approval-id", approval_id,
            "--actor-id", "approver",
            "--permissions", _ROLLOUT_APPROVAL_APPROVE_PERM,
            "--reason", "Looks good",
        )
        assert rc == 0, f"approve failed: stderr={err}, stdout={out}"
        data = json.loads(out)
        assert data["status"] == "approved"
        assert data["approval_id"] == approval_id
        assert data["resolved_by"] == "approver"

    def test_approval_reject(self, tmp_path):
        """policy rollout approval reject rejects a pending approval and fails the step/plan."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        rollout_id, step_id = _create_and_start_plan(config, steps_file)

        # Request approval
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "request",
            "--config", config,
            "--rollout-id", rollout_id,
            "--step-id", step_id,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_APPROVAL_REQUEST_PERM,
        )
        assert rc == 0, f"request failed: stderr={err}, stdout={out}"
        approval_id = json.loads(out)["approval_id"]

        # Reject
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "reject",
            "--config", config,
            "--approval-id", approval_id,
            "--actor-id", "rejecter",
            "--permissions", _ROLLOUT_APPROVAL_REJECT_PERM,
            "--reason", "Not safe",
        )
        assert rc == 0, f"reject failed: stderr={err}, stdout={out}"
        data = json.loads(out)
        assert data["status"] == "rejected"
        assert data["approval_id"] == approval_id
        assert data["resolved_by"] == "rejecter"

    def test_permission_denied_exits_nonzero(self, tmp_path):
        """Missing required permission exits with non-zero code."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        rollout_id, step_id = _create_and_start_plan(config, steps_file)

        # Request approval without the required permission
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "request",
            "--config", config,
            "--rollout-id", rollout_id,
            "--step-id", step_id,
            "--actor-id", "unauthorized",
            # No approval permissions provided
        )
        assert rc != 0, "Expected non-zero exit code for permission denied"
        assert "permission" in err.lower() or "denied" in err.lower()

    def test_missing_required_reason_exits_nonzero(self, tmp_path):
        """With require_reason=True, missing reason exits non-zero."""
        config = _write_test_config_require_reason(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        rollout_id, step_id = _create_and_start_plan(config, steps_file)

        # Request approval (this doesn't need reason, but approve/reject do)
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "request",
            "--config", config,
            "--rollout-id", rollout_id,
            "--step-id", step_id,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_APPROVAL_REQUEST_PERM,
        )
        assert rc == 0, f"request failed: stderr={err}, stdout={out}"
        approval_id = json.loads(out)["approval_id"]

        # Try to approve without reason (require_reason is true)
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "approve",
            "--config", config,
            "--approval-id", approval_id,
            "--actor-id", "approver",
            "--permissions", _ROLLOUT_APPROVAL_APPROVE_PERM,
            # No --reason provided
        )
        assert rc != 0, "Expected non-zero exit code when reason is required but missing"
        assert "reason" in err.lower()

    def test_already_resolved_exits_nonzero(self, tmp_path):
        """Approving an already-approved approval exits non-zero."""
        config = _write_test_config(tmp_path)
        steps_file = _write_steps_file(tmp_path)
        rollout_id, step_id = _create_and_start_plan(config, steps_file)

        # Request approval
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "request",
            "--config", config,
            "--rollout-id", rollout_id,
            "--step-id", step_id,
            "--actor-id", "admin",
            "--permissions", _ROLLOUT_APPROVAL_REQUEST_PERM,
        )
        assert rc == 0, f"request failed: stderr={err}, stdout={out}"
        approval_id = json.loads(out)["approval_id"]

        # Approve once
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "approve",
            "--config", config,
            "--approval-id", approval_id,
            "--actor-id", "approver",
            "--permissions", _ROLLOUT_APPROVAL_APPROVE_PERM,
            "--reason", "Looks good",
        )
        assert rc == 0, f"first approve failed: stderr={err}, stdout={out}"

        # Try to approve again (already resolved)
        rc, out, err = _run_cli(
            "policy", "rollout", "approval", "approve",
            "--config", config,
            "--approval-id", approval_id,
            "--actor-id", "approver2",
            "--permissions", _ROLLOUT_APPROVAL_APPROVE_PERM,
            "--reason", "Double approve",
        )
        assert rc != 0, "Expected non-zero exit code for already-resolved approval"
        assert "pending" in err.lower() or "status" in err.lower() or "already" in err.lower()


class TestPhase37ApprovalCLIPolicyFields:
    """Tests for Phase 37 Task 6: policy-aware approval CLI updates."""

    def test_approval_to_dict_includes_policy(self):
        """_approval_to_dict includes policy, decisions, expires_at fields."""
        from agent_app.cli import _approval_to_dict
        from agent_app.governance.policy_rollout_approval import (
            RolloutApprovalPolicy,
            RolloutApprovalPolicyType,
            RolloutStepApproval,
            RolloutStepApprovalStatus,
        )

        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
            allowed_approver_roles=["admin", "reviewer"],
            allowed_approver_permissions=["policy.rollout.approval.approve"],
            prohibit_requester_approval=True,
            prohibit_creator_approval=False,
            expires_after_seconds=3600,
            require_reason=True,
        )
        approval = RolloutStepApproval(
            approval_id="rsa_test",
            rollout_id="ro_test",
            step_id="s1",
            bundle_id="bnd_test",
            environment="prod",
            ring_name="canary",
            requested_by="admin",
            requested_reason="Needs review",
            status=RolloutStepApprovalStatus.PENDING,
            resolved_by=None,
            resolved_reason=None,
            created_at=datetime.now(timezone.utc),
            resolved_at=None,
            policy=policy,
            decisions=[],
            expires_at=datetime.now(timezone.utc),
        )

        result = _approval_to_dict(approval)

        # Policy field
        assert result["policy"] is not None
        assert result["policy"]["policy_type"] == "quorum"
        assert result["policy"]["required_approvals"] == 2
        assert result["policy"]["allowed_approver_roles"] == ["admin", "reviewer"]
        assert result["policy"]["allowed_approver_permissions"] == ["policy.rollout.approval.approve"]
        assert result["policy"]["prohibit_requester_approval"] is True
        assert result["policy"]["prohibit_creator_approval"] is False
        assert result["policy"]["expires_after_seconds"] == 3600
        assert result["policy"]["require_reason"] is True

        # expires_at field
        assert result["expires_at"] is not None

        # required_approvals and current_approvals
        assert result["required_approvals"] == 2
        assert result["current_approvals"] == 0

    def test_approval_to_dict_includes_decisions(self):
        """_approval_to_dict includes decisions array with decision details."""
        from agent_app.cli import _approval_to_dict
        from agent_app.governance.policy_rollout_approval import (
            RolloutApprovalDecision,
            RolloutApprovalDecisionType,
            RolloutApprovalPolicy,
            RolloutApprovalPolicyType,
            RolloutStepApproval,
            RolloutStepApprovalStatus,
        )

        now = datetime.now(timezone.utc)
        decision = RolloutApprovalDecision(
            decision_id="rsd_test1",
            approval_id="rsa_test",
            decision_type=RolloutApprovalDecisionType.APPROVE,
            decided_by="reviewer1",
            reason="Looks good",
            roles=["reviewer"],
            permissions=["policy.rollout.approval.approve"],
            created_at=now,
        )
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = RolloutStepApproval(
            approval_id="rsa_test",
            rollout_id="ro_test",
            step_id="s1",
            bundle_id="bnd_test",
            environment="prod",
            ring_name="canary",
            requested_by="admin",
            requested_reason="Needs review",
            status=RolloutStepApprovalStatus.PENDING,
            resolved_by=None,
            resolved_reason=None,
            created_at=now,
            resolved_at=None,
            policy=policy,
            decisions=[decision],
            expires_at=None,
        )

        result = _approval_to_dict(approval)

        # Decisions array
        assert len(result["decisions"]) == 1
        d = result["decisions"][0]
        assert d["decision_id"] == "rsd_test1"
        assert d["decision_type"] == "approve"
        assert d["decided_by"] == "reviewer1"
        assert d["reason"] == "Looks good"
        assert d["roles"] == ["reviewer"]
        assert d["permissions"] == ["policy.rollout.approval.approve"]

        # current_approvals counts approve decisions
        assert result["current_approvals"] == 1
        assert result["required_approvals"] == 2

    def test_approval_to_dict_no_policy_backward_compat(self):
        """_approval_to_dict handles approval without policy/decisions gracefully."""
        from agent_app.cli import _approval_to_dict

        # Create a mock approval without policy/decisions attributes
        approval = MagicMock()
        approval.approval_id = "rsa_old"
        approval.rollout_id = "ro_old"
        approval.step_id = "s1"
        approval.bundle_id = "bnd_old"
        approval.environment = "prod"
        approval.ring_name = "canary"
        approval.requested_by = "admin"
        approval.requested_reason = None
        approval.status = MagicMock(value="pending")
        approval.resolved_by = None
        approval.resolved_reason = None
        approval.created_at = datetime.now(timezone.utc)
        approval.resolved_at = None
        # No policy attribute
        del approval.policy
        # No decisions attribute
        del approval.decisions
        # No expires_at attribute
        del approval.expires_at

        result = _approval_to_dict(approval)

        assert result["policy"] is None
        assert result["decisions"] == []
        assert result["expires_at"] is None
        assert result["required_approvals"] == 1
        assert result["current_approvals"] == 0

    def test_build_context_passes_roles(self):
        """_build_context with roles creates RunContext with those roles."""
        from agent_app.cli import _build_context

        ctx = _build_context("test_user", ["perm1"], roles=["admin", "reviewer"])
        assert ctx.roles == ["admin", "reviewer"]
        assert ctx.user_id == "test_user"
        assert ctx.permissions == ["perm1"]

    def test_build_context_no_roles_default_empty(self):
        """_build_context without roles defaults to empty list."""
        from agent_app.cli import _build_context

        ctx = _build_context("test_user", ["perm1"])
        assert ctx.roles == []
