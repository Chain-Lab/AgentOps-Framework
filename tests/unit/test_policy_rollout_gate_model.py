"""Tests for Phase 43 rollout gate enums and RolloutStep extension."""
import pytest
from datetime import datetime, timezone


def test_rollout_gate_mode_values():
    from agent_app.governance.policy_rollout import RolloutGateMode
    assert RolloutGateMode.DISABLED == "disabled"
    assert RolloutGateMode.MANUAL == "manual"
    assert RolloutGateMode.AUTO == "auto"
    assert len(RolloutGateMode) == 3


def test_rollout_gate_failure_action_values():
    from agent_app.governance.policy_rollout import RolloutGateFailureAction
    assert RolloutGateFailureAction.BLOCK == "block"
    assert RolloutGateFailureAction.FAIL == "fail"
    assert RolloutGateFailureAction.SKIP == "skip"
    assert len(RolloutGateFailureAction) == 3


def test_rollout_step_default_gate_mode_disabled():
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType, RolloutGateMode
    step = RolloutStep(
        step_id="s1",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
    )
    assert step.simulation_gate_mode == RolloutGateMode.DISABLED


def test_rollout_step_default_failure_action_block():
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType, RolloutGateFailureAction
    step = RolloutStep(
        step_id="s1",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
    )
    assert step.simulation_gate_failure_action == RolloutGateFailureAction.BLOCK


def test_rollout_step_new_fields_default():
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
    step = RolloutStep(
        step_id="s1",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
    )
    assert step.simulation_candidate_rules == []
    assert step.simulation_gate_rules == []
    assert step.simulation_window_start is None
    assert step.simulation_window_end is None
    assert step.simulation_limit is None
    assert step.simulation_include_base is True
    assert step.simulation_gate_max_age_seconds is None


def test_rollout_step_with_auto_gate():
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType, RolloutGateMode, RolloutGateFailureAction
    step = RolloutStep(
        step_id="s1",
        step_type=RolloutStepType.ASSIGN_RING,
        environment="prod",
        ring_name="canary",
        requires_simulation_gate=True,
        simulation_gate_mode=RolloutGateMode.AUTO,
        simulation_gate_failure_action=RolloutGateFailureAction.FAIL,
        simulation_limit=1000,
    )
    assert step.simulation_gate_mode == RolloutGateMode.AUTO
    assert step.simulation_gate_failure_action == RolloutGateFailureAction.FAIL
    assert step.simulation_limit == 1000


def test_rollout_step_backward_compat_phase42_fields():
    """Phase 42 fields must still exist and work."""
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
    step = RolloutStep(
        step_id="s1",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
        requires_simulation_gate=True,
        simulation_gate_requirement_id="rgr_abc",
        simulation_gate_result_id="gr_def",
    )
    assert step.requires_simulation_gate is True
    assert step.simulation_gate_requirement_id == "rgr_abc"
    assert step.simulation_gate_result_id == "gr_def"


def test_rollout_gate_execution_status_values():
    from agent_app.governance.policy_rollout_gate import RolloutGateExecutionStatus
    assert RolloutGateExecutionStatus.NOT_REQUIRED == "not_required"
    assert RolloutGateExecutionStatus.SATISFIED == "satisfied"
    assert RolloutGateExecutionStatus.BLOCKED == "blocked"
    assert RolloutGateExecutionStatus.FAILED == "failed"
    assert RolloutGateExecutionStatus.SKIPPED == "skipped"
    assert RolloutGateExecutionStatus.ERROR == "error"
    assert len(RolloutGateExecutionStatus) == 6


def test_rollout_gate_execution_result_valid():
    from agent_app.governance.policy_rollout_gate import RolloutGateExecutionResult, RolloutGateExecutionStatus
    result = RolloutGateExecutionResult(
        execution_id="rge_abc123",
        rollout_id="ro_xyz",
        step_id="s1",
        status=RolloutGateExecutionStatus.SATISFIED,
        created_at=datetime.now(timezone.utc),
    )
    assert result.execution_id == "rge_abc123"
    assert result.status == RolloutGateExecutionStatus.SATISFIED
    assert result.requirement_id is None
    assert result.gate_result_id is None
    assert result.simulation_id is None


def test_rollout_gate_execution_result_id_prefix():
    from agent_app.governance.policy_rollout_gate import RolloutGateExecutionResult, RolloutGateExecutionStatus
    with pytest.raises(ValueError):
        RolloutGateExecutionResult(
            execution_id="bad_prefix",
            rollout_id="ro_xyz",
            step_id="s1",
            status=RolloutGateExecutionStatus.SATISFIED,
            created_at=datetime.now(timezone.utc),
        )


def test_rollout_gate_execution_result_tz_aware():
    from agent_app.governance.policy_rollout_gate import RolloutGateExecutionResult, RolloutGateExecutionStatus
    with pytest.raises(ValueError):
        RolloutGateExecutionResult(
            execution_id="rge_abc",
            rollout_id="ro_xyz",
            step_id="s1",
            status=RolloutGateExecutionStatus.SATISFIED,
            created_at=datetime(2026, 1, 1),  # naive datetime
        )


def test_rollout_gate_execution_result_with_all_fields():
    from agent_app.governance.policy_rollout_gate import RolloutGateExecutionResult, RolloutGateExecutionStatus
    result = RolloutGateExecutionResult(
        execution_id="rge_abc",
        rollout_id="ro_xyz",
        step_id="s1",
        status=RolloutGateExecutionStatus.BLOCKED,
        requirement_id="rgr_def",
        gate_result_id="gr_ghi",
        simulation_id="psim_jkl",
        action_taken="gate_blocked",
        reason="Gate result expired",
        error={"type": "gate_expired"},
        created_at=datetime.now(timezone.utc),
        metadata={"max_age_seconds": 86400},
    )
    assert result.requirement_id == "rgr_def"
    assert result.action_taken == "gate_blocked"
    assert result.reason == "Gate result expired"
    assert result.error == {"type": "gate_expired"}
    assert result.metadata == {"max_age_seconds": 86400}
