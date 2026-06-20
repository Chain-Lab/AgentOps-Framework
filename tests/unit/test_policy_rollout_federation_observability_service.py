"""Tests for FederationObservabilityService — timeline reconstruction and analytics."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEventType,
    FederationHistoryEvent,
    FederationTimeline,
    FederationTargetTimeline,
    FederationWaveTimeline,
    FederationAnalyticsReport,
    FederationTargetHealthSummary,
    FederationWaveOutcomeSummary,
    FederationConflictSummary,
)
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederationExecutionStrategy,
    FederatedRolloutWave,
    FederatedRolloutTargetExecution,
    FederatedRolloutTargetExecutionStatus,
    FederatedRolloutTarget,
    FederatedTargetStatus,
)
from agent_app.governance.policy_rollout import RolloutStep, RolloutStepStatus
from agent_app.runtime.policy_rollout_federation_history_store import (
    InMemoryFederationHistoryStore,
)
from agent_app.runtime.policy_rollout_federation_store import (
    InMemoryFederatedRolloutPlanStore,
)
from agent_app.runtime.policy_rollout_federation_observability_service import (
    FederationObservabilityService,
)
from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalDashboardSummary,
    FederationApprovalRequest,
    FederationApprovalStatus,
)
from agent_app.runtime.policy_rollout_federation_approval_store import (
    InMemoryFederationApprovalStore,
)
from agent_app.runtime.policy_compliance_export import (
    export_federation_approval_summary_json,
    export_federation_approval_summary_csv,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now(offset_seconds: float = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)


def _make_event(
    event_type: FederationHistoryEventType,
    federation_id: str = "frp_test123",
    target_id: str | None = None,
    rollout_id: str | None = None,
    wave_id: str | None = None,
    tenant_id: str | None = None,
    environment: str | None = None,
    ring_name: str | None = None,
    region: str | None = None,
    metadata: dict | None = None,
    created_at: datetime | None = None,
) -> FederationHistoryEvent:
    return FederationHistoryEvent(
        history_event_id=f"fhe_{event_type.value.replace('.', '_')}_{id(event_type)}_{_now().timestamp()}",
        federation_id=federation_id,
        target_id=target_id,
        rollout_id=rollout_id,
        wave_id=wave_id,
        event_type=event_type,
        tenant_id=tenant_id,
        environment=environment,
        ring_name=ring_name,
        region=region,
        metadata=metadata or {},
        created_at=created_at or _now(),
    )


def _make_plan(
    federation_id: str = "frp_test123",
    name: str = "Test Federation",
    strategy: FederationExecutionStrategy = FederationExecutionStrategy.SEQUENTIAL,
    status: FederatedRolloutPlanStatus = FederatedRolloutPlanStatus.ACTIVE,
) -> FederatedRolloutPlan:
    return FederatedRolloutPlan(
        federation_id=federation_id,
        name=name,
        bundle_id="pb_test",
        strategy=strategy,
        status=status,
        target_ids=["frt_target1"],
        waves=[
            FederatedRolloutWave(
                wave_id="frw_wave1",
                name="Wave 1",
                target_ids=["frt_target1"],
            ),
        ],
        executions=[
            FederatedRolloutTargetExecution(
                execution_id="fre_exec1",
                target_id="frt_target1",
                status=FederatedRolloutTargetExecutionStatus.PENDING,
            ),
        ],
        rollout_template_steps=[
            RolloutStep(
                step_id="step_1",
                step_type="activate",
                environment="staging",
                status=RolloutStepStatus.PENDING,
            ),
        ],
        created_by="test_user",
        reason="test",
        created_at=_now(),
        updated_at=_now(),
    )


# ===========================================================================
# TestFederationObservabilityTimeline
# ===========================================================================


class TestFederationObservabilityTimeline:
    """Tests for FederationObservabilityService.get_timeline()."""

    @pytest.mark.asyncio
    async def test_timeline_from_history_events(self) -> None:
        """3 events produce a timeline with 3 events entries."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        t0 = _now(0)
        t1 = _now(10)
        t2 = _now(20)
        await store.append(_make_event(FederationHistoryEventType.FEDERATION_STARTED, created_at=t0))
        await store.append(_make_event(FederationHistoryEventType.FEDERATION_COMPLETED, created_at=t1))
        await store.append(_make_event(FederationHistoryEventType.FEDERATION_CREATED, created_at=t2))

        timeline = await service.get_timeline("frp_test123")
        assert isinstance(timeline, FederationTimeline)
        assert timeline.federation_id == "frp_test123"
        assert len(timeline.events) == 3

    @pytest.mark.asyncio
    async def test_timeline_includes_target_executions(self) -> None:
        """TARGET_EXECUTION_STARTED + SUCCEEDED produce a target timeline entry."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        t0 = _now(0)
        t1 = _now(5)
        await store.append(_make_event(
            FederationHistoryEventType.TARGET_EXECUTION_STARTED,
            target_id="frt_target1",
            created_at=t0,
        ))
        await store.append(_make_event(
            FederationHistoryEventType.TARGET_EXECUTION_SUCCEEDED,
            target_id="frt_target1",
            created_at=t1,
        ))

        timeline = await service.get_timeline("frp_test123")
        assert len(timeline.targets) >= 1
        target_tl = timeline.targets[0]
        assert isinstance(target_tl, FederationTargetTimeline)
        assert target_tl.target_id == "frt_target1"
        assert target_tl.status == "succeeded"
        assert target_tl.started_at is not None
        assert target_tl.completed_at is not None
        assert target_tl.duration_seconds is not None
        assert target_tl.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_timeline_includes_waves(self) -> None:
        """WAVE_STARTED + SUCCEEDED produce a wave timeline entry."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        t0 = _now(0)
        t1 = _now(10)
        await store.append(_make_event(
            FederationHistoryEventType.WAVE_STARTED,
            wave_id="frw_wave1",
            created_at=t0,
        ))
        await store.append(_make_event(
            FederationHistoryEventType.WAVE_SUCCEEDED,
            wave_id="frw_wave1",
            created_at=t1,
        ))

        timeline = await service.get_timeline("frp_test123")
        assert len(timeline.waves) >= 1
        wave_tl = timeline.waves[0]
        assert isinstance(wave_tl, FederationWaveTimeline)
        assert wave_tl.wave_id == "frw_wave1"
        assert wave_tl.status == "succeeded"
        assert wave_tl.started_at is not None
        assert wave_tl.completed_at is not None

    @pytest.mark.asyncio
    async def test_timeline_enriched_from_plan_store(self) -> None:
        """Timeline name/strategy set when plan found in plan store."""
        history_store = InMemoryFederationHistoryStore()
        plan_store = InMemoryFederatedRolloutPlanStore()
        service = FederationObservabilityService(
            history_store=history_store,
            federation_plan_store=plan_store,
        )

        plan = _make_plan(
            federation_id="frp_test123",
            name="My Federation",
            strategy=FederationExecutionStrategy.WAVE,
        )
        await plan_store.create(plan)

        await history_store.append(_make_event(
            FederationHistoryEventType.FEDERATION_STARTED,
        ))

        timeline = await service.get_timeline("frp_test123")
        assert timeline.name == "My Federation"
        assert timeline.strategy == "wave"


# ===========================================================================
# TestFederationObservabilityReport
# ===========================================================================


class TestFederationObservabilityReport:
    """Tests for FederationObservabilityService.generate_report()."""

    @pytest.mark.asyncio
    async def test_report_empty_sources(self) -> None:
        """No events produce empty summaries."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        report = await service.generate_report()
        assert isinstance(report, FederationAnalyticsReport)
        assert report.total_federations == 0
        assert report.target_health.total_targets == 0
        assert report.wave_outcomes.total_waves == 0
        assert report.conflicts.total_conflicts == 0

    @pytest.mark.asyncio
    async def test_report_counts_completed_failed(self) -> None:
        """2 federations, 1 completed + 1 failed counted correctly."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        # Federation 1 — completed
        await store.append(_make_event(
            FederationHistoryEventType.FEDERATION_STARTED,
            federation_id="frp_fed1",
        ))
        await store.append(_make_event(
            FederationHistoryEventType.FEDERATION_COMPLETED,
            federation_id="frp_fed1",
        ))

        # Federation 2 — failed
        await store.append(_make_event(
            FederationHistoryEventType.FEDERATION_STARTED,
            federation_id="frp_fed2",
        ))
        await store.append(_make_event(
            FederationHistoryEventType.FEDERATION_FAILED,
            federation_id="frp_fed2",
        ))

        report = await service.generate_report()
        assert report.total_federations == 2
        assert report.completed_federations == 1
        assert report.failed_federations == 1

    @pytest.mark.asyncio
    async def test_report_target_health(self) -> None:
        """Target execution events produce correct health counts."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        # 2 succeeded, 1 failed, 1 blocked
        for i in range(2):
            await store.append(_make_event(
                FederationHistoryEventType.TARGET_EXECUTION_STARTED,
                target_id=f"frt_s{i}",
                federation_id="frp_f1",
            ))
            await store.append(_make_event(
                FederationHistoryEventType.TARGET_EXECUTION_SUCCEEDED,
                target_id=f"frt_s{i}",
                federation_id="frp_f1",
            ))

        await store.append(_make_event(
            FederationHistoryEventType.TARGET_EXECUTION_STARTED,
            target_id="frt_fail",
            federation_id="frp_f1",
        ))
        await store.append(_make_event(
            FederationHistoryEventType.TARGET_EXECUTION_FAILED,
            target_id="frt_fail",
            federation_id="frp_f1",
        ))

        await store.append(_make_event(
            FederationHistoryEventType.TARGET_EXECUTION_STARTED,
            target_id="frt_block",
            federation_id="frp_f1",
        ))
        await store.append(_make_event(
            FederationHistoryEventType.TARGET_EXECUTION_BLOCKED,
            target_id="frt_block",
            federation_id="frp_f1",
        ))

        report = await service.generate_report()
        assert report.target_health.succeeded_targets == 2
        assert report.target_health.failed_targets == 1
        assert report.target_health.blocked_targets == 1

    @pytest.mark.asyncio
    async def test_report_wave_outcomes(self) -> None:
        """Wave events produce correct wave outcome counts."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        # 1 succeeded wave, 1 failed wave
        await store.append(_make_event(
            FederationHistoryEventType.WAVE_STARTED,
            wave_id="frw_w1",
            federation_id="frp_f1",
        ))
        await store.append(_make_event(
            FederationHistoryEventType.WAVE_SUCCEEDED,
            wave_id="frw_w1",
            federation_id="frp_f1",
        ))

        await store.append(_make_event(
            FederationHistoryEventType.WAVE_STARTED,
            wave_id="frw_w2",
            federation_id="frp_f1",
        ))
        await store.append(_make_event(
            FederationHistoryEventType.WAVE_FAILED,
            wave_id="frw_w2",
            federation_id="frp_f1",
        ))

        report = await service.generate_report()
        assert report.wave_outcomes.total_waves == 2
        assert report.wave_outcomes.succeeded_waves == 1
        assert report.wave_outcomes.failed_waves == 1

    @pytest.mark.asyncio
    async def test_report_conflict_summary(self) -> None:
        """CONFLICT_DETECTED events produce conflict counts."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        # 2 error conflicts, 1 warning conflict
        await store.append(_make_event(
            FederationHistoryEventType.CONFLICT_DETECTED,
            federation_id="frp_f1",
            metadata={"severity": "error"},
        ))
        await store.append(_make_event(
            FederationHistoryEventType.CONFLICT_DETECTED,
            federation_id="frp_f1",
            metadata={"severity": "error"},
        ))
        await store.append(_make_event(
            FederationHistoryEventType.CONFLICT_DETECTED,
            federation_id="frp_f1",
            metadata={"severity": "warning"},
        ))

        report = await service.generate_report()
        assert report.conflicts.total_conflicts == 3
        assert report.conflicts.error_conflicts == 2
        assert report.conflicts.warning_conflicts == 1

    @pytest.mark.asyncio
    async def test_report_environment_summary(self) -> None:
        """Events with environment produce environment summary."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        await store.append(_make_event(
            FederationHistoryEventType.TARGET_EXECUTION_STARTED,
            federation_id="frp_f1",
            environment="staging",
        ))
        await store.append(_make_event(
            FederationHistoryEventType.TARGET_EXECUTION_STARTED,
            federation_id="frp_f1",
            environment="staging",
        ))
        await store.append(_make_event(
            FederationHistoryEventType.TARGET_EXECUTION_STARTED,
            federation_id="frp_f1",
            environment="production",
        ))

        report = await service.generate_report()
        assert len(report.environment_summary) >= 2
        # staging should have count 2, production count 1
        staging_entry = next(
            (e for e in report.environment_summary if e["environment"] == "staging"), None
        )
        prod_entry = next(
            (e for e in report.environment_summary if e["environment"] == "production"), None
        )
        assert staging_entry is not None
        assert staging_entry["event_count"] == 2
        assert prod_entry is not None
        assert prod_entry["event_count"] == 1

    @pytest.mark.asyncio
    async def test_missing_optional_stores_produce_partial_report(self) -> None:
        """Only history_store provided — report generates without error."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        await store.append(_make_event(
            FederationHistoryEventType.FEDERATION_STARTED,
            federation_id="frp_f1",
        ))
        await store.append(_make_event(
            FederationHistoryEventType.FEDERATION_COMPLETED,
            federation_id="frp_f1",
        ))

        report = await service.generate_report()
        assert report.total_federations == 1
        assert report.completed_federations == 1


# ===========================================================================
# TestFederationObservabilityListEvents
# ===========================================================================


class TestFederationObservabilityListEvents:
    """Tests for FederationObservabilityService.list_history_events()."""

    @pytest.mark.asyncio
    async def test_list_events(self) -> None:
        """list_history_events with federation_id filter returns matching events."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        await store.append(_make_event(
            FederationHistoryEventType.FEDERATION_STARTED,
            federation_id="frp_f1",
        ))
        await store.append(_make_event(
            FederationHistoryEventType.FEDERATION_COMPLETED,
            federation_id="frp_f1",
        ))
        await store.append(_make_event(
            FederationHistoryEventType.FEDERATION_STARTED,
            federation_id="frp_f2",
        ))

        events = await service.list_history_events(federation_id="frp_f1")
        assert len(events) == 2
        assert all(e.federation_id == "frp_f1" for e in events)


# ===========================================================================
# TestFederationObservabilityApprovalSummary
# ===========================================================================


class TestFederationObservabilityApprovalSummary:
    """Tests for FederationObservabilityService approval summary integration."""

    @pytest.mark.asyncio
    async def test_get_approval_summary_returns_empty_when_no_store(self) -> None:
        """get_approval_summary returns empty FederationApprovalDashboardSummary when no store."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        summary = await service.get_approval_summary()
        assert isinstance(summary, FederationApprovalDashboardSummary)
        assert summary.total_pending == 0
        assert summary.total_approved == 0
        assert summary.total_rejected == 0
        assert summary.average_approval_latency_seconds is None
        assert summary.by_tenant == {}
        assert summary.by_action == {}
        assert summary.blocked_federation_actions == 0

    @pytest.mark.asyncio
    async def test_get_approval_summary_returns_empty_with_tenant_filter(self) -> None:
        """get_approval_summary with tenant_id returns empty when no store."""
        store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=store)

        summary = await service.get_approval_summary(tenant_id="tenant_1")
        assert isinstance(summary, FederationApprovalDashboardSummary)
        assert summary.total_pending == 0

    @pytest.mark.asyncio
    async def test_get_approval_summary_returns_populated_summary(self) -> None:
        """get_approval_summary returns populated data from approval store."""
        history_store = InMemoryFederationHistoryStore()
        approval_store = InMemoryFederationApprovalStore()
        service = FederationObservabilityService(
            history_store=history_store,
            approval_store=approval_store,
        )

        # Create approval requests
        req1 = FederationApprovalRequest(
            approval_id="fap_001",
            federation_id="frp_f1",
            action="federation.plan.start",
            requested_by="user1",
            tenant_id="tenant_a",
            required_approvers=["approver1"],
            created_at=_now(),
        )
        req2 = FederationApprovalRequest(
            approval_id="fap_002",
            federation_id="frp_f2",
            action="federation.target.execute",
            requested_by="user2",
            tenant_id="tenant_b",
            required_approvers=["approver2"],
            created_at=_now(-60),
        )
        await approval_store.create(req1)
        await approval_store.create(req2)

        # Approve one
        await approval_store.approve("fap_002", "approver2")

        summary = await service.get_approval_summary()
        assert summary.total_pending == 1
        assert summary.total_approved == 1
        assert summary.average_approval_latency_seconds is not None
        assert summary.average_approval_latency_seconds > 0

    @pytest.mark.asyncio
    async def test_get_approval_summary_with_tenant_filter(self) -> None:
        """get_approval_summary with tenant_id filters to that tenant."""
        history_store = InMemoryFederationHistoryStore()
        approval_store = InMemoryFederationApprovalStore()
        service = FederationObservabilityService(
            history_store=history_store,
            approval_store=approval_store,
        )

        req1 = FederationApprovalRequest(
            approval_id="fap_001",
            federation_id="frp_f1",
            action="federation.plan.start",
            requested_by="user1",
            tenant_id="tenant_a",
            required_approvers=["approver1"],
            created_at=_now(),
        )
        req2 = FederationApprovalRequest(
            approval_id="fap_002",
            federation_id="frp_f2",
            action="federation.target.execute",
            requested_by="user2",
            tenant_id="tenant_b",
            required_approvers=["approver2"],
            created_at=_now(),
        )
        await approval_store.create(req1)
        await approval_store.create(req2)

        summary = await service.get_approval_summary(tenant_id="tenant_a")
        assert summary.total_pending == 1
        assert "tenant_a" in summary.by_tenant

    @pytest.mark.asyncio
    async def test_generate_report_includes_approval_metadata_when_store_present(self) -> None:
        """generate_report includes approval summary metadata when approval_store is configured."""
        history_store = InMemoryFederationHistoryStore()
        approval_store = InMemoryFederationApprovalStore()
        service = FederationObservabilityService(
            history_store=history_store,
            approval_store=approval_store,
        )

        # Add a history event so report is non-empty
        await history_store.append(_make_event(
            FederationHistoryEventType.FEDERATION_STARTED,
            federation_id="frp_f1",
        ))

        # Add an approval request
        req = FederationApprovalRequest(
            approval_id="fap_001",
            federation_id="frp_f1",
            action="federation.plan.start",
            requested_by="user1",
            tenant_id="tenant_a",
            required_approvers=["approver1"],
            created_at=_now(),
        )
        await approval_store.create(req)

        report = await service.generate_report()
        assert report.metadata["approvals_pending_count"] == 1
        assert report.metadata["approvals_approved_count"] == 0
        assert report.metadata["approvals_rejected_count"] == 0
        assert report.metadata["escalated_approvals_count"] == 0
        assert report.metadata["blocked_federation_actions_count"] == 1

    @pytest.mark.asyncio
    async def test_generate_report_has_zero_approval_metadata_when_no_store(self) -> None:
        """generate_report has zero/default approval metadata when no approval_store."""
        history_store = InMemoryFederationHistoryStore()
        service = FederationObservabilityService(history_store=history_store)

        await history_store.append(_make_event(
            FederationHistoryEventType.FEDERATION_STARTED,
            federation_id="frp_f1",
        ))

        report = await service.generate_report()
        assert report.metadata["approvals_pending_count"] == 0
        assert report.metadata["approvals_approved_count"] == 0
        assert report.metadata["approvals_rejected_count"] == 0
        assert report.metadata["average_approval_latency_seconds"] is None
        assert report.metadata["approvals_by_tenant"] == {}
        assert report.metadata["approvals_by_target"] == {}
        assert report.metadata["escalated_approvals_count"] == 0
        assert report.metadata["blocked_federation_actions_count"] == 0


# ===========================================================================
# TestFederationApprovalExportHelpers
# ===========================================================================


class TestFederationApprovalExportHelpers:
    """Tests for federation approval summary export helpers."""

    def test_export_federation_approval_summary_json(self) -> None:
        """export_federation_approval_summary_json returns valid JSON string."""
        summary = FederationApprovalDashboardSummary(
            total_pending=2,
            total_approved=5,
            total_rejected=1,
            total_expired=0,
            total_escalated=1,
            total_cancelled=0,
            average_approval_latency_seconds=42.5,
            by_tenant={"tenant_a": 2},
            by_action={"federation.plan.start": 2},
            blocked_federation_actions=2,
        )
        result = export_federation_approval_summary_json(summary)
        assert isinstance(result, str)
        assert "total_pending" in result
        assert "total_approved" in result
        assert "42.5" in result

    def test_export_federation_approval_summary_csv(self) -> None:
        """export_federation_approval_summary_csv returns flat rows."""
        summary = FederationApprovalDashboardSummary(
            total_pending=2,
            total_approved=5,
            total_rejected=1,
            total_expired=0,
            total_escalated=1,
            total_cancelled=0,
            average_approval_latency_seconds=42.5,
            by_tenant={"tenant_a": 2},
            by_action={"federation.plan.start": 2},
            blocked_federation_actions=2,
        )
        rows = export_federation_approval_summary_csv(summary)
        assert len(rows) >= 1

        totals_row = next(r for r in rows if r["section"] == "totals")
        assert totals_row["total_pending"] == 2
        assert totals_row["total_approved"] == 5
        assert totals_row["average_approval_latency_seconds"] == 42.5

        tenant_rows = [r for r in rows if r["section"] == "by_tenant"]
        assert len(tenant_rows) == 1
        assert tenant_rows[0]["tenant_id"] == "tenant_a"

        action_rows = [r for r in rows if r["section"] == "by_action"]
        assert len(action_rows) == 1
        assert action_rows[0]["action"] == "federation.plan.start"

    def test_export_federation_approval_summary_json_empty(self) -> None:
        """export_federation_approval_summary_json works with empty summary."""
        summary = FederationApprovalDashboardSummary()
        result = export_federation_approval_summary_json(summary)
        assert isinstance(result, str)
        assert "total_pending" in result

    def test_export_federation_approval_summary_csv_empty(self) -> None:
        """export_federation_approval_summary_csv works with empty summary."""
        summary = FederationApprovalDashboardSummary()
        rows = export_federation_approval_summary_csv(summary)
        assert len(rows) >= 1
        totals_row = next(r for r in rows if r["section"] == "totals")
        assert totals_row["total_pending"] == 0
