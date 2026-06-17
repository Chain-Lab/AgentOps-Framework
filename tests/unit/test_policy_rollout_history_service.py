"""Unit tests for RolloutHistoryService."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)
from agent_app.governance.policy_rollout_history import (
    RolloutHistoryEvent,
    RolloutHistoryEventType,
)
from agent_app.runtime.policy_rollout_history_service import RolloutHistoryService
from agent_app.runtime.policy_rollout_history_store import InMemoryRolloutHistoryStore


def _make_event(
    history_event_id: str = "rhe_001",
    rollout_id: str = "ro_001",
    event_type: RolloutHistoryEventType = RolloutHistoryEventType.ROLLOUT_CREATED,
    step_id: str | None = None,
    environment: str | None = None,
    ring_name: str | None = None,
    created_at: datetime | None = None,
    **kwargs,
) -> RolloutHistoryEvent:
    return RolloutHistoryEvent(
        history_event_id=history_event_id,
        rollout_id=rollout_id,
        event_type=event_type,
        step_id=step_id,
        environment=environment,
        ring_name=ring_name,
        created_at=created_at or datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        **kwargs,
    )


def _make_plan(
    rollout_id: str = "ro_001",
    name: str = "Test Rollout",
    bundle_id: str = "pb_001",
    status: RolloutPlanStatus = RolloutPlanStatus.ACTIVE,
) -> RolloutPlan:
    return RolloutPlan(
        rollout_id=rollout_id,
        name=name,
        bundle_id=bundle_id,
        status=status,
        steps=[
            RolloutStep(
                step_id="step_1",
                step_type=RolloutStepType.ACTIVATE,
                environment="prod",
                ring_name="canary",
                status=RolloutStepStatus.SUCCEEDED,
            ),
        ],
        created_by="user_test",
        created_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
    )


class _FakeRolloutStore:
    """Minimal fake rollout store for testing enrichment."""

    def __init__(self, plan: RolloutPlan | None = None):
        self._plan = plan

    async def get(self, rollout_id: str) -> RolloutPlan | None:
        if self._plan and self._plan.rollout_id == rollout_id:
            return self._plan
        return None


# ---------------------------------------------------------------------------
# get_timeline tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_timeline_empty_store() -> None:
    """Returns empty timeline when no events exist."""
    history_store = InMemoryRolloutHistoryStore()
    service = RolloutHistoryService(history_store=history_store)

    timeline = await service.get_timeline("ro_nonexistent")

    assert timeline.rollout_id == "ro_nonexistent"
    assert timeline.events == []
    assert timeline.steps == []
    assert timeline.name is None
    assert timeline.status is None


@pytest.mark.asyncio
async def test_get_timeline_no_store() -> None:
    """Returns empty timeline when history_store is None."""
    service = RolloutHistoryService(history_store=None)

    timeline = await service.get_timeline("ro_001")

    assert timeline.rollout_id == "ro_001"
    assert timeline.events == []
    assert timeline.steps == []


@pytest.mark.asyncio
async def test_get_timeline_with_events() -> None:
    """Builds timeline from history events."""
    history_store = InMemoryRolloutHistoryStore()
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    await history_store.append(_make_event(
        history_event_id="rhe_001",
        rollout_id="ro_001",
        event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
        created_at=t0,
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_002",
        rollout_id="ro_001",
        event_type=RolloutHistoryEventType.STEP_STARTED,
        step_id="step_1",
        created_at=t1,
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_003",
        rollout_id="ro_001",
        event_type=RolloutHistoryEventType.STEP_SUCCEEDED,
        step_id="step_1",
        created_at=t2,
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_004",
        rollout_id="ro_001",
        event_type=RolloutHistoryEventType.ROLLOUT_COMPLETED,
        created_at=t2,
    ))

    service = RolloutHistoryService(history_store=history_store)
    timeline = await service.get_timeline("ro_001")

    assert timeline.started_at == t0
    assert timeline.completed_at == t2
    assert timeline.duration_seconds == 7200.0
    assert len(timeline.steps) == 1
    step = timeline.steps[0]
    assert step.step_id == "step_1"
    assert step.status == "succeeded"
    assert step.started_at == t1
    assert step.completed_at == t2
    assert step.duration_seconds == 3600.0


@pytest.mark.asyncio
async def test_get_timeline_enriched_from_rollout_store() -> None:
    """Enriches timeline from rollout plan data."""
    history_store = InMemoryRolloutHistoryStore()
    plan = _make_plan(rollout_id="ro_001", name="My Rollout", bundle_id="pb_999")

    await history_store.append(_make_event(
        history_event_id="rhe_001",
        rollout_id="ro_001",
        event_type=RolloutHistoryEventType.ROLLOUT_CREATED,
        created_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_002",
        rollout_id="ro_001",
        event_type=RolloutHistoryEventType.STEP_STARTED,
        step_id="step_1",
        created_at=datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
    ))

    rollout_store = _FakeRolloutStore(plan)
    service = RolloutHistoryService(
        history_store=history_store,
        rollout_store=rollout_store,
    )
    timeline = await service.get_timeline("ro_001")

    assert timeline.name == "My Rollout"
    assert timeline.bundle_id == "pb_999"
    assert timeline.status == "active"
    assert timeline.created_at == plan.created_at

    # Step enrichment
    step = timeline.steps[0]
    assert step.step_id == "step_1"
    assert step.step_type == RolloutStepType.ACTIVATE
    assert step.environment == "prod"
    assert step.ring_name == "canary"


# ---------------------------------------------------------------------------
# generate_report tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_report_empty() -> None:
    """Returns empty report when no events exist."""
    history_store = InMemoryRolloutHistoryStore()
    service = RolloutHistoryService(history_store=history_store)

    report = await service.generate_report()

    assert report.total_rollouts == 0
    assert report.completed_rollouts == 0
    assert report.failed_rollouts == 0
    assert report.cancelled_rollouts == 0
    assert report.blocked_rollouts == 0
    assert report.gate_outcomes.total == 0
    assert report.approval_outcomes.total == 0


@pytest.mark.asyncio
async def test_generate_report_counts() -> None:
    """Counts completed/failed/cancelled/blocked rollouts."""
    history_store = InMemoryRolloutHistoryStore()
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    # Completed rollout
    await history_store.append(_make_event(
        history_event_id="rhe_001", rollout_id="ro_completed",
        event_type=RolloutHistoryEventType.ROLLOUT_COMPLETED, created_at=base,
    ))
    # Failed rollout
    await history_store.append(_make_event(
        history_event_id="rhe_002", rollout_id="ro_failed",
        event_type=RolloutHistoryEventType.ROLLOUT_FAILED, created_at=base,
    ))
    # Cancelled rollout
    await history_store.append(_make_event(
        history_event_id="rhe_003", rollout_id="ro_cancelled",
        event_type=RolloutHistoryEventType.ROLLOUT_CANCELLED, created_at=base,
    ))
    # Blocked rollout (has STEP_BLOCKED but no completion/failure)
    await history_store.append(_make_event(
        history_event_id="rhe_004", rollout_id="ro_blocked",
        event_type=RolloutHistoryEventType.STEP_BLOCKED,
        step_id="step_1", created_at=base,
    ))

    service = RolloutHistoryService(history_store=history_store)
    report = await service.generate_report()

    assert report.total_rollouts == 4
    assert report.completed_rollouts == 1
    assert report.failed_rollouts == 1
    assert report.cancelled_rollouts == 1
    assert report.blocked_rollouts == 1


@pytest.mark.asyncio
async def test_generate_report_gate_outcomes() -> None:
    """Gate outcome summary is computed correctly."""
    history_store = InMemoryRolloutHistoryStore()
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    await history_store.append(_make_event(
        history_event_id="rhe_001", rollout_id="ro_001",
        event_type=RolloutHistoryEventType.GATE_SATISFIED,
        step_id="step_1", created_at=base,
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_002", rollout_id="ro_001",
        event_type=RolloutHistoryEventType.GATE_BLOCKED,
        step_id="step_2", created_at=base,
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_003", rollout_id="ro_002",
        event_type=RolloutHistoryEventType.GATE_FAILED,
        step_id="step_1", created_at=base,
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_004", rollout_id="ro_002",
        event_type=RolloutHistoryEventType.GATE_SKIPPED,
        step_id="step_2", created_at=base,
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_005", rollout_id="ro_003",
        event_type=RolloutHistoryEventType.GATE_EXPIRED,
        step_id="step_1", created_at=base,
    ))

    service = RolloutHistoryService(history_store=history_store)
    report = await service.generate_report()

    assert report.gate_outcomes.total == 5
    assert report.gate_outcomes.satisfied == 1
    assert report.gate_outcomes.blocked == 1
    assert report.gate_outcomes.failed == 1
    assert report.gate_outcomes.skipped == 1
    assert report.gate_outcomes.expired == 1


@pytest.mark.asyncio
async def test_generate_report_approval_latency() -> None:
    """Approval latency summary is computed correctly."""
    history_store = InMemoryRolloutHistoryStore()
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    await history_store.append(_make_event(
        history_event_id="rhe_001", rollout_id="ro_001",
        event_type=RolloutHistoryEventType.APPROVAL_REQUESTED,
        step_id="step_1", created_at=base,
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_002", rollout_id="ro_001",
        event_type=RolloutHistoryEventType.APPROVAL_APPROVED,
        step_id="step_1", created_at=base + timedelta(hours=2),
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_003", rollout_id="ro_002",
        event_type=RolloutHistoryEventType.APPROVAL_REQUESTED,
        step_id="step_1", created_at=base,
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_004", rollout_id="ro_002",
        event_type=RolloutHistoryEventType.APPROVAL_REJECTED,
        step_id="step_1", created_at=base + timedelta(hours=1),
    ))

    service = RolloutHistoryService(history_store=history_store)
    report = await service.generate_report()

    assert report.approval_outcomes.total == 4
    assert report.approval_outcomes.pending == 2
    assert report.approval_outcomes.approved == 1
    assert report.approval_outcomes.rejected == 1
    assert report.approval_outcomes.average_latency_seconds == 7200.0


@pytest.mark.asyncio
async def test_generate_report_missing_stores() -> None:
    """Partial report with no optional stores — history_store only."""
    history_store = InMemoryRolloutHistoryStore()
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    await history_store.append(_make_event(
        history_event_id="rhe_001", rollout_id="ro_001",
        event_type=RolloutHistoryEventType.ROLLOUT_COMPLETED,
        environment="staging", ring_name="canary",
        created_at=base,
    ))

    service = RolloutHistoryService(history_store=history_store)
    report = await service.generate_report()

    assert report.total_rollouts == 1
    assert report.completed_rollouts == 1
    assert len(report.environment_summary) == 1
    assert report.environment_summary[0]["environment"] == "staging"
    assert len(report.ring_summary) == 1
    assert report.ring_summary[0]["ring_name"] == "canary"


# ---------------------------------------------------------------------------
# list_history_events tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_history_events() -> None:
    """Delegates to store.list() with filters."""
    history_store = InMemoryRolloutHistoryStore()
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    await history_store.append(_make_event(
        history_event_id="rhe_001", rollout_id="ro_001",
        event_type=RolloutHistoryEventType.ROLLOUT_CREATED,
        step_id="step_1", created_at=base,
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_002", rollout_id="ro_001",
        event_type=RolloutHistoryEventType.STEP_STARTED,
        step_id="step_1", created_at=base,
    ))
    await history_store.append(_make_event(
        history_event_id="rhe_003", rollout_id="ro_002",
        event_type=RolloutHistoryEventType.ROLLOUT_CREATED,
        created_at=base,
    ))

    service = RolloutHistoryService(history_store=history_store)

    # Filter by rollout_id
    result = await service.list_history_events(rollout_id="ro_001")
    assert len(result) == 2

    # Filter by event_type
    result = await service.list_history_events(event_type=RolloutHistoryEventType.ROLLOUT_CREATED)
    assert len(result) == 2

    # Filter by step_id
    result = await service.list_history_events(step_id="step_1")
    assert len(result) == 2

    # No store returns empty list
    service_no_store = RolloutHistoryService(history_store=None)
    result = await service_no_store.list_history_events()
    assert result == []
