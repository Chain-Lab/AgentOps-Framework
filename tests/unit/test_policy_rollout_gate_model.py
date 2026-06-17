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
