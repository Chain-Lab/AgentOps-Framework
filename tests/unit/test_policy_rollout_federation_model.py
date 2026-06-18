from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedRolloutTargetExecution,
    FederatedRolloutTargetExecutionStatus,
    FederatedRolloutWave,
    FederatedTargetStatus,
    FederationExecutionStrategy,
    RolloutConflict,
    RolloutConflictSeverity,
    RolloutConflictType,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _step(environment: str = "prod", ring_name: str | None = "canary") -> RolloutStep:
    return RolloutStep(
        step_id="step_activate",
        step_type=RolloutStepType.ACTIVATE,
        environment=environment,
        ring_name=ring_name,
    )


class TestFederatedRolloutTarget:
    def test_valid_target_preserves_optional_fields(self) -> None:
        target = FederatedRolloutTarget(
            target_id="frt_prod_us_canary",
            name="prod-us-canary",
            tenant_id="tenant_a",
            environment="prod",
            ring_name="canary",
            region="us-east",
            labels={"tier": "gold"},
            metadata={"owner": "release"},
            created_at=_now(),
        )

        assert target.target_id == "frt_prod_us_canary"
        assert target.tenant_id == "tenant_a"
        assert target.environment == "prod"
        assert target.ring_name == "canary"
        assert target.region == "us-east"
        assert target.labels == {"tier": "gold"}
        assert target.status == FederatedTargetStatus.ENABLED

    def test_target_id_requires_prefix(self) -> None:
        with pytest.raises(ValidationError, match="frt_"):
            FederatedRolloutTarget(
                target_id="bad_target",
                name="bad",
                environment="prod",
                created_at=_now(),
            )

    def test_target_created_at_requires_timezone(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederatedRolloutTarget(
                target_id="frt_no_tz",
                name="bad",
                environment="prod",
                created_at=datetime(2026, 6, 18, 12, 0, 0),
            )


class TestFederatedRolloutPlan:
    def test_valid_sequential_plan(self) -> None:
        plan = FederatedRolloutPlan(
            federation_id="frp_global_rollout",
            name="global rollout",
            bundle_id="pb_123",
            target_ids=["frt_a", "frt_b"],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            created_at=_now(),
            updated_at=_now(),
        )

        assert plan.federation_id == "frp_global_rollout"
        assert plan.strategy == FederationExecutionStrategy.SEQUENTIAL
        assert plan.status == FederatedRolloutPlanStatus.DRAFT
        assert plan.target_ids == ["frt_a", "frt_b"]
        assert len(plan.rollout_template_steps) == 1

    def test_federation_id_requires_prefix(self) -> None:
        with pytest.raises(ValidationError, match="frp_"):
            FederatedRolloutPlan(
                federation_id="bad",
                name="bad",
                bundle_id="pb_123",
                target_ids=["frt_a"],
                rollout_template_steps=[_step()],
                created_by="user",
                created_at=_now(),
                updated_at=_now(),
            )

    def test_execution_id_requires_prefix(self) -> None:
        with pytest.raises(ValidationError, match="fre_"):
            FederatedRolloutTargetExecution(
                execution_id="bad",
                target_id="frt_a",
            )

    def test_wave_id_requires_prefix(self) -> None:
        with pytest.raises(ValidationError, match="frw_"):
            FederatedRolloutWave(wave_id="bad", target_ids=["frt_a"])

    def test_wave_strategy_requires_waves(self) -> None:
        with pytest.raises(ValidationError, match="WAVE strategy requires at least one wave"):
            FederatedRolloutPlan(
                federation_id="frp_wave_missing",
                name="wave missing",
                bundle_id="pb_123",
                strategy=FederationExecutionStrategy.WAVE,
                target_ids=["frt_a"],
                rollout_template_steps=[_step()],
                created_by="user",
                created_at=_now(),
                updated_at=_now(),
            )

    def test_wave_targets_must_exist_when_target_ids_present(self) -> None:
        with pytest.raises(ValidationError, match="unknown target_id"):
            FederatedRolloutPlan(
                federation_id="frp_bad_wave",
                name="bad wave",
                bundle_id="pb_123",
                strategy=FederationExecutionStrategy.WAVE,
                target_ids=["frt_a"],
                waves=[FederatedRolloutWave(wave_id="frw_1", target_ids=["frt_missing"])],
                rollout_template_steps=[_step()],
                created_by="user",
                created_at=_now(),
                updated_at=_now(),
            )

    def test_duplicate_target_validation(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate target_id"):
            FederatedRolloutPlan(
                federation_id="frp_dup",
                name="dup",
                bundle_id="pb_123",
                target_ids=["frt_a", "frt_a"],
                rollout_template_steps=[_step()],
                created_by="user",
                created_at=_now(),
                updated_at=_now(),
            )

    def test_target_ids_can_be_empty_when_waves_provided(self) -> None:
        plan = FederatedRolloutPlan(
            federation_id="frp_wave_only",
            name="wave only",
            bundle_id="pb_123",
            strategy=FederationExecutionStrategy.WAVE,
            target_ids=[],
            waves=[FederatedRolloutWave(wave_id="frw_1", target_ids=["frt_a"])],
            rollout_template_steps=[_step()],
            created_by="user",
            created_at=_now(),
            updated_at=_now(),
        )

        assert plan.waves[0].target_ids == ["frt_a"]

    def test_plan_datetimes_require_timezone(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederatedRolloutPlan(
                federation_id="frp_no_tz",
                name="bad",
                bundle_id="pb_123",
                target_ids=["frt_a"],
                rollout_template_steps=[_step()],
                created_by="user",
                created_at=datetime(2026, 6, 18, 12, 0, 0),
                updated_at=_now(),
            )


class TestRolloutConflict:
    def test_valid_conflict(self) -> None:
        conflict = RolloutConflict(
            conflict_id="frc_001",
            conflict_type=RolloutConflictType.TARGET_ALREADY_ACTIVE,
            severity=RolloutConflictSeverity.ERROR,
            target_id="frt_a",
            environment="prod",
            ring_name="canary",
            existing_federation_id="frp_existing",
            message="Target is already active in another federation.",
        )

        assert conflict.conflict_id == "frc_001"
        assert conflict.severity == RolloutConflictSeverity.ERROR
        assert conflict.target_id == "frt_a"

    def test_conflict_id_requires_prefix(self) -> None:
        with pytest.raises(ValidationError, match="frc_"):
            RolloutConflict(
                conflict_id="bad",
                conflict_type=RolloutConflictType.DUPLICATE_TARGET,
                severity=RolloutConflictSeverity.ERROR,
                message="Duplicate target.",
            )

    def test_missing_target_conflict_type_exists(self) -> None:
        assert RolloutConflictType.MISSING_TARGET.value == "missing_target"
