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
