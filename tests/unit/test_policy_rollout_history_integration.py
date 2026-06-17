"""Phase 45 Task 4 — Integration tests for rollout history recorder in existing services.

Tests that services correctly call history_recorder.record() at key lifecycle points,
and that the integration is optional (no-op when recorder is None) and fault-tolerant
(recorder errors don't break service operations).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
from agent_app.governance.policy_rollout_approval import (
    RolloutApprovalPolicy,
    RolloutApprovalPolicyType,
    RolloutStepApproval,
    RolloutStepApprovalStatus,
)
from agent_app.governance.policy_rollout_history import (
    RolloutAnalyticsReport,
    RolloutHistoryEventType,
    RolloutTimeline,
)
from agent_app.runtime.policy_compliance_export import (
    rollout_analytics_report_to_csv_rows,
    rollout_analytics_report_to_json,
    rollout_timeline_to_json,
)
from agent_app.runtime.policy_expiration_service import PolicyExpirationService
from agent_app.runtime.policy_notification_service import PolicyNotificationService
from agent_app.runtime.policy_rollout_service import RolloutService


# --- Helpers ---

def _make_step(
    step_id: str = "step_1",
    step_type: RolloutStepType = RolloutStepType.ACTIVATE,
    environment: str = "production",
    ring_name: str | None = None,
    status: RolloutStepStatus = RolloutStepStatus.PENDING,
    requires_approval: bool = False,
    requires_simulation_gate: bool = False,
    simulation_gate_mode: RolloutGateMode = RolloutGateMode.DISABLED,
) -> RolloutStep:
    return RolloutStep(
        step_id=step_id,
        step_type=step_type,
        environment=environment,
        ring_name=ring_name,
        status=status,
        requires_approval=requires_approval,
        requires_simulation_gate=requires_simulation_gate,
        simulation_gate_mode=simulation_gate_mode,
    )


def _make_plan(
    rollout_id: str = "ro_test123",
    name: str = "test-plan",
    bundle_id: str = "b_test",
    steps: list[RolloutStep] | None = None,
    status: RolloutPlanStatus = RolloutPlanStatus.DRAFT,
    created_by: str = "user1",
) -> RolloutPlan:
    return RolloutPlan(
        rollout_id=rollout_id,
        name=name,
        bundle_id=bundle_id,
        status=status,
        steps=steps or [_make_step()],
        created_by=created_by,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _make_context(
    user_id: str = "user1",
    tenant_id: str = "tenant1",
) -> RunContext:
    return RunContext(
        run_id="run_test",
        user_id=user_id,
        tenant_id=tenant_id,
        roles=[],
        permissions=[],
    )


def _make_mock_store(plan: RolloutPlan | None = None) -> AsyncMock:
    store = AsyncMock()
    store.create = AsyncMock(return_value=plan or _make_plan())
    store.get = AsyncMock(return_value=plan or _make_plan())
    store.update = AsyncMock(side_effect=lambda p: p)
    store.delete = AsyncMock()
    store.list = AsyncMock(return_value=[])
    return store


def _make_mock_release_service() -> AsyncMock:
    svc = AsyncMock()
    promotion = MagicMock()
    promotion.promotion_id = "promo_1"
    svc.request_promotion = AsyncMock(return_value=promotion)
    svc.approve_promotion = AsyncMock(return_value=promotion)
    activation = MagicMock()
    activation.activation_id = "act_1"
    svc.execute_promotion = AsyncMock(return_value=activation)
    assignment = MagicMock()
    assignment.assignment_id = "assign_1"
    svc.assign_activation_to_ring = AsyncMock(return_value=assignment)
    return svc


# --- RolloutService Tests ---

class TestRolloutServiceHistoryIntegration:
    """Tests that RolloutService records history events at key lifecycle points."""

    async def test_rollout_service_records_created(self) -> None:
        recorder = AsyncMock()
        plan = _make_plan()
        store = _make_mock_store(plan)
        service = RolloutService(
            rollout_store=store,
            release_service=_make_mock_release_service(),
            history_recorder=recorder,
        )
        ctx = _make_context()

        await service.create_plan(
            name="test",
            bundle_id="b_1",
            steps=[_make_step()],
            created_by="creator",
            context=ctx,
        )

        recorder.record.assert_called_once()
        call_kwargs = recorder.record.call_args
        assert call_kwargs.kwargs["event_type"] == RolloutHistoryEventType.ROLLOUT_CREATED
        assert call_kwargs.kwargs["actor_id"] == "creator"

    async def test_rollout_service_records_started(self) -> None:
        recorder = AsyncMock()
        plan = _make_plan(status=RolloutPlanStatus.DRAFT)
        store = _make_mock_store(plan)
        service = RolloutService(
            rollout_store=store,
            release_service=_make_mock_release_service(),
            history_recorder=recorder,
        )
        ctx = _make_context()

        await service.start_plan(
            rollout_id="ro_test123",
            started_by="starter",
            context=ctx,
        )

        recorder.record.assert_called_once()
        call_kwargs = recorder.record.call_args
        assert call_kwargs.kwargs["event_type"] == RolloutHistoryEventType.ROLLOUT_STARTED
        assert call_kwargs.kwargs["actor_id"] == "starter"

    async def test_rollout_service_records_step_events(self) -> None:
        recorder = AsyncMock()
        # Create a plan with a step that will succeed
        step = _make_step(step_id="step_1", status=RolloutStepStatus.PENDING)
        plan = _make_plan(steps=[step], status=RolloutPlanStatus.ACTIVE)
        store = _make_mock_store(plan)
        service = RolloutService(
            rollout_store=store,
            release_service=_make_mock_release_service(),
            history_recorder=recorder,
        )
        ctx = _make_context()

        await service.run_next_step(
            rollout_id="ro_test123",
            actor_id="actor1",
            context=ctx,
        )

        # Should have recorded at least one event (STEP_SUCCEEDED)
        assert recorder.record.call_count >= 1
        event_types = [c.kwargs["event_type"] for c in recorder.record.call_args_list]
        assert RolloutHistoryEventType.STEP_SUCCEEDED in event_types

    async def test_rollout_service_no_recorder_works(self) -> None:
        plan = _make_plan(status=RolloutPlanStatus.DRAFT)
        store = _make_mock_store(plan)
        service = RolloutService(
            rollout_store=store,
            release_service=_make_mock_release_service(),
            history_recorder=None,
        )
        ctx = _make_context()

        # Should not raise
        result = await service.create_plan(
            name="test",
            bundle_id="b_1",
            steps=[_make_step()],
            created_by="creator",
            context=ctx,
        )
        assert result is not None

    async def test_rollout_service_records_cancelled(self) -> None:
        recorder = AsyncMock()
        plan = _make_plan(status=RolloutPlanStatus.ACTIVE)
        store = _make_mock_store(plan)
        service = RolloutService(
            rollout_store=store,
            release_service=_make_mock_release_service(),
            history_recorder=recorder,
        )
        ctx = _make_context()

        await service.cancel_plan(
            rollout_id="ro_test123",
            cancelled_by="canceller",
            context=ctx,
        )

        event_types = [c.kwargs["event_type"] for c in recorder.record.call_args_list]
        assert RolloutHistoryEventType.ROLLOUT_CANCELLED in event_types

    async def test_rollout_service_records_approval_events(self) -> None:
        recorder = AsyncMock()
        step = _make_step(step_id="step_1", requires_approval=True, status=RolloutStepStatus.PENDING)
        plan = _make_plan(steps=[step], status=RolloutPlanStatus.ACTIVE)
        store = _make_mock_store(plan)

        approval = RolloutStepApproval(
            approval_id="rsa_test",
            rollout_id="ro_test123",
            step_id="step_1",
            bundle_id="b_test",
            environment="production",
            requested_by="requester",
            status=RolloutStepApprovalStatus.PENDING,
            policy=RolloutApprovalPolicy(policy_type=RolloutApprovalPolicyType.SINGLE),
            created_at=datetime.now(timezone.utc),
        )
        approval_store = AsyncMock()
        approval_store.get_pending_for_step = AsyncMock(return_value=None)
        approval_store.create = AsyncMock(return_value=approval)

        service = RolloutService(
            rollout_store=store,
            release_service=_make_mock_release_service(),
            approval_store=approval_store,
            history_recorder=recorder,
        )
        ctx = _make_context()

        await service.request_step_approval(
            rollout_id="ro_test123",
            step_id="step_1",
            requested_by="requester",
            context=ctx,
        )

        event_types = [c.kwargs["event_type"] for c in recorder.record.call_args_list]
        assert RolloutHistoryEventType.APPROVAL_REQUESTED in event_types


# --- Gate Service Tests ---

class TestGateServiceHistoryIntegration:
    """Tests that RolloutGateAutomationService records gate history events."""

    async def test_gate_service_records_gate_events(self) -> None:
        from agent_app.governance.policy_rollout_gate import (
            RolloutGateExecutionResult,
            RolloutGateExecutionStatus,
        )
        from agent_app.runtime.policy_rollout_gate_service import RolloutGateAutomationService

        recorder = AsyncMock()
        release_gate = AsyncMock()
        # Return SATISFIED requirement
        req = MagicMock()
        req.status = "SATISFIED"
        req.requirement_id = "rgr_1"
        req.gate_result_id = None
        req.simulation_id = None
        release_gate.check_requirement = AsyncMock(return_value=req)

        from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus
        req.status = ReleaseGateRequirementStatus.SATISFIED

        service = RolloutGateAutomationService(
            release_gate_automation_service=release_gate,
            history_recorder=recorder,
        )

        plan = _make_plan()
        step = _make_step(
            requires_simulation_gate=True,
            simulation_gate_mode=RolloutGateMode.AUTO,
        )
        ctx = _make_context()

        await service.ensure_step_gate(plan, step, ctx)

        recorder.record.assert_called_once()
        call_kwargs = recorder.record.call_args
        assert call_kwargs.kwargs["event_type"] == RolloutHistoryEventType.GATE_SATISFIED


# --- Expiration Service Tests ---

class TestExpirationServiceHistoryIntegration:
    """Tests that PolicyExpirationService records expiration history events."""

    async def test_expiration_service_records_approval_expired(self) -> None:
        recorder = AsyncMock()

        # Create a mock approval that will be expired
        approval = MagicMock()
        approval.approval_id = "rsa_expired"
        approval.rollout_id = "ro_test123"
        approval.step_id = "step_1"

        approval_store = AsyncMock()
        approval_store.expire_pending = AsyncMock(return_value=[approval])

        service = PolicyExpirationService(
            rollout_approval_store=approval_store,
            history_recorder=recorder,
        )

        await service.expire_rollout_approvals()

        recorder.record.assert_called_once()
        call_kwargs = recorder.record.call_args
        assert call_kwargs.kwargs["event_type"] == RolloutHistoryEventType.APPROVAL_EXPIRED
        assert call_kwargs.kwargs["rollout_id"] == "ro_test123"

    async def test_expiration_service_records_gate_expired(self) -> None:
        recorder = AsyncMock()

        # Create a mock gate requirement that will be expired
        req = MagicMock()
        req.requirement_id = "rgr_expired"
        req.source_type = "rollout_step"
        req.source_id = "ro_test123:step_1"
        req.max_age_seconds = 3600
        req.satisfied_at = None
        req.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        req.status = MagicMock()
        req.status.value = "REQUIRED"

        gate_store = AsyncMock()
        gate_store.list = AsyncMock(return_value=[req])
        gate_store.update = AsyncMock()

        # Use a time far in the future so the gate is expired
        from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus
        req.status = ReleaseGateRequirementStatus.REQUIRED

        service = PolicyExpirationService(
            release_gate_requirement_store=gate_store,
            history_recorder=recorder,
        )

        # Use a time far enough in the future
        future_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await service.expire_gate_requirements(now=future_time)

        recorder.record.assert_called_once()
        call_kwargs = recorder.record.call_args
        assert call_kwargs.kwargs["event_type"] == RolloutHistoryEventType.GATE_EXPIRED
        assert call_kwargs.kwargs["rollout_id"] == "ro_test123"


# --- Notification Service Tests ---

class TestNotificationServiceHistoryIntegration:
    """Tests that PolicyNotificationService records notification history events."""

    async def test_notification_service_records_rollout_notifications(self) -> None:
        from agent_app.governance.policy_notification import (
            PolicyNotificationRule,
            PolicyNotificationRuleStatus,
            PolicyNotificationSeverity,
            PolicyNotificationStatus,
        )

        recorder = AsyncMock()

        # Create a matching rule
        rule = PolicyNotificationRule(
            rule_id="pnr_test",
            name="Test Rule",
            event_types=["policy.rollout.step_succeeded"],
            source_types=[],
            channels=["log"],
            severity=PolicyNotificationSeverity.INFO,
            title_template="Rollout Update",
            body_template="{rollout_id}",
            status=PolicyNotificationRuleStatus.ENABLED,
            created_at=datetime.now(timezone.utc),
        )

        rule_store = AsyncMock()
        rule_store.list = AsyncMock(return_value=[rule])

        notif_store = AsyncMock()
        notif_store.create = AsyncMock()
        notif_store.update = AsyncMock()

        # Create a log channel that returns SENT
        log_channel = AsyncMock()
        log_result = MagicMock()
        log_result.status = PolicyNotificationStatus.SENT
        log_result.sent_at = datetime.now(timezone.utc)
        log_channel.send = AsyncMock(return_value=log_result)

        service = PolicyNotificationService(
            notification_store=notif_store,
            rule_store=rule_store,
            channels={"log": log_channel},
            history_recorder=recorder,
        )

        await service.notify_event(
            event_type="policy.rollout.step_succeeded",
            data={"rollout_id": "ro_test123", "step_id": "step_1"},
            source_type="rollout_step",
            source_id="ro_test123:step_1",
        )

        # Should have recorded NOTIFICATION_CREATED and NOTIFICATION_SENT
        assert recorder.record.call_count == 2
        event_types = [c.kwargs["event_type"] for c in recorder.record.call_args_list]
        assert RolloutHistoryEventType.NOTIFICATION_CREATED in event_types
        assert RolloutHistoryEventType.NOTIFICATION_SENT in event_types


# --- Export Helper Tests ---

class TestExportHelpers:
    """Tests for rollout history export helper functions."""

    def test_export_timeline_json(self) -> None:
        timeline = RolloutTimeline(
            rollout_id="ro_test",
            name="Test Rollout",
            status="COMPLETED",
        )
        result = rollout_timeline_to_json(timeline)
        assert isinstance(result, str)
        assert "ro_test" in result
        assert "COMPLETED" in result

    def test_export_analytics_json(self) -> None:
        report = RolloutAnalyticsReport(
            report_id="rar_test1",
            generated_at=datetime.now(timezone.utc),
            total_rollouts=10,
        )
        result = rollout_analytics_report_to_json(report)
        assert isinstance(result, str)
        assert "rar_test1" in result
        assert "10" in result

    def test_export_analytics_csv_rows(self) -> None:
        report = RolloutAnalyticsReport(
            report_id="rar_test2",
            generated_at=datetime.now(timezone.utc),
            total_rollouts=5,
            completed_rollouts=3,
            failed_rollouts=1,
            cancelled_rollouts=0,
            blocked_rollouts=1,
        )
        rows = rollout_analytics_report_to_csv_rows(report)
        assert isinstance(rows, list)
        assert len(rows) >= 3  # summary, gate_outcomes, approval_outcomes at minimum

        # Check summary row
        summary = rows[0]
        assert summary["section"] == "summary"
        assert summary["total_rollouts"] == 5
        assert summary["completed_rollouts"] == 3

        # Check gate outcomes
        gate = rows[1]
        assert gate["section"] == "gate_outcomes"

        # Check approval outcomes
        approval = rows[2]
        assert approval["section"] == "approval_outcomes"


# --- Fault Tolerance Test ---

class TestHistoryRecordingFaultTolerance:
    """Tests that history recording failures don't break service operations."""

    async def test_history_recording_failure_does_not_break_rollout(self) -> None:
        recorder = AsyncMock()
        recorder.record = AsyncMock(side_effect=RuntimeError("DB connection lost"))

        plan = _make_plan(status=RolloutPlanStatus.DRAFT)
        store = _make_mock_store(plan)
        service = RolloutService(
            rollout_store=store,
            release_service=_make_mock_release_service(),
            history_recorder=recorder,
        )
        ctx = _make_context()

        # Should NOT raise even though recorder fails
        result = await service.create_plan(
            name="test",
            bundle_id="b_1",
            steps=[_make_step()],
            created_by="creator",
            context=ctx,
        )
        assert result is not None


# --- Run all tests via pytest ---

@pytest.mark.asyncio
async def test_rollout_service_records_created() -> None:
    t = TestRolloutServiceHistoryIntegration()
    await t.test_rollout_service_records_created()


@pytest.mark.asyncio
async def test_rollout_service_records_started() -> None:
    t = TestRolloutServiceHistoryIntegration()
    await t.test_rollout_service_records_started()


@pytest.mark.asyncio
async def test_rollout_service_records_step_events() -> None:
    t = TestRolloutServiceHistoryIntegration()
    await t.test_rollout_service_records_step_events()


@pytest.mark.asyncio
async def test_rollout_service_no_recorder_works() -> None:
    t = TestRolloutServiceHistoryIntegration()
    await t.test_rollout_service_no_recorder_works()


@pytest.mark.asyncio
async def test_rollout_service_records_cancelled() -> None:
    t = TestRolloutServiceHistoryIntegration()
    await t.test_rollout_service_records_cancelled()


@pytest.mark.asyncio
async def test_rollout_service_records_approval_events() -> None:
    t = TestRolloutServiceHistoryIntegration()
    await t.test_rollout_service_records_approval_events()


@pytest.mark.asyncio
async def test_gate_service_records_gate_events() -> None:
    t = TestGateServiceHistoryIntegration()
    await t.test_gate_service_records_gate_events()


@pytest.mark.asyncio
async def test_expiration_service_records_approval_expired() -> None:
    t = TestExpirationServiceHistoryIntegration()
    await t.test_expiration_service_records_approval_expired()


@pytest.mark.asyncio
async def test_expiration_service_records_gate_expired() -> None:
    t = TestExpirationServiceHistoryIntegration()
    await t.test_expiration_service_records_gate_expired()


@pytest.mark.asyncio
async def test_notification_service_records_rollout_notifications() -> None:
    t = TestNotificationServiceHistoryIntegration()
    await t.test_notification_service_records_rollout_notifications()


def test_export_timeline_json() -> None:
    t = TestExportHelpers()
    t.test_export_timeline_json()


def test_export_analytics_json() -> None:
    t = TestExportHelpers()
    t.test_export_analytics_json()


def test_export_analytics_csv_rows() -> None:
    t = TestExportHelpers()
    t.test_export_analytics_csv_rows()


@pytest.mark.asyncio
async def test_history_recording_failure_does_not_break_rollout() -> None:
    t = TestHistoryRecordingFaultTolerance()
    await t.test_history_recording_failure_does_not_break_rollout()
