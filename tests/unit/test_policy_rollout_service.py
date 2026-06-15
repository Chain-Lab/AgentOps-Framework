"""Tests for RolloutService — orchestrates multi-environment rollout plans."""
import pytest
import uuid
from datetime import datetime, timezone

from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)
from agent_app.governance.policy_promotion import PromotionRequest, PromotionRequestStatus
from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus
from agent_app.governance.policy_ring_assignment import (
    RingActivationAssignment,
    RingActivationAssignmentStatus,
)
from agent_app.governance.policy_rbac import PolicyReleasePermission
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.governance.audit import AuditEvent
from agent_app.core.context import RunContext
from agent_app.runtime.policy_rollout_service import RolloutService


# -- Stubs --


class _StubRolloutStore:
    """In-memory rollout store for testing."""

    def __init__(self):
        self._plans: dict[str, RolloutPlan] = {}

    async def create(self, plan: RolloutPlan) -> RolloutPlan:
        self._plans[plan.rollout_id] = plan
        return plan

    async def get(self, rollout_id: str) -> RolloutPlan | None:
        return self._plans.get(rollout_id)

    async def update(self, plan: RolloutPlan) -> RolloutPlan:
        if plan.rollout_id not in self._plans:
            raise KeyError(plan.rollout_id)
        self._plans[plan.rollout_id] = plan
        return plan

    async def list(self, status=None, bundle_id=None):
        plans = list(self._plans.values())
        if status:
            plans = [p for p in plans if p.status == status]
        if bundle_id:
            plans = [p for p in plans if p.bundle_id == bundle_id]
        return plans


class _StubReleaseService:
    """Stub release service that records calls."""

    def __init__(self):
        self.promotions_requested: list[PromotionRequest] = []
        self.promotions_approved: list[PromotionRequest] = []
        self.promotions_executed: list[PolicyActivation] = []
        self.ring_assignments: list[RingActivationAssignment] = []
        self.ring_promotions: list[RingActivationAssignment] = []

    async def request_promotion(
        self, bundle_id, requested_by, context, reason=None, gate_result_id=None
    ):
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
                approved = pr.model_copy(
                    update={
                        "status": PromotionRequestStatus.APPROVED,
                        "resolved_by": approved_by,
                    }
                )
                self.promotions_approved.append(approved)
                return approved
        raise KeyError(promotion_id)

    async def execute_promotion(
        self,
        promotion_id,
        executed_by,
        context,
        bypass_gate=False,
        bypass_reason=None,
        environment="prod",
        reason=None,
    ):
        activation = PolicyActivation(
            activation_id=f"pa_{uuid.uuid4().hex[:8]}",
            environment=environment,
            bundle_id="pb_test",
            config_hash="abc123",
            promotion_id=promotion_id,
            activated_by=executed_by,
            status=PolicyActivationStatus.ACTIVE,
            reason=reason,
        )
        self.promotions_executed.append(activation)
        return activation

    async def assign_activation_to_ring(
        self, environment, ring_name, activation_id, assigned_by, context, reason=None
    ):
        assignment = RingActivationAssignment(
            assignment_id=f"ra_{uuid.uuid4().hex[:8]}",
            environment=environment,
            ring_name=ring_name,
            activation_id=activation_id,
            bundle_id="pb_test",
            config_hash="abc123",
            status=RingActivationAssignmentStatus.ACTIVE,
            assigned_by=assigned_by,
            reason=reason,
        )
        self.ring_assignments.append(assignment)
        return assignment

    async def promote_canary_to_stable(
        self, environment, canary_ring, stable_ring, promoted_by, context, reason=None
    ):
        assignment = RingActivationAssignment(
            assignment_id=f"ra_{uuid.uuid4().hex[:8]}",
            environment=environment,
            ring_name=stable_ring,
            activation_id=f"pa_auto_{uuid.uuid4().hex[:6]}",
            bundle_id="pb_test",
            config_hash="abc123",
            status=RingActivationAssignmentStatus.ACTIVE,
            assigned_by=promoted_by,
            reason=reason,
        )
        self.ring_promotions.append(assignment)
        return assignment

    @property
    def activation_store(self):
        return _StubActivationStore()


class _StubActivationStore:
    async def list(self, environment=None):
        return []


class _StubPermissionChecker:
    def __init__(self, allowed=True):
        self._allowed = allowed

    async def check(self, permission, context):
        return self._allowed


class _StubAuditLogger:
    def __init__(self):
        self.events: list[AuditEvent] = []

    async def log(self, event: AuditEvent):
        self.events.append(event)


class _StubEventStore:
    def __init__(self):
        self.events: list = []

    async def append(self, event):
        self.events.append(event)

    async def list(self, **kwargs):
        return self.events


class _StubEvalRunner:
    """Stub eval runner that returns a configurable result."""

    def __init__(self, passed=True, total=1, failures=0):
        self._passed = passed
        self._total = total
        self._failures = failures

    async def run_suite(self, suite):
        return type(
            "Result",
            (),
            {
                "passed": self._passed,
                "total": self._total,
                "failures": self._failures,
            },
        )()


# -- Helpers --


def _make_service(
    allowed=True, eval_runner=None, audit_logger=None, event_store=None
):
    store = _StubRolloutStore()
    release_svc = _StubReleaseService()
    checker = _StubPermissionChecker(allowed=allowed)
    logger = audit_logger or _StubAuditLogger()
    ev_store = event_store or _StubEventStore()
    return RolloutService(
        rollout_store=store,
        release_service=release_svc,
        eval_runner=eval_runner,
        audit_logger=logger,
        event_store=ev_store,
        permission_checker=checker,
    )


def _make_context(permissions=None):
    return RunContext(
        run_id=f"run_{uuid.uuid4().hex[:8]}",
        user_id="test_user",
        tenant_id="test_tenant",
        permissions=permissions
        or [
            "policy.rollout.create",
            "policy.rollout.start",
            "policy.rollout.execute",
            "policy.rollout.cancel",
        ],
    )


def _make_steps():
    return [
        RolloutStep(
            step_id="dev_activate",
            step_type=RolloutStepType.ACTIVATE,
            environment="dev",
            ring_name="stable",
        ),
        RolloutStep(
            step_id="staging_activate",
            step_type=RolloutStepType.ACTIVATE,
            environment="staging",
            ring_name="stable",
            require_previous_step="dev_activate",
        ),
    ]


# -- Tests --


class TestPermissionChecks:
    """Verify that each public method enforces RBAC."""

    @pytest.mark.asyncio
    async def test_create_plan_requires_permission(self):
        svc = _make_service(allowed=False)
        ctx = _make_context()
        steps = _make_steps()
        with pytest.raises(PermissionError, match="policy.rollout.create"):
            await svc.create_plan(
                name="test",
                bundle_id="pb_1",
                steps=steps,
                created_by="user1",
                context=ctx,
            )

    @pytest.mark.asyncio
    async def test_start_plan_requires_permission(self):
        svc = _make_service(allowed=True)
        ctx = _make_context()
        steps = _make_steps()
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
        )
        # Now deny permission for start
        svc._permission_checker = _StubPermissionChecker(allowed=False)
        with pytest.raises(PermissionError, match="policy.rollout.start"):
            await svc.start_plan(
                rollout_id=plan.rollout_id,
                started_by="user1",
                context=ctx,
            )

    @pytest.mark.asyncio
    async def test_cancel_plan_requires_permission(self):
        svc = _make_service(allowed=True)
        ctx = _make_context()
        steps = _make_steps()
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
        )
        svc._permission_checker = _StubPermissionChecker(allowed=False)
        with pytest.raises(PermissionError, match="policy.rollout.cancel"):
            await svc.cancel_plan(
                rollout_id=plan.rollout_id,
                cancelled_by="user1",
                context=ctx,
            )

    @pytest.mark.asyncio
    async def test_run_next_requires_permission(self):
        svc = _make_service(allowed=True)
        ctx = _make_context()
        steps = _make_steps()
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )
        svc._permission_checker = _StubPermissionChecker(allowed=False)
        with pytest.raises(PermissionError, match="policy.rollout.execute"):
            await svc.run_next_step(
                rollout_id=plan.rollout_id,
                actor_id="user1",
                context=ctx,
            )


class TestCreatePlan:
    @pytest.mark.asyncio
    async def test_create_plan_stores_and_returns(self):
        svc = _make_service()
        ctx = _make_context()
        steps = _make_steps()
        plan = await svc.create_plan(
            name="My Rollout",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
            reason="Deploy v2",
        )
        assert plan.rollout_id.startswith("ro_")
        assert plan.name == "My Rollout"
        assert plan.bundle_id == "pb_1"
        assert plan.status == RolloutPlanStatus.DRAFT
        assert len(plan.steps) == 2
        assert plan.created_by == "user1"
        assert plan.reason == "Deploy v2"
        # Verify stored
        stored = await svc._rollout_store.get(plan.rollout_id)
        assert stored is not None
        assert stored.rollout_id == plan.rollout_id


class TestStartPlan:
    @pytest.mark.asyncio
    async def test_start_plan_sets_active(self):
        svc = _make_service()
        ctx = _make_context()
        steps = _make_steps()
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
        )
        started = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )
        assert started.status == RolloutPlanStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_start_plan_rejects_non_draft(self):
        svc = _make_service()
        ctx = _make_context()
        steps = _make_steps()
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
        )
        # Start it once
        await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )
        # Try to start again — should fail
        with pytest.raises(ValueError, match="Must be DRAFT"):
            await svc.start_plan(
                rollout_id=plan.rollout_id,
                started_by="user1",
                context=ctx,
            )


class TestStepDependencies:
    @pytest.mark.asyncio
    async def test_previous_step_blocking(self):
        """Step with require_previous_step can't run until previous succeeds."""
        svc = _make_service()
        ctx = _make_context()
        steps = _make_steps()  # staging_activate requires dev_activate
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )
        # First step should be dev_activate (no dependency)
        next_step = svc._find_next_runnable_step(plan)
        assert next_step is not None
        assert next_step.step_id == "dev_activate"

        # Run the first step
        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        # dev_activate should have succeeded
        dev_step = next(s for s in plan.steps if s.step_id == "dev_activate")
        assert dev_step.status == RolloutStepStatus.SUCCEEDED

        # Now staging_activate should be runnable
        next_step = svc._find_next_runnable_step(plan)
        assert next_step is not None
        assert next_step.step_id == "staging_activate"


class TestActivateStep:
    @pytest.mark.asyncio
    async def test_activate_step_succeeds(self):
        """ACTIVATE step creates promotion + activation + ring assignment."""
        svc = _make_service()
        ctx = _make_context()
        steps = [
            RolloutStep(
                step_id="dev_activate",
                step_type=RolloutStepType.ACTIVATE,
                environment="dev",
                ring_name="stable",
            ),
        ]
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
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
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.SUCCEEDED
        assert step.activation_id is not None
        assert step.activation_id.startswith("pa_")
        assert step.assignment_id is not None
        assert step.assignment_id.startswith("ra_")
        # Verify release service was called
        assert len(svc._release_service.promotions_requested) == 1
        assert len(svc._release_service.promotions_approved) == 1
        assert len(svc._release_service.promotions_executed) == 1
        assert len(svc._release_service.ring_assignments) == 1


class TestCanaryEvalStep:
    @pytest.mark.asyncio
    async def test_canary_eval_no_runner_fails(self):
        """CANARY_EVAL fails when no eval runner is configured."""
        svc = _make_service(eval_runner=None)
        ctx = _make_context()
        steps = [
            RolloutStep(
                step_id="canary_eval",
                step_type=RolloutStepType.CANARY_EVAL,
                environment="prod",
                eval_suite="smoke",
            ),
        ]
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
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
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.FAILED
        assert step.error is not None
        assert step.error["type"] == "no_eval_runner"


class TestPromoteRingStep:
    @pytest.mark.asyncio
    async def test_promote_ring_step_succeeds(self):
        """PROMOTE_RING step calls promote_canary_to_stable."""
        svc = _make_service()
        ctx = _make_context()
        steps = [
            RolloutStep(
                step_id="promote_ring",
                step_type=RolloutStepType.PROMOTE_RING,
                environment="prod",
                from_ring="canary",
                to_ring="stable",
            ),
        ]
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
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
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.SUCCEEDED
        assert step.assignment_id is not None
        # Verify promote_canary_to_stable was called
        assert len(svc._release_service.ring_promotions) == 1
        promo = svc._release_service.ring_promotions[0]
        assert promo.ring_name == "stable"


class TestApprovalBlocking:
    @pytest.mark.asyncio
    async def test_approval_required_marks_blocked(self):
        """requires_approval=True marks step BLOCKED."""
        svc = _make_service()
        ctx = _make_context()
        steps = [
            RolloutStep(
                step_id="needs_approval",
                step_type=RolloutStepType.ACTIVATE,
                environment="prod",
                requires_approval=True,
            ),
        ]
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
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
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.BLOCKED
        assert step.error is not None
        assert step.error["type"] == "approval_required"
        # Plan should still be ACTIVE (not FAILED)
        assert plan.status == RolloutPlanStatus.ACTIVE


class TestRunAllAvailable:
    @pytest.mark.asyncio
    async def test_run_all_stops_on_failure(self):
        """run_all_available stops when a step fails."""
        svc = _make_service(eval_runner=None)  # No eval runner → canary_eval fails
        ctx = _make_context()
        steps = [
            RolloutStep(
                step_id="dev_activate",
                step_type=RolloutStepType.ACTIVATE,
                environment="dev",
                ring_name="stable",
            ),
            RolloutStep(
                step_id="canary_eval",
                step_type=RolloutStepType.CANARY_EVAL,
                environment="dev",
                eval_suite="smoke",
                require_previous_step="dev_activate",
            ),
        ]
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )
        plan = await svc.run_all_available(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )
        # First step should have succeeded
        dev_step = next(s for s in plan.steps if s.step_id == "dev_activate")
        assert dev_step.status == RolloutStepStatus.SUCCEEDED
        # Second step should have failed (no eval runner)
        eval_step = next(s for s in plan.steps if s.step_id == "canary_eval")
        assert eval_step.status == RolloutStepStatus.FAILED
        # Plan should be FAILED
        assert plan.status == RolloutPlanStatus.FAILED


class TestPlanCompletion:
    @pytest.mark.asyncio
    async def test_plan_completes_when_all_succeeded(self):
        """Plan status becomes COMPLETED when all steps succeed."""
        svc = _make_service()
        ctx = _make_context()
        steps = [
            RolloutStep(
                step_id="dev_activate",
                step_type=RolloutStepType.ACTIVATE,
                environment="dev",
                ring_name="stable",
            ),
        ]
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
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
        assert plan.status == RolloutPlanStatus.COMPLETED
        step = plan.steps[0]
        assert step.status == RolloutStepStatus.SUCCEEDED


class TestAuditEvents:
    @pytest.mark.asyncio
    async def test_audit_events_written(self):
        """Verify audit events are logged for create/start/cancel."""
        audit_logger = _StubAuditLogger()
        svc = _make_service(audit_logger=audit_logger)
        ctx = _make_context()
        steps = _make_steps()

        # Create
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
        )
        # Start
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )
        # Cancel
        plan = await svc.cancel_plan(
            rollout_id=plan.rollout_id,
            cancelled_by="user1",
            context=ctx,
            reason="Changed mind",
        )

        # Check audit events
        event_types = [e.event_type for e in audit_logger.events]
        assert "policy.rollout.created" in event_types
        assert "policy.rollout.started" in event_types
        assert "policy.rollout.cancelled" in event_types

        # Verify cancelled event data
        cancelled_event = next(
            e for e in audit_logger.events if e.event_type == "policy.rollout.cancelled"
        )
        assert cancelled_event.user_id == "user1"
        assert cancelled_event.data["reason"] == "Changed mind"


class TestChangeEvents:
    @pytest.mark.asyncio
    async def test_change_events_emitted(self):
        """Verify change events are emitted for key lifecycle transitions."""
        event_store = _StubEventStore()
        svc = _make_service(event_store=event_store)
        ctx = _make_context()
        steps = [
            RolloutStep(
                step_id="dev_activate",
                step_type=RolloutStepType.ACTIVATE,
                environment="dev",
                ring_name="stable",
            ),
        ]

        # Create
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
        )
        # Start
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )
        # Run step
        plan = await svc.run_next_step(
            rollout_id=plan.rollout_id,
            actor_id="user1",
            context=ctx,
        )

        event_types = [e.event_type for e in event_store.events]
        assert PolicyChangeEventType.ROLLOUT_CREATED in event_types
        assert PolicyChangeEventType.ROLLOUT_STARTED in event_types
        assert PolicyChangeEventType.ROLLOUT_STEP_SUCCEEDED in event_types
        assert PolicyChangeEventType.ROLLOUT_COMPLETED in event_types


class TestCancelPlan:
    @pytest.mark.asyncio
    async def test_cancel_active_plan(self):
        svc = _make_service()
        ctx = _make_context()
        steps = _make_steps()
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
        )
        plan = await svc.start_plan(
            rollout_id=plan.rollout_id,
            started_by="user1",
            context=ctx,
        )
        plan = await svc.cancel_plan(
            rollout_id=plan.rollout_id,
            cancelled_by="user1",
            context=ctx,
            reason="Emergency stop",
        )
        assert plan.status == RolloutPlanStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_non_cancellable_status(self):
        svc = _make_service()
        ctx = _make_context()
        steps = _make_steps()
        plan = await svc.create_plan(
            name="test",
            bundle_id="pb_1",
            steps=steps,
            created_by="user1",
            context=ctx,
        )
        # Manually set status to COMPLETED
        plan = plan.model_copy(update={"status": RolloutPlanStatus.COMPLETED})
        await svc._rollout_store.update(plan)
        with pytest.raises(ValueError, match="Must be DRAFT or ACTIVE"):
            await svc.cancel_plan(
                rollout_id=plan.rollout_id,
                cancelled_by="user1",
                context=ctx,
            )


class TestNotFoundErrors:
    @pytest.mark.asyncio
    async def test_start_nonexistent_plan(self):
        svc = _make_service()
        ctx = _make_context()
        with pytest.raises(KeyError, match="not found"):
            await svc.start_plan(
                rollout_id="ro_nonexistent",
                started_by="user1",
                context=ctx,
            )

    @pytest.mark.asyncio
    async def test_run_step_nonexistent_plan(self):
        svc = _make_service()
        ctx = _make_context()
        with pytest.raises(KeyError, match="not found"):
            await svc.run_next_step(
                rollout_id="ro_nonexistent",
                actor_id="user1",
                context=ctx,
            )
