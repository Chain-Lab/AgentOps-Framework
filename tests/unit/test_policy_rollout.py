"""Tests for RolloutPlan and RolloutStep models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)


def _make_step(
    step_id: str = "step_1",
    step_type: RolloutStepType = RolloutStepType.ACTIVATE,
    environment: str = "production",
    **overrides,
) -> RolloutStep:
    """Helper to build a RolloutStep with sensible defaults."""
    base = dict(
        step_id=step_id,
        step_type=step_type,
        environment=environment,
    )
    base.update(overrides)
    return RolloutStep(**base)


def _make_plan(**overrides) -> RolloutPlan:
    """Helper to build a RolloutPlan with sensible defaults."""
    now = datetime.now(timezone.utc)
    base = dict(
        rollout_id="ro_abc123",
        name="Test rollout",
        bundle_id="pb_xyz789",
        steps=[
            _make_step(step_id="step_1", step_type=RolloutStepType.ACTIVATE),
            _make_step(
                step_id="step_2",
                step_type=RolloutStepType.ASSIGN_RING,
                ring_name="canary",
            ),
        ],
        created_by="user_1",
        created_at=now,
        updated_at=now,
    )
    base.update(overrides)
    return RolloutPlan(**base)


# --- Tests ---


def test_valid_plan_creation():
    """Create a valid RolloutPlan with 2+ steps and verify all fields."""
    now = datetime.now(timezone.utc)
    plan = _make_plan(
        rollout_id="ro_abc123",
        name="My rollout",
        bundle_id="pb_xyz789",
        steps=[
            _make_step(
                step_id="step_activate",
                step_type=RolloutStepType.ACTIVATE,
                environment="staging",
            ),
            _make_step(
                step_id="step_ring",
                step_type=RolloutStepType.ASSIGN_RING,
                environment="staging",
                ring_name="canary",
                from_ring=None,
                to_ring="canary",
            ),
            _make_step(
                step_id="step_eval",
                step_type=RolloutStepType.CANARY_EVAL,
                environment="staging",
                eval_suite="smoke",
            ),
        ],
        created_by="admin",
        reason="Release v2",
        created_at=now,
        updated_at=now,
    )

    assert plan.rollout_id == "ro_abc123"
    assert plan.name == "My rollout"
    assert plan.bundle_id == "pb_xyz789"
    assert plan.created_by == "admin"
    assert plan.reason == "Release v2"
    assert plan.created_at == now
    assert plan.updated_at == now
    assert len(plan.steps) == 3

    s1 = plan.steps[0]
    assert s1.step_id == "step_activate"
    assert s1.step_type == RolloutStepType.ACTIVATE
    assert s1.environment == "staging"
    assert s1.status == RolloutStepStatus.PENDING

    s2 = plan.steps[1]
    assert s2.step_type == RolloutStepType.ASSIGN_RING
    assert s2.ring_name == "canary"
    assert s2.to_ring == "canary"

    s3 = plan.steps[2]
    assert s3.step_type == RolloutStepType.CANARY_EVAL
    assert s3.eval_suite == "smoke"


def test_rollout_id_prefix():
    """rollout_id should conventionally use the ro_ prefix."""
    plan = _make_plan(rollout_id="ro_abc123")
    assert plan.rollout_id.startswith("ro_")

    # Verify the enum values are string-based
    assert RolloutPlanStatus.DRAFT == "draft"
    assert RolloutStepType.ACTIVATE == "activate"


def test_default_status_draft():
    """New plan defaults to DRAFT status, steps default to PENDING."""
    plan = _make_plan()
    assert plan.status == RolloutPlanStatus.DRAFT
    for step in plan.steps:
        assert step.status == RolloutStepStatus.PENDING


def test_empty_steps_raises():
    """ValueError when steps list is empty."""
    with pytest.raises(ValueError, match="at least one step"):
        _make_plan(steps=[])


def test_duplicate_step_id_raises():
    """ValueError when two steps have the same step_id."""
    with pytest.raises(ValueError, match="Duplicate step_id"):
        _make_plan(
            steps=[
                _make_step(step_id="step_a"),
                _make_step(step_id="step_a"),
            ]
        )


def test_invalid_require_previous_step_raises():
    """ValueError when require_previous_step references a non-existent step."""
    with pytest.raises(ValueError, match="requires previous step.*does not exist"):
        _make_plan(
            steps=[
                _make_step(
                    step_id="step_1",
                    step_type=RolloutStepType.ACTIVATE,
                ),
                _make_step(
                    step_id="step_2",
                    step_type=RolloutStepType.CANARY_EVAL,
                    require_previous_step="step_nonexistent",
                ),
            ]
        )


def test_timezone_aware_datetimes():
    """created_at and updated_at are timezone-aware."""
    now = datetime.now(timezone.utc)
    plan = _make_plan(created_at=now, updated_at=now)

    assert plan.created_at.tzinfo is not None
    assert plan.updated_at.tzinfo is not None

    # Step timestamps should also be timezone-aware when set
    step = _make_step(started_at=now, completed_at=now)
    assert step.started_at is not None
    assert step.started_at.tzinfo is not None
    assert step.completed_at is not None
    assert step.completed_at.tzinfo is not None
