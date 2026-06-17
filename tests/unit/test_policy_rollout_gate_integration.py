"""Tests for RolloutService simulation gate automation integration.

Phase 43 Task 4: Integration of RolloutGateAutomationService into RolloutService.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from agent_app.core.context import RunContext
from agent_app.governance.policy_rollout import (
    RolloutGateFailureAction,
    RolloutGateMode,
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
from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)
from agent_app.governance.policy_promotion import PromotionRequest, PromotionRequestStatus
from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus
from agent_app.governance.policy_ring_assignment import (
    RingActivationAssignment,
    RingActivationAssignmentStatus,
)
from agent_app.runtime.policy_rollout_service import RolloutService


# -- Stubs --


class _StubRolloutStore:
    """In-memory rollout plan store for testing."""

    def __init__(self):
        self._plans: dict[str, RolloutPlan] = {}

    async def create(self, plan: RolloutPlan) -> RolloutPlan:
        self._plans[plan.rollout_id] = plan
        return plan

    async def get(self, rollout_id: str) -> RolloutPlan | None:
        return self._plans.get(rollout_id)

    async def update(self, plan: RolloutPlan) -> RolloutPlan:
        self._plans[plan.rollout_id] = plan
        return plan

    async def list(self, status=None, bundle_id=None):
        return list(self._plans.values())


class _StubReleaseService:
    """Minimal release service stub for rollout tests."""

    def __init__(self):
        self.promotions_requested: list = []
        self.promotions_approved: list = []
        self.promotions_executed: list = []

    async def request_promotion(self, bundle_id, requested_by, context, reason=None, gate_result_id=None):
        pr = PromotionRequest(
            promotion_id=f"pr_{uuid.uuid4().hex[:8]}",
            bundle_id=bundle_id,
            requested_by=requested_by,
            status=PromotionRequestStatus.PENDING,
            reason=reason,
        )
        self.promotions_requested.append(pr)
        return pr

    async def approve_promotion(self, promotion_id, approved_by, context, reason=None):
        for pr in self.promotions_requested:
            if pr.promotion_id == promotion_id:
                approved = pr.model_copy(update={
                    "status": PromotionRequestStatus.APPROVED,
                    "resolved_by": approved_by,
                })
                self.promotions_approved.append(approved)
                return approved
        raise KeyError(promotion_id)

    async def execute_promotion(self, promotion_id, executed_by, context, **kwargs):
        activation = PolicyActivation(
            activation_id=f"pa_{uuid.uuid4().hex[:8]}",
            environment=kwargs.get("environment", "prod"),
            bundle_id="pb_test",
            config_hash="abc123",
            promotion_id=promotion_id,
            activated_by=executed_by,
            status=PolicyActivationStatus.ACTIVE,
        )
        self.promotions_executed.append(activation)
        return activation

    async def assign_activation_to_ring(self, **kwargs):
        return RingActivationAssignment(
            assignment_id=f"ra_{uuid.uuid4().hex[:8]}",
            environment=kwargs["environment"],
            ring_name=kwargs["ring_name"],
            activation_id=kwargs["activation_id"],
            bundle_id="pb_test",
            config_hash="abc123",
            status=RingActivationAssignmentStatus.ACTIVE,
            assigned_by=kwargs["assigned_by"],
        )

    async def promote_canary_to_stable(self, **kwargs):
        return RingActivationAssignment(
            assignment_id=f"ra_{uuid.uuid4().hex[:8]}",
            environment=kwargs["environment"],
            ring_name=kwargs["stable_ring"],
            activation_id=f"pa_auto_{uuid.uuid4().hex[:6]}",
            bundle_id="pb_test",
            config_hash="abc123",
            status=RingActivationAssignmentStatus.ACTIVE,
            assigned_by=kwargs["promoted_by"],
        )

    @property
    def activation_store(self):
        class _S:
            async def list(self, environment=None):
                return []
        return _S()


class _StubPermissionChecker:
    async def check(self, permission, context):
        return True


class _StubAuditLogger:
    def __init__(self):
        self.events: list = []

    async def log(self, event):
        self.events.append(event)


# -- Helpers --


def _make_step(
    step_id: str = "dev_activate",
    step_type: RolloutStepType = RolloutStepType.ACTIVATE,
    environment: str = "dev",
    ring_name: str = "stable",
    requires_simulation_gate: bool = False,
    simulation_gate_mode: RolloutGateMode = RolloutGateMode.DISABLED,
    simulation_gate_failure_action: RolloutGateFailureAction = RolloutGateFailureAction.BLOCK,
    **kwargs,
) -> RolloutStep:
    return RolloutStep(
        step_id=step_id,
        step_type=step_type,
        environment=environment,
        ring_name=ring_name,
        requires_simulation_gate=requires_simulation_gate,
        simulation_gate_mode=simulation_gate_mode,
        simulation_gate_failure_action=simulation_gate_failure_action,
        **kwargs,
    )


def _make_plan(
    steps: list[RolloutStep] | None = None,
    status: RolloutPlanStatus = RolloutPlanStatus.DRAFT,
    rollout_id: str | None = None,
) -> RolloutPlan:
    if steps is None:
        steps = [_make_step()]
    now = datetime.now(timezone.utc)
    return RolloutPlan(
        rollout_id=rollout_id or f"ro_{uuid.uuid4().hex[:8]}",
        name="test-plan",
        bundle_id="pb_test",
        status=status,
        steps=steps,
        created_by="user1",
        created_at=now,
        updated_at=now,
    )


def _make_context(
    user_id: str = "test_user",
    tenant_id: str = "test_tenant",
) -> RunContext:
    return RunContext(
        run_id=f"run_{uuid.uuid4().hex[:8]}",
        user_id=user_id,
        tenant_id=tenant_id,
        permissions=[
            "policy.rollout.create",
            "policy.rollout.start",
            "policy.rollout.execute",
            "policy.rollout.cancel",
        ],
    )


def _gate_result(
    status: RolloutGateExecutionStatus = RolloutGateExecutionStatus.SATISFIED,
    requirement_id: str | None = None,
    gate_result_id: str | None = None,
    reason: str | None = None,
    action_taken: str | None = None,
    error: dict | None = None,
) -> RolloutGateExecutionResult:
    return RolloutGateExecutionResult(
        execution_id=f"rge_{uuid.uuid4().hex[:12]}",
        rollout_id="ro_test",
        step_id="dev_activate",
        status=status,
        requirement_id=requirement_id,
        gate_result_id=gate_result_id,
        action_taken=action_taken,
        reason=reason,
        error=error,
        created_at=datetime.now(timezone.utc),
    )


def _make_service(
    release_gate_automation_service=None,
    rollout_gate_automation_service=None,
    audit_logger=None,
) -> RolloutService:
    store = _StubRolloutStore()
    release_svc = _StubReleaseService()
    checker = _StubPermissionChecker()
    logger = audit_logger or _StubAuditLogger()
    return RolloutService(
        rollout_store=store,
        release_service=release_svc,
        audit_logger=logger,
        permission_checker=checker,
        release_gate_automation_service=release_gate_automation_service,
        rollout_gate_automation_service=rollout_gate_automation_service,
    )


async def _create_and_start_plan(svc, ctx, steps):
    """Create and start a rollout plan, returning the ACTIVE plan."""
    plan = await svc.create_plan(
        name="test",
        bundle_id="pb_test",
        steps=steps,
        created_by="user1",
        context=ctx,
    )
    plan = await svc.start_plan(
        rollout_id=plan.rollout_id,
        started_by="user1",
        context=ctx,
    )
    return plan


# -- Tests --


class TestNoGateConfigPreservesBehavior:
    """1. No gate config preserves existing behavior."""

    @pytest.mark.asyncio
    async def test_step_without_gate_config_executes_normally(self):
        """Step with no gate config and no gate services executes normally."""
        svc = _make_service()
        ctx = _make_context()
        steps = [_make_step(requires_simulation_gate=False, simulation_gate_mode=RolloutGateMode.DISABLED)]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.SUCCEEDED
        assert step.activation_id is not None

    @pytest.mark.asyncio
    async def test_step_with_gate_service_but_disabled_mode_executes(self):
        """Step with gate service available but DISABLED mode skips gate check."""
        gate_svc = AsyncMock()
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(requires_simulation_gate=False, simulation_gate_mode=RolloutGateMode.DISABLED)]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.SUCCEEDED
        # Gate service should NOT have been called (DISABLED mode + no requires_simulation_gate)
        gate_svc.ensure_step_gate.assert_not_called()


class TestManualModeBlocksStep:
    """2. MANUAL missing gate blocks step."""

    @pytest.mark.asyncio
    async def test_manual_mode_blocks_when_gate_missing(self):
        """Step with MANUAL gate mode becomes BLOCKED when gate is not satisfied."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.BLOCKED,
            reason="Gate is required, manual mode requires explicit gate result",
            action_taken="manual_blocked",
            requirement_id="rgr_test123",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.MANUAL,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.BLOCKED
        assert step.error is not None
        assert step.error["type"] == "simulation_gate_required"
        assert "manual" in step.error["message"].lower() or "blocked" in step.error["message"].lower()
        assert step.simulation_gate_requirement_id == "rgr_test123"
        # Plan should still be ACTIVE
        assert plan.status == RolloutPlanStatus.ACTIVE


class TestAutoPassingGateExecutesStep:
    """3. AUTO passing gate executes step normally."""

    @pytest.mark.asyncio
    async def test_auto_mode_passing_gate_executes_step(self):
        """Step with AUTO gate mode and SATISFIED gate result executes normally."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.SATISFIED,
            requirement_id="rgr_satisfied",
            gate_result_id="gr_pass",
            action_taken="auto_passed",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.SUCCEEDED
        assert step.activation_id is not None
        # Gate IDs should be updated on the step
        assert step.simulation_gate_requirement_id == "rgr_satisfied"
        assert step.simulation_gate_result_id == "gr_pass"


class TestAutoFailingGateBlockAction:
    """4. AUTO failing gate with BLOCK action blocks step."""

    @pytest.mark.asyncio
    async def test_auto_block_action_blocks_step(self):
        """Step with AUTO gate mode and BLOCK failure action becomes BLOCKED."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.BLOCKED,
            reason="Gate failed with status required",
            action_taken="auto_blocked",
            requirement_id="rgr_blocked",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
            simulation_gate_failure_action=RolloutGateFailureAction.BLOCK,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.BLOCKED
        assert step.error is not None
        assert step.error["type"] == "simulation_gate_required"
        assert step.error["action_taken"] == "auto_blocked"
        # Plan should still be ACTIVE
        assert plan.status == RolloutPlanStatus.ACTIVE


class TestAutoFailingGateFailAction:
    """5. AUTO failing gate with FAIL action fails step."""

    @pytest.mark.asyncio
    async def test_auto_fail_action_fails_step(self):
        """Step with AUTO gate mode and FAIL failure action becomes FAILED."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.FAILED,
            reason="Gate failed with status required",
            action_taken="auto_failed",
            requirement_id="rgr_failed",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
            simulation_gate_failure_action=RolloutGateFailureAction.FAIL,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.FAILED
        assert step.error is not None
        assert step.error["type"] == "simulation_gate_failed"
        assert step.error["action_taken"] == "auto_failed"
        # Plan should be FAILED
        assert plan.status == RolloutPlanStatus.FAILED


class TestAutoFailingGateSkipAction:
    """6. AUTO failing gate with SKIP action skips step."""

    @pytest.mark.asyncio
    async def test_auto_skip_action_skips_step(self):
        """Step with AUTO gate mode and SKIP failure action becomes SKIPPED."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.SKIPPED,
            reason="Gate failed with status required",
            action_taken="auto_skipped",
            requirement_id="rgr_skipped",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
            simulation_gate_failure_action=RolloutGateFailureAction.SKIP,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.SKIPPED
        assert step.simulation_gate_requirement_id == "rgr_skipped"
        # Plan should be COMPLETED (only step, and it's skipped)
        assert plan.status == RolloutPlanStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_skip_does_not_complete_plan_if_other_steps_pending(self):
        """SKIPPED step does not complete the plan if other steps are still PENDING."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.SKIPPED,
            reason="Gate failed",
            action_taken="auto_skipped",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [
            _make_step(
                step_id="step1",
                requires_simulation_gate=True,
                simulation_gate_mode=RolloutGateMode.AUTO,
                simulation_gate_failure_action=RolloutGateFailureAction.SKIP,
            ),
            _make_step(
                step_id="step2",
                environment="staging",
                require_previous_step="step1",
            ),
        ]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step1 = next(s for s in plan.steps if s.step_id == "step1")
        assert step1.status == RolloutStepStatus.SKIPPED
        # Plan should still be ACTIVE (step2 is PENDING)
        assert plan.status == RolloutPlanStatus.ACTIVE


class TestRunAllAvailableStopsOnBlocked:
    """7. run_all_available stops on BLOCKED."""

    @pytest.mark.asyncio
    async def test_run_all_stops_on_blocked_step(self):
        """run_all_available stops iterating when a step is BLOCKED by gate."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.BLOCKED,
            reason="Gate blocked",
            action_taken="auto_blocked",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
            simulation_gate_failure_action=RolloutGateFailureAction.BLOCK,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_all_available(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.BLOCKED


class TestRunAllAvailableStopsOnFailed:
    """8. run_all_available stops on FAILED."""

    @pytest.mark.asyncio
    async def test_run_all_stops_on_failed_step(self):
        """run_all_available stops iterating when a step is FAILED by gate."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.FAILED,
            reason="Gate failed",
            action_taken="auto_failed",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
            simulation_gate_failure_action=RolloutGateFailureAction.FAIL,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_all_available(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.FAILED
        assert plan.status == RolloutPlanStatus.FAILED


class TestStepFieldsUpdatedWithGateIds:
    """9. Step fields updated with gate IDs on pass."""

    @pytest.mark.asyncio
    async def test_gate_ids_updated_on_satisfied(self):
        """When gate returns SATISFIED, step fields are updated with gate IDs."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.SATISFIED,
            requirement_id="rgr_abc123",
            gate_result_id="gr_xyz789",
            action_taken="auto_passed",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.SUCCEEDED
        assert step.simulation_gate_requirement_id == "rgr_abc123"
        assert step.simulation_gate_result_id == "gr_xyz789"

    @pytest.mark.asyncio
    async def test_gate_ids_preserve_existing_if_result_is_none(self):
        """When gate result IDs are None, existing step IDs are preserved."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.SATISFIED,
            requirement_id=None,
            gate_result_id=None,
            action_taken="existing_satisfied",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
            simulation_gate_requirement_id="rgr_existing",
            simulation_gate_result_id="gr_existing",
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.SUCCEEDED
        # Existing IDs should be preserved since gate result returned None
        assert step.simulation_gate_requirement_id == "rgr_existing"
        assert step.simulation_gate_result_id == "gr_existing"


class TestPhase42BackwardCompat:
    """10. Phase 42 backward compat manual blocking still works."""

    @pytest.mark.asyncio
    async def test_phase42_gate_check_still_blocks(self):
        """Phase 42 release_gate_automation_service still blocks steps when gate is not SATISFIED."""
        # Create a Phase 42-style release gate service
        release_gate_svc = AsyncMock()
        release_gate_svc.check_requirement.return_value = type(
            "GateReq",
            (),
            {
                "status": ReleaseGateRequirementStatus.REQUIRED,
                "requirement_id": "rgr_p42",
            },
        )()
        svc = _make_service(release_gate_automation_service=release_gate_svc)
        ctx = _make_context()
        steps = [_make_step(requires_simulation_gate=True)]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.BLOCKED
        assert step.error is not None
        assert step.error["type"] == "simulation_gate_required"
        assert "required" in step.error["requirement_status"]

    @pytest.mark.asyncio
    async def test_phase42_and_phase43_both_present(self):
        """When both Phase 42 and Phase 43 services are present, Phase 42 runs first."""
        # Phase 42 gate is SATISFIED, so Phase 43 check runs next
        release_gate_svc = AsyncMock()
        release_gate_svc.check_requirement.return_value = type(
            "GateReq",
            (),
            {
                "status": ReleaseGateRequirementStatus.SATISFIED,
                "requirement_id": "rgr_p42_satisfied",
            },
        )()

        rollout_gate_svc = AsyncMock()
        rollout_gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.SATISFIED,
            requirement_id="rgr_p43",
            gate_result_id="gr_p43",
            action_taken="auto_passed",
        )

        svc = _make_service(
            release_gate_automation_service=release_gate_svc,
            rollout_gate_automation_service=rollout_gate_svc,
        )
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.SUCCEEDED
        # Phase 42 check was called
        release_gate_svc.check_requirement.assert_called_once()
        # Phase 43 check was also called
        rollout_gate_svc.ensure_step_gate.assert_called_once()

    @pytest.mark.asyncio
    async def test_phase42_blocks_before_phase43_runs(self):
        """When Phase 42 blocks, Phase 43 service is never called."""
        release_gate_svc = AsyncMock()
        release_gate_svc.check_requirement.return_value = type(
            "GateReq",
            (),
            {
                "status": ReleaseGateRequirementStatus.REQUIRED,
                "requirement_id": "rgr_p42_required",
            },
        )

        rollout_gate_svc = AsyncMock()

        svc = _make_service(
            release_gate_automation_service=release_gate_svc,
            rollout_gate_automation_service=rollout_gate_svc,
        )
        ctx = _make_context()
        steps = [_make_step(requires_simulation_gate=True)]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.BLOCKED
        # Phase 43 service should NOT have been called
        rollout_gate_svc.ensure_step_gate.assert_not_called()


class TestRunAllAvailableSkippedContinuation:
    """SKIPPED steps allow run_all_available to continue to next step."""

    @pytest.mark.asyncio
    async def test_run_all_continues_after_skipped_step(self):
        """run_all_available continues to next step after a SKIPPED step."""
        # First call returns SKIPPED, second call would execute normally
        # But we need to handle the fact that run_all_available calls run_next_step
        # which re-fetches the plan each time.
        gate_svc = AsyncMock()
        # First step gets SKIPPED by gate
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.SKIPPED,
            reason="Gate skipped",
            action_taken="auto_skipped",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [
            _make_step(
                step_id="step1",
                requires_simulation_gate=True,
                simulation_gate_mode=RolloutGateMode.AUTO,
                simulation_gate_failure_action=RolloutGateFailureAction.SKIP,
            ),
            _make_step(
                step_id="step2",
                environment="staging",
                # No gate config on step2, so it should execute normally
            ),
        ]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_all_available(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step1 = next(s for s in plan.steps if s.step_id == "step1")
        assert step1.status == RolloutStepStatus.SKIPPED
        # step2 should have been executed (no gate config)
        step2 = next(s for s in plan.steps if s.step_id == "step2")
        assert step2.status == RolloutStepStatus.SUCCEEDED


class TestGateErrorHandling:
    """ERROR status from gate is treated as BLOCKED (conservative)."""

    @pytest.mark.asyncio
    async def test_error_status_blocks_step(self):
        """When gate returns ERROR, step is treated as BLOCKED."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.ERROR,
            error={"type": "check_error", "message": "Connection refused"},
            action_taken="auto_error",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.BLOCKED
        assert step.error is not None
        assert step.error["type"] == "simulation_gate_error"
        assert "Connection refused" in step.error["message"]
        # Plan should still be ACTIVE (conservative block, not fail)
        assert plan.status == RolloutPlanStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_error_with_no_error_dict_uses_default_message(self):
        """When gate returns ERROR with no error dict, default message is used."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.ERROR,
            error=None,
            action_taken="auto_error",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.BLOCKED
        assert step.error["message"] == "Gate evaluation error"


class TestGateModeNotRequired:
    """NOT_REQUIRED status from gate allows step to proceed."""

    @pytest.mark.asyncio
    async def test_not_required_allows_execution(self):
        """When gate returns NOT_REQUIRED, step proceeds to execution."""
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.NOT_REQUIRED,
            action_taken="gate_disabled",
        )
        svc = _make_service(rollout_gate_automation_service=gate_svc)
        ctx = _make_context()
        # Step has gate mode MANUAL but gate service says NOT_REQUIRED
        # (this shouldn't normally happen, but tests the fallthrough)
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.MANUAL,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step = plan.steps[0]
        # NOT_REQUIRED falls through to execution
        assert step.status == RolloutStepStatus.SUCCEEDED


class TestAuditEventsForGateActions:
    """Verify audit events are written for gate-blocked/failed/skipped steps."""

    @pytest.mark.asyncio
    async def test_blocked_step_writes_audit(self):
        """Blocked step writes step_blocked audit event with gate details."""
        logger = _StubAuditLogger()
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.BLOCKED,
            reason="Gate blocked",
            action_taken="auto_blocked",
        )
        svc = _make_service(
            rollout_gate_automation_service=gate_svc,
            audit_logger=logger,
        )
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
            simulation_gate_failure_action=RolloutGateFailureAction.BLOCK,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        event_types = [e.event_type for e in logger.events]
        assert "policy.rollout.step_blocked" in event_types
        blocked_event = next(e for e in logger.events if e.event_type == "policy.rollout.step_blocked")
        assert blocked_event.data["reason"] == "simulation_gate_blocked"
        assert blocked_event.data["gate_action"] == "auto_blocked"

    @pytest.mark.asyncio
    async def test_failed_step_writes_audit(self):
        """Failed step writes step_failed audit event with gate details."""
        logger = _StubAuditLogger()
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.FAILED,
            reason="Gate failed",
            action_taken="auto_failed",
        )
        svc = _make_service(
            rollout_gate_automation_service=gate_svc,
            audit_logger=logger,
        )
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
            simulation_gate_failure_action=RolloutGateFailureAction.FAIL,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        event_types = [e.event_type for e in logger.events]
        assert "policy.rollout.step_failed" in event_types
        failed_event = next(e for e in logger.events if e.event_type == "policy.rollout.step_failed")
        assert failed_event.data["reason"] == "simulation_gate_failed"
        assert failed_event.data["gate_action"] == "auto_failed"

    @pytest.mark.asyncio
    async def test_skipped_step_writes_audit(self):
        """Skipped step writes step_skipped audit event."""
        logger = _StubAuditLogger()
        gate_svc = AsyncMock()
        gate_svc.ensure_step_gate.return_value = _gate_result(
            status=RolloutGateExecutionStatus.SKIPPED,
            reason="Gate skipped",
            action_taken="auto_skipped",
        )
        svc = _make_service(
            rollout_gate_automation_service=gate_svc,
            audit_logger=logger,
        )
        ctx = _make_context()
        steps = [_make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
            simulation_gate_failure_action=RolloutGateFailureAction.SKIP,
        )]
        plan = await _create_and_start_plan(svc, ctx, steps)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        event_types = [e.event_type for e in logger.events]
        assert "policy.rollout.step_skipped" in event_types
        skipped_event = next(e for e in logger.events if e.event_type == "policy.rollout.step_skipped")
        assert skipped_event.data["reason"] == "simulation_gate_skipped"
