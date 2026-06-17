"""Phase 43 Task 6: Tests for CLI rollout gate commands."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.governance.policy_gate import PolicyGateResult, PolicyGateStatus
from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)
from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)
from agent_app.governance.policy_rollout_gate import (
    RolloutGateExecutionResult,
    RolloutGateExecutionStatus,
)
from tests.conftest import _run_async


# -- Test fixtures and helpers --


def _make_step(
    step_id: str = "prod_canary",
    simulation_gate_mode: str = "auto",
    **overrides,
) -> RolloutStep:
    """Build a minimal rollout step for testing."""
    defaults = dict(
        step_id=step_id,
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
        status=RolloutStepStatus.PENDING,
        simulation_gate_mode=simulation_gate_mode,
    )
    defaults.update(overrides)
    return RolloutStep(**defaults)


def _make_plan(
    rollout_id: str = "ro_test001",
    steps: list[RolloutStep] | None = None,
    **overrides,
) -> RolloutPlan:
    """Build a minimal rollout plan for testing."""
    if steps is None:
        steps = [_make_step()]
    defaults = dict(
        rollout_id=rollout_id,
        name="test-rollout",
        bundle_id="bnd_test001",
        steps=steps,
        status=RolloutPlanStatus.ACTIVE,
        created_by="tester",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return RolloutPlan(**defaults)


def _make_execution_result(
    status: RolloutGateExecutionStatus = RolloutGateExecutionStatus.SATISFIED,
    **overrides,
) -> RolloutGateExecutionResult:
    """Build a minimal gate execution result for testing."""
    defaults = dict(
        execution_id="rge_test001",
        rollout_id="ro_test001",
        step_id="prod_canary",
        status=status,
        requirement_id="rgr_test001",
        gate_result_id="gr_test001",
        simulation_id="psim_test001",
        action_taken="auto_passed",
        reason=None,
        error=None,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return RolloutGateExecutionResult(**defaults)


def _make_requirement(
    status: ReleaseGateRequirementStatus = ReleaseGateRequirementStatus.SATISFIED,
    **overrides,
) -> ReleaseGateRequirement:
    """Build a minimal gate requirement for testing."""
    defaults = dict(
        requirement_id="rgr_test001",
        source_type="rollout_step",
        source_id="ro_test001:prod_canary",
        status=status,
        max_age_seconds=None,
        gate_result_id="gr_test001",
        simulation_id="psim_test001",
        satisfied_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ReleaseGateRequirement(**defaults)


def _make_app(
    gate_automation_service=None,
    rollout_gate_automation_service=None,
    rollout_store=None,
) -> MagicMock:
    """Create a mock app with rollout-gate-related attributes."""
    app = MagicMock()
    app._release_gate_automation_service = gate_automation_service
    app.rollout_gate_automation_service = rollout_gate_automation_service
    app._rollout_store = rollout_store
    app.rollout_store = rollout_store
    return app


def _make_rollout_store(plan: RolloutPlan | None = None) -> MagicMock:
    """Create a mock rollout store."""
    store = MagicMock()
    store.get = AsyncMock(return_value=plan)
    return store


# -- Tests --


class TestRolloutGateRun:
    def test_run_basic_pass_exit_0(self, capsys):
        """policy rollout gate run with passing gate exits 0."""
        from agent_app.cli import _cmd_policy_rollout_gate_run

        plan = _make_plan()
        result = _make_execution_result(status=RolloutGateExecutionStatus.SATISFIED)
        mock_gate_service = MagicMock()
        mock_gate_service.run_step_gate = AsyncMock(return_value=result)

        store = _make_rollout_store(plan)
        app = _make_app(rollout_gate_automation_service=mock_gate_service, rollout_store=store)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_test001",
            step_id="prod_canary",
            actor_id="release_manager",
            permissions=["policy.rollout.gate.run"],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_gate_run(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "ro_test001" in captured.out
        assert "prod_canary" in captured.out
        assert "satisfied" in captured.out.lower()
        assert "rgr_test001" in captured.out

    def test_run_basic_fail_exit_nonzero(self, capsys):
        """policy rollout gate run with failing gate exits non-zero."""
        from agent_app.cli import _cmd_policy_rollout_gate_run

        plan = _make_plan()
        result = _make_execution_result(
            status=RolloutGateExecutionStatus.FAILED,
            action_taken="auto_failed",
            reason="Gate failed with status failed",
        )
        mock_gate_service = MagicMock()
        mock_gate_service.run_step_gate = AsyncMock(return_value=result)

        store = _make_rollout_store(plan)
        app = _make_app(rollout_gate_automation_service=mock_gate_service, rollout_store=store)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_test001",
            step_id="prod_canary",
            actor_id="release_manager",
            permissions=["policy.rollout.gate.run"],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_gate_run(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "failed" in captured.out.lower()

    def test_run_missing_service_exits_nonzero(self, capsys):
        """policy rollout gate run with no service exits non-zero."""
        from agent_app.cli import _cmd_policy_rollout_gate_run

        app = _make_app(rollout_gate_automation_service=None)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_test001",
            step_id="prod_canary",
            actor_id="release_manager",
            permissions=[],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_gate_run(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "not configured" in captured.err.lower() or "not configured" in captured.out.lower()


class TestRolloutGateStatus:
    def test_status_text_output(self, capsys):
        """policy rollout gate status shows text output."""
        from agent_app.cli import _cmd_policy_rollout_gate_status

        plan = _make_plan()
        result = _make_execution_result(status=RolloutGateExecutionStatus.SATISFIED)
        mock_gate_service = MagicMock()
        mock_gate_service.check_step_gate = AsyncMock(return_value=result)

        store = _make_rollout_store(plan)
        app = _make_app(rollout_gate_automation_service=mock_gate_service, rollout_store=store)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_test001",
            step_id="prod_canary",
            json=False,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_gate_status(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "ro_test001" in captured.out
        assert "prod_canary" in captured.out
        assert "satisfied" in captured.out.lower()
        assert "gr_test001" in captured.out

    def test_status_json_output(self, capsys):
        """policy rollout gate status --json outputs JSON."""
        from agent_app.cli import _cmd_policy_rollout_gate_status

        plan = _make_plan()
        result = _make_execution_result(
            status=RolloutGateExecutionStatus.SATISFIED,
            requirement_id="rgr_json001",
            gate_result_id="gr_json001",
            simulation_id="psim_json001",
        )
        mock_gate_service = MagicMock()
        mock_gate_service.check_step_gate = AsyncMock(return_value=result)

        store = _make_rollout_store(plan)
        app = _make_app(rollout_gate_automation_service=mock_gate_service, rollout_store=store)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_test001",
            step_id="prod_canary",
            json=True,
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_gate_status(args))

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["rollout_id"] == "ro_test001"
        assert data["step_id"] == "prod_canary"
        assert data["execution_status"] == "satisfied"
        assert data["requirement_id"] == "rgr_json001"
        assert data["gate_result_id"] == "gr_json001"
        assert data["simulation_id"] == "psim_json001"


class TestRolloutGateAttach:
    def test_attach_basic_exit_0(self, capsys):
        """policy rollout gate attach with satisfied gate exits 0."""
        from agent_app.cli import _cmd_policy_rollout_gate_attach

        plan = _make_plan()
        req = _make_requirement(status=ReleaseGateRequirementStatus.SATISFIED)
        mock_gate_service = MagicMock()
        mock_gate_service.require_gate_for_promotion = AsyncMock()
        mock_gate_service.attach_gate_result = AsyncMock(return_value=req)

        store = _make_rollout_store(plan)
        app = _make_app(
            gate_automation_service=mock_gate_service,
            rollout_gate_automation_service=MagicMock(),
            rollout_store=store,
        )

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_test001",
            step_id="prod_canary",
            gate_result_id="gr_test001",
            simulation_id="psim_test001",
            actor_id="release_manager",
            permissions=["policy.rollout.gate.attach"],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_gate_attach(args))

        assert rc == 0
        captured = capsys.readouterr()
        assert "ro_test001" in captured.out
        assert "prod_canary" in captured.out
        assert "satisfied" in captured.out.lower()
        assert "gr_test001" in captured.out

    def test_attach_missing_service_exits_nonzero(self, capsys):
        """policy rollout gate attach with no release gate service exits non-zero."""
        from agent_app.cli import _cmd_policy_rollout_gate_attach

        plan = _make_plan()
        store = _make_rollout_store(plan)

        # rollout_gate_automation_service present but release_gate_automation_service None
        app = _make_app(
            gate_automation_service=None,
            rollout_gate_automation_service=MagicMock(),
            rollout_store=store,
        )

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_test001",
            step_id="prod_canary",
            gate_result_id="gr_test001",
            simulation_id=None,
            actor_id="release_manager",
            permissions=[],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_gate_attach(args))

        assert rc != 0


class TestRolloutGateMissingRollout:
    def test_missing_rollout_exits_nonzero(self, capsys):
        """policy rollout gate commands with missing rollout exits non-zero."""
        from agent_app.cli import _cmd_policy_rollout_gate_run

        store = _make_rollout_store(plan=None)
        mock_gate_service = MagicMock()
        app = _make_app(rollout_gate_automation_service=mock_gate_service, rollout_store=store)

        args = argparse.Namespace(
            config="agentapp.yaml",
            rollout_id="ro_nonexistent",
            step_id="prod_canary",
            actor_id="release_manager",
            permissions=[],
        )

        with patch("agent_app.config.loader.build_app", return_value=app):
            rc = _run_async(_cmd_policy_rollout_gate_run(args))

        assert rc != 0
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower() or "not found" in captured.out.lower()
