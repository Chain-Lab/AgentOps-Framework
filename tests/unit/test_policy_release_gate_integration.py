"""Tests for simulation gate enforcement in PolicyReleaseService and RolloutService.

Phase 42 Task 5: Integration of simulation gate enforcement into promotion and rollout flows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from agent_app.core.context import RunContext
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.governance.policy_bundle import InMemoryPolicyBundleStore, PolicyBundle, compute_config_hash
from agent_app.governance.policy_gate import PolicyGateEvaluator, PolicyGateRule
from agent_app.governance.policy_promotion import PromotionRequest, PromotionRequestStatus
from agent_app.governance.policy_release_gate import ReleaseGateRequirement, ReleaseGateRequirementStatus
from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)
from agent_app.governance.policy_rbac import PolicyReleasePermissionChecker
from agent_app.runtime.policy_release import PolicyReleaseService
from agent_app.runtime.policy_rollout_service import RolloutService
from agent_app.runtime.promotion_store import InMemoryPromotionRequestStore
from agent_app.runtime.policy_gate_store import InMemoryPolicyGateStore
from agent_app.runtime.policy_release_gate_store import InMemoryReleaseGateRequirementStore
from agent_app.runtime.policy_release_gate_service import ReleaseGateAutomationService


# -- Helpers --


def _make_bundle(bundle_id: str = "pb_test", version: str = "1.0.0") -> PolicyBundle:
    return PolicyBundle(
        bundle_id=bundle_id,
        name="test-bundle",
        version=version,
        config_hash=compute_config_hash("test config"),
        created_at=datetime.now(timezone.utc),
    )


def _make_replay_result():
    """Create a simple passing replay result."""
    from agent_app.governance.policy_replay import (
        PolicyReplayResult,
        PolicyReplayRun,
        PolicyReplayStatus,
    )
    run = PolicyReplayRun(
        replay_id="replay_test",
        status=PolicyReplayStatus.COMPLETED,
        source_decision_count=100,
        changed_count=0,
        unchanged_count=100,
        failed_count=0,
        created_at=datetime.now(timezone.utc),
    )
    return PolicyReplayResult(replay=run, changes=[])


class _MockReplayRunner:
    async def run_replay(self, **kwargs):
        return _make_replay_result()


class _MockReplayStore:
    async def save(self, result):
        return result


def _make_context(permissions=None, user_id="alice", tenant_id="tenant_1") -> RunContext:
    return RunContext(
        run_id="run_test",
        user_id=user_id,
        tenant_id=tenant_id,
        permissions=permissions or [
            "policy.promotion.request",
            "policy.promotion.approve",
            "policy.promotion.execute",
            "policy.rollout.create",
            "policy.rollout.start",
            "policy.rollout.execute",
        ],
    )


def _make_gate_automation_service():
    """Create a ReleaseGateAutomationService with in-memory stores."""
    req_store = InMemoryReleaseGateRequirementStore()
    gate_store = InMemoryPolicyGateStore()
    return ReleaseGateAutomationService(
        requirement_store=req_store,
        gate_store=gate_store,
    ), req_store, gate_store


def _make_release_service(
    gate_automation_service=None,
    require_simulation_gate=False,
    max_age_seconds=None,
    promotion_store=None,
    gate_store=None,
):
    """Create a PolicyReleaseService with optional gate automation params."""
    if promotion_store is None:
        promotion_store = InMemoryPromotionRequestStore()
    if gate_store is None:
        gate_store = InMemoryPolicyGateStore()
    evaluator = PolicyGateEvaluator(rules=[
        PolicyGateRule(name="safe_default", max_changed_ratio=0.10, max_failed_replays=0),
    ])
    return PolicyReleaseService(
        bundle_store=InMemoryPolicyBundleStore(),
        replay_runner=_MockReplayRunner(),
        replay_store=_MockReplayStore(),
        gate_evaluator=evaluator,
        gate_store=gate_store,
        promotion_store=promotion_store,
        permission_checker=PolicyReleasePermissionChecker(),
        release_gate_automation_service=gate_automation_service,
        require_simulation_gate_for_promotion=require_simulation_gate,
        simulation_gate_max_age_seconds=max_age_seconds,
    )


# -- Rollout test stubs --


class _StubRolloutStore:
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
        from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus
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
        from agent_app.governance.policy_ring_assignment import RingActivationAssignment, RingActivationAssignmentStatus
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
        from agent_app.governance.policy_ring_assignment import RingActivationAssignment, RingActivationAssignmentStatus
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


# -- Test Classes --


class TestPolicyReleaseServiceGateEnforcement:
    """Tests for simulation gate enforcement in PolicyReleaseService.execute_promotion()."""

    @pytest.mark.asyncio
    async def test_enforcement_disabled_promotion_works_normally(self):
        """execute_promotion works when require_simulation_gate_for_promotion=False."""
        service = _make_release_service(require_simulation_gate=False)
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        ctx = _make_context()
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx)
        # Should succeed — no gate enforcement
        result = await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_execute_blocked_with_missing_gate(self):
        """execute_promotion raises ValueError when gate required but no result attached."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        service = _make_release_service(
            gate_automation_service=gate_svc,
            require_simulation_gate=True,
        )
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        ctx = _make_context()
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx)

        # Create a REQUIRED (unsatisfied) gate requirement for the promotion
        gate_req = ReleaseGateRequirement(
            requirement_id=f"rgr_{uuid.uuid4().hex[:12]}",
            source_type="promotion",
            source_id=req.promotion_id,
            status=ReleaseGateRequirementStatus.REQUIRED,
        )
        await req_store.create(gate_req)

        with pytest.raises(ValueError, match="simulation gate is required"):
            await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)

    @pytest.mark.asyncio
    async def test_execute_blocked_with_failed_gate(self):
        """execute_promotion raises ValueError when gate result is FAILED."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        service = _make_release_service(
            gate_automation_service=gate_svc,
            require_simulation_gate=True,
        )
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        ctx = _make_context()
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx)

        # Create a FAILED gate requirement for the promotion
        gate_req = ReleaseGateRequirement(
            requirement_id=f"rgr_{uuid.uuid4().hex[:12]}",
            source_type="promotion",
            source_id=req.promotion_id,
            status=ReleaseGateRequirementStatus.FAILED,
        )
        await req_store.create(gate_req)

        with pytest.raises(ValueError, match="simulation gate failed"):
            await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)

    @pytest.mark.asyncio
    async def test_execute_blocked_with_expired_gate(self):
        """execute_promotion raises ValueError when gate result has expired."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        service = _make_release_service(
            gate_automation_service=gate_svc,
            require_simulation_gate=True,
        )
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        ctx = _make_context()
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx)

        # Create an EXPIRED gate requirement for the promotion
        gate_req = ReleaseGateRequirement(
            requirement_id=f"rgr_{uuid.uuid4().hex[:12]}",
            source_type="promotion",
            source_id=req.promotion_id,
            status=ReleaseGateRequirementStatus.EXPIRED,
        )
        await req_store.create(gate_req)

        with pytest.raises(ValueError, match="simulation gate result has expired"):
            await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)

    @pytest.mark.asyncio
    async def test_execute_succeeds_with_satisfied_gate(self):
        """execute_promotion proceeds when simulation gate is SATISFIED."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        service = _make_release_service(
            gate_automation_service=gate_svc,
            require_simulation_gate=True,
        )
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        ctx = _make_context()
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx)

        # Create a SATISFIED gate requirement for the promotion
        gate_req = ReleaseGateRequirement(
            requirement_id=f"rgr_{uuid.uuid4().hex[:12]}",
            source_type="promotion",
            source_id=req.promotion_id,
            gate_result_id="gr_satisfied",
            status=ReleaseGateRequirementStatus.SATISFIED,
            satisfied_at=datetime.now(timezone.utc),
        )
        await req_store.create(gate_req)

        # Should succeed — gate is satisfied
        result = await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_request_promotion_creates_gate_requirement(self):
        """request_promotion auto-creates a gate requirement when configured."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        service = _make_release_service(
            gate_automation_service=gate_svc,
            require_simulation_gate=True,
        )
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        ctx = _make_context()
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)

        # Verify gate requirement was created
        stored_req = await req_store.get_for_source("promotion", req.promotion_id)
        assert stored_req is not None
        assert stored_req.status == ReleaseGateRequirementStatus.REQUIRED
        assert stored_req.source_id == req.promotion_id

        # Verify the request has the requirement_id
        assert req.simulation_gate_required is True
        assert req.simulation_gate_requirement_id is not None

    @pytest.mark.asyncio
    async def test_execute_blocked_writes_audit_event(self):
        """When execute is blocked, an audit event is written."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        audit_logger = InMemoryAuditLogger()
        promotion_store = InMemoryPromotionRequestStore()
        service = PolicyReleaseService(
            bundle_store=InMemoryPolicyBundleStore(),
            replay_runner=_MockReplayRunner(),
            replay_store=_MockReplayStore(),
            gate_evaluator=PolicyGateEvaluator(rules=[
                PolicyGateRule(name="safe_default", max_changed_ratio=0.10, max_failed_replays=0),
            ]),
            gate_store=InMemoryPolicyGateStore(),
            promotion_store=promotion_store,
            permission_checker=PolicyReleasePermissionChecker(),
            audit_logger=audit_logger,
            release_gate_automation_service=gate_svc,
            require_simulation_gate_for_promotion=True,
        )
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        ctx = _make_context()
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx)

        # Create REQUIRED gate requirement
        gate_req = ReleaseGateRequirement(
            requirement_id=f"rgr_{uuid.uuid4().hex[:12]}",
            source_type="promotion",
            source_id=req.promotion_id,
            status=ReleaseGateRequirementStatus.REQUIRED,
        )
        await req_store.create(gate_req)

        with pytest.raises(ValueError, match="simulation gate is required"):
            await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)

        # Verify audit event was written
        event_types = [e.event_type for e in audit_logger.list_events()]
        assert "policy.promotion.gate.execution_blocked" in event_types


class TestRolloutServiceGateEnforcement:
    """Tests for simulation gate enforcement in RolloutService.run_next_step()."""

    def _make_rollout_service(self, gate_automation_service=None):
        store = _StubRolloutStore()
        release_svc = _StubReleaseService()
        checker = _StubPermissionChecker()
        logger = _StubAuditLogger()
        return RolloutService(
            rollout_store=store,
            release_service=release_svc,
            audit_logger=logger,
            permission_checker=checker,
            release_gate_automation_service=gate_automation_service,
        )

    @pytest.mark.asyncio
    async def test_step_blocks_when_gate_missing(self):
        """Step with requires_simulation_gate=True becomes BLOCKED when gate is REQUIRED."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        svc = self._make_rollout_service(gate_automation_service=gate_svc)
        ctx = _make_context()

        step = RolloutStep(
            step_id="prod_activate",
            step_type=RolloutStepType.ACTIVATE,
            environment="prod",
            ring_name="stable",
            requires_simulation_gate=True,
        )
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_test",
            steps=[step],
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )

        # Create a REQUIRED (unsatisfied) gate requirement for the step
        gate_req = ReleaseGateRequirement(
            requirement_id=f"rgr_{uuid.uuid4().hex[:12]}",
            source_type="rollout_step",
            source_id="prod_activate",
            status=ReleaseGateRequirementStatus.REQUIRED,
        )
        await req_store.create(gate_req)

        # Run the step — should be blocked
        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step_result = plan.steps[0]
        assert step_result.status == RolloutStepStatus.BLOCKED
        assert step_result.error is not None
        assert step_result.error["type"] == "simulation_gate_required"
        assert "required" in step_result.error["requirement_status"]
        # Plan should still be ACTIVE (not FAILED)
        assert plan.status == RolloutPlanStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_step_executes_when_gate_satisfied(self):
        """Step with requires_simulation_gate=True proceeds when gate is SATISFIED."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        svc = self._make_rollout_service(gate_automation_service=gate_svc)
        ctx = _make_context()

        step = RolloutStep(
            step_id="dev_activate",
            step_type=RolloutStepType.ACTIVATE,
            environment="dev",
            ring_name="stable",
            requires_simulation_gate=True,
        )
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_test",
            steps=[step],
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )

        # Create a SATISFIED gate requirement for the step
        gate_req = ReleaseGateRequirement(
            requirement_id=f"rgr_{uuid.uuid4().hex[:12]}",
            source_type="rollout_step",
            source_id="dev_activate",
            gate_result_id="gr_satisfied",
            status=ReleaseGateRequirementStatus.SATISFIED,
            satisfied_at=datetime.now(timezone.utc),
        )
        await req_store.create(gate_req)

        # Run the step — should succeed (gate is satisfied)
        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step_result = plan.steps[0]
        assert step_result.status == RolloutStepStatus.SUCCEEDED
        assert step_result.activation_id is not None

    @pytest.mark.asyncio
    async def test_step_blocks_when_gate_failed(self):
        """Step with requires_simulation_gate=True becomes BLOCKED when gate is FAILED."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        svc = self._make_rollout_service(gate_automation_service=gate_svc)
        ctx = _make_context()

        step = RolloutStep(
            step_id="prod_activate",
            step_type=RolloutStepType.ACTIVATE,
            environment="prod",
            ring_name="stable",
            requires_simulation_gate=True,
        )
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_test",
            steps=[step],
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )

        # Create a FAILED gate requirement for the step
        gate_req = ReleaseGateRequirement(
            requirement_id=f"rgr_{uuid.uuid4().hex[:12]}",
            source_type="rollout_step",
            source_id="prod_activate",
            status=ReleaseGateRequirementStatus.FAILED,
        )
        await req_store.create(gate_req)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step_result = plan.steps[0]
        assert step_result.status == RolloutStepStatus.BLOCKED
        assert step_result.error["requirement_status"] == "failed"

    @pytest.mark.asyncio
    async def test_step_blocks_when_gate_expired(self):
        """Step with requires_simulation_gate=True becomes BLOCKED when gate is EXPIRED."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        svc = self._make_rollout_service(gate_automation_service=gate_svc)
        ctx = _make_context()

        step = RolloutStep(
            step_id="prod_activate",
            step_type=RolloutStepType.ACTIVATE,
            environment="prod",
            ring_name="stable",
            requires_simulation_gate=True,
        )
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_test",
            steps=[step],
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )

        # Create an EXPIRED gate requirement for the step
        gate_req = ReleaseGateRequirement(
            requirement_id=f"rgr_{uuid.uuid4().hex[:12]}",
            source_type="rollout_step",
            source_id="prod_activate",
            status=ReleaseGateRequirementStatus.EXPIRED,
        )
        await req_store.create(gate_req)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step_result = plan.steps[0]
        assert step_result.status == RolloutStepStatus.BLOCKED
        assert step_result.error["requirement_status"] == "expired"

    @pytest.mark.asyncio
    async def test_step_without_gate_requirement_not_blocked(self):
        """Step with requires_simulation_gate=False is not affected by gate enforcement."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        svc = self._make_rollout_service(gate_automation_service=gate_svc)
        ctx = _make_context()

        step = RolloutStep(
            step_id="dev_activate",
            step_type=RolloutStepType.ACTIVATE,
            environment="dev",
            ring_name="stable",
            requires_simulation_gate=False,  # default
        )
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_test",
            steps=[step],
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )

        # No gate requirement created — should execute normally
        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step_result = plan.steps[0]
        assert step_result.status == RolloutStepStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_blocked_step_writes_audit(self):
        """Blocked step writes a step_blocked audit event."""
        gate_svc, req_store, _ = _make_gate_automation_service()
        logger = _StubAuditLogger()
        svc = RolloutService(
            rollout_store=_StubRolloutStore(),
            release_service=_StubReleaseService(),
            audit_logger=logger,
            permission_checker=_StubPermissionChecker(),
            release_gate_automation_service=gate_svc,
        )
        ctx = _make_context()

        step = RolloutStep(
            step_id="prod_activate",
            step_type=RolloutStepType.ACTIVATE,
            environment="prod",
            ring_name="stable",
            requires_simulation_gate=True,
        )
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_test",
            steps=[step],
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )

        # Create a REQUIRED gate requirement
        gate_req = ReleaseGateRequirement(
            requirement_id=f"rgr_{uuid.uuid4().hex[:12]}",
            source_type="rollout_step",
            source_id="prod_activate",
            status=ReleaseGateRequirementStatus.REQUIRED,
        )
        await req_store.create(gate_req)

        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )

        # Verify audit event
        event_types = [e.event_type for e in logger.events]
        assert "policy.rollout.step_blocked" in event_types
        blocked_event = next(e for e in logger.events if e.event_type == "policy.rollout.step_blocked")
        assert blocked_event.data["reason"] == "simulation_gate_required"

    @pytest.mark.asyncio
    async def test_no_gate_service_step_still_executes(self):
        """Step with requires_simulation_gate=True but no gate service executes normally (backward compat)."""
        svc = self._make_rollout_service(gate_automation_service=None)
        ctx = _make_context()

        step = RolloutStep(
            step_id="dev_activate",
            step_type=RolloutStepType.ACTIVATE,
            environment="dev",
            ring_name="stable",
            requires_simulation_gate=True,
        )
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_test",
            steps=[step],
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )

        # Without gate service, the step should proceed normally
        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        step_result = plan.steps[0]
        assert step_result.status == RolloutStepStatus.SUCCEEDED


class TestBackwardCompatibility:
    """Verify existing promotion and rollout flows still work after Phase 42 changes."""

    @pytest.mark.asyncio
    async def test_promotion_lifecycle_without_gate(self):
        """Full promotion lifecycle works without gate automation service."""
        service = _make_release_service()  # No gate automation
        bundle = await service.create_bundle(name="test", version="1.0.0", config_path="test.yaml")
        await service.run_gate(bundle_id=bundle.bundle_id, created_by="admin")
        ctx = _make_context()
        req = await service.request_promotion(bundle_id=bundle.bundle_id, requested_by="alice", context=ctx)
        assert req.promotion_id.startswith("pr_")
        assert req.simulation_gate_required is False
        assert req.simulation_gate_requirement_id is None

        updated = await service.approve_promotion(promotion_id=req.promotion_id, approved_by="reviewer", context=ctx)
        assert updated.status == PromotionRequestStatus.APPROVED

        result = await service.execute_promotion(promotion_id=req.promotion_id, executed_by="rm", context=ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_rollout_step_without_gate(self):
        """Rollout step without requires_simulation_gate works normally."""
        svc = RolloutService(
            rollout_store=_StubRolloutStore(),
            release_service=_StubReleaseService(),
            audit_logger=_StubAuditLogger(),
            permission_checker=_StubPermissionChecker(),
        )
        ctx = _make_context()
        step = RolloutStep(
            step_id="dev_activate",
            step_type=RolloutStepType.ACTIVATE,
            environment="dev",
            ring_name="stable",
        )
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_test",
            steps=[step],
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )
        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        assert plan.steps[0].status == RolloutStepStatus.SUCCEEDED
        assert plan.status == RolloutPlanStatus.COMPLETED
