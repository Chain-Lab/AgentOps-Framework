# Phase 47: Policy Rollout Federation Observability and Reporting

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make federated rollout execution explainable and measurable by adding federation history events, timeline reconstruction, analytics reports, and export helpers.

**Architecture:** Phase 47 mirrors the Phase 45 rollout-history pattern but scoped to federation. A `FederationHistoryRecorder` captures normalized events whenever the `RolloutFederationService` or `PolicyNotificationService` performs federation-related actions. A `FederationObservabilityService` reconstructs timelines and generates analytics from those events, enriched from existing federation plan/target stores and rollout history. CLI and console pages expose history, timeline, and analytics to operators.

**Tech Stack:** Python 3.10+, Pydantic v2, pytest, SQLite (optional), FastAPI/Jinja2 (optional, for console)

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `agent_app/governance/policy_rollout_federation_history.py` | FederationHistoryEventType, FederationHistoryEvent, FederationTargetTimeline, FederationWaveTimeline, FederationTimeline, FederationTargetHealthSummary, FederationWaveOutcomeSummary, FederationConflictSummary, FederationAnalyticsReport |
| `agent_app/runtime/policy_rollout_federation_history_store.py` | FederationHistoryStore Protocol, InMemoryFederationHistoryStore, SQLiteFederationHistoryStore, create_federation_history_store() |
| `agent_app/runtime/policy_rollout_federation_history_recorder.py` | FederationHistoryRecorder — creates/append events, optional audit |
| `agent_app/runtime/policy_rollout_federation_observability_service.py` | FederationObservabilityService — timeline, report, list_history_events |
| `tests/unit/test_policy_rollout_federation_history_model.py` | Model validation tests |
| `tests/unit/test_policy_rollout_federation_history_store.py` | Store tests |
| `tests/unit/test_policy_rollout_federation_history_recorder.py` | Recorder tests |
| `tests/unit/test_policy_rollout_federation_observability_service.py` | Observability service tests |
| `tests/unit/test_policy_rollout_federation_history_config.py` | Config/loader/RBAC/events/AgentApp tests |
| `tests/unit/test_policy_rollout_federation_history_cli.py` | CLI command tests |
| `tests/unit/test_policy_rollout_federation_history_console.py` | Console page tests |
| `agent_app/console/templates/policy_federation_history.html` | History events page |
| `agent_app/console/templates/policy_federation_timeline.html` | Timeline page |
| `agent_app/console/templates/policy_federation_analytics.html` | Analytics page |
| `docs/release_checklist_phase47.md` | Release checklist |

### Modified files

| File | Change |
|------|--------|
| `agent_app/runtime/policy_rollout_federation_service.py` | Add optional recorder, record events on target/plan/execution changes |
| `agent_app/runtime/policy_notification_service.py` | Add optional federation recorder, record events when federation_id in metadata |
| `agent_app/runtime/policy_compliance_export.py` | Add federation_timeline_to_json, federation_analytics_report_to_json, federation_analytics_report_to_csv_rows |
| `agent_app/governance/policy_rbac.py` | Add FEDERATION_HISTORY_VIEW, FEDERATION_ANALYTICS_VIEW, FEDERATION_ANALYTICS_EXPORT |
| `agent_app/governance/policy_change_event.py` | Add 7 federation history change events (81 → 88) |
| `agent_app/config/schema.py` | Add RolloutFederationHistoryConfig |
| `agent_app/config/loader.py` | Wire federation history store/recorder/service |
| `agent_app/core/app.py` | Add federation_history_store, federation_history_recorder, federation_observability_service properties |
| `agent_app/cli.py` | Add federation history/timeline/analytics/export commands |
| `agent_app/console/router.py` | Add federation history/timeline/analytics routes |
| `agent_app/adapters/fastapi.py` | Pass new services to console router |
| `docs/policy_release.md` | Phase 47 section |
| `CHANGELOG.md` | v0.35.0 entry |
| `README.md` | Phase 47 in roadmap |
| `tests/unit/test_policy_change_event.py` | 81 → 88 |
| `tests/unit/test_policy_rollout_gate_config.py` | 81 → 88 |
| `tests/unit/test_policy_notification_config.py` | 81 → 88 |
| `tests/unit/test_policy_rollout_history_config.py` | 81 → 88 |

---

### Task 1: Federation history, timeline, and analytics models

**Files:**
- Create: `agent_app/governance/policy_rollout_federation_history.py`
- Test: `tests/unit/test_policy_rollout_federation_history_model.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Phase 47 Task 1 tests — federation history, timeline, and analytics models."""

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEventType,
    FederationHistoryEvent,
    FederationTargetTimeline,
    FederationWaveTimeline,
    FederationTimeline,
    FederationTargetHealthSummary,
    FederationWaveOutcomeSummary,
    FederationConflictSummary,
    FederationAnalyticsReport,
)


class TestFederationHistoryEventType:
    """Tests for FederationHistoryEventType enum."""

    def test_event_types_exist(self) -> None:
        """All 22 federation history event types exist."""
        expected = [
            "FEDERATION_CREATED", "FEDERATION_STARTED", "FEDERATION_COMPLETED",
            "FEDERATION_FAILED", "FEDERATION_CANCELLED", "FEDERATION_BLOCKED",
            "TARGET_CREATED", "TARGET_ENABLED", "TARGET_DISABLED",
            "TARGET_EXECUTION_STARTED", "TARGET_EXECUTION_SUCCEEDED",
            "TARGET_EXECUTION_FAILED", "TARGET_EXECUTION_BLOCKED",
            "TARGET_EXECUTION_SKIPPED", "TARGET_EXECUTION_CANCELLED",
            "WAVE_STARTED", "WAVE_SUCCEEDED", "WAVE_FAILED", "WAVE_BLOCKED",
            "CONFLICT_DETECTED", "NOTIFICATION_CREATED", "NOTIFICATION_SENT",
            "NOTIFICATION_FAILED",
        ]
        for name in expected:
            assert hasattr(FederationHistoryEventType, name), f"Missing {name}"

    def test_event_type_count(self) -> None:
        """Total event types should be 23."""
        assert len(FederationHistoryEventType) == 23

    def test_event_type_values(self) -> None:
        """Event type values use federation.* prefix."""
        assert FederationHistoryEventType.FEDERATION_CREATED.value == "federation.created"
        assert FederationHistoryEventType.TARGET_EXECUTION_STARTED.value == "federation.target_execution.started"
        assert FederationHistoryEventType.CONFLICT_DETECTED.value == "federation.conflict.detected"
        assert FederationHistoryEventType.NOTIFICATION_FAILED.value == "federation.notification.failed"


class TestFederationHistoryEvent:
    """Tests for FederationHistoryEvent model."""

    def test_valid_history_event(self) -> None:
        """Can create a valid FederationHistoryEvent."""
        now = datetime.now(timezone.utc)
        event = FederationHistoryEvent(
            history_event_id="fhe_abc123",
            federation_id="frp_plan1",
            event_type=FederationHistoryEventType.FEDERATION_CREATED,
            created_at=now,
        )
        assert event.history_event_id == "fhe_abc123"
        assert event.federation_id == "frp_plan1"
        assert event.event_type == FederationHistoryEventType.FEDERATION_CREATED
        assert event.created_at == now

    def test_id_prefix_validation(self) -> None:
        """history_event_id must start with fhe_."""
        with pytest.raises(ValueError):
            FederationHistoryEvent(
                history_event_id="bad_id",
                event_type=FederationHistoryEventType.FEDERATION_CREATED,
                created_at=datetime.now(timezone.utc),
            )

    def test_timezone_aware_required(self) -> None:
        """created_at must be timezone-aware."""
        with pytest.raises(ValueError):
            FederationHistoryEvent(
                history_event_id="fhe_abc",
                event_type=FederationHistoryEventType.FEDERATION_CREATED,
                created_at=datetime(2026, 1, 1),  # naive
            )

    def test_optional_fields_default_none(self) -> None:
        """Optional fields default to None."""
        event = FederationHistoryEvent(
            history_event_id="fhe_abc",
            event_type=FederationHistoryEventType.FEDERATION_CREATED,
            created_at=datetime.now(timezone.utc),
        )
        assert event.federation_id is None
        assert event.target_id is None
        assert event.rollout_id is None
        assert event.wave_id is None
        assert event.tenant_id is None
        assert event.environment is None
        assert event.ring_name is None
        assert event.region is None
        assert event.actor_id is None
        assert event.source_type is None
        assert event.source_id is None
        assert event.message is None

    def test_metadata_default_empty(self) -> None:
        """metadata defaults to empty dict."""
        event = FederationHistoryEvent(
            history_event_id="fhe_abc",
            event_type=FederationHistoryEventType.FEDERATION_CREATED,
            created_at=datetime.now(timezone.utc),
        )
        assert event.metadata == {}

    def test_all_optional_fields(self) -> None:
        """Can set all optional fields."""
        event = FederationHistoryEvent(
            history_event_id="fhe_abc",
            federation_id="frp_plan1",
            target_id="frt_target1",
            rollout_id="rp_roll1",
            wave_id="frw_wave1",
            event_type=FederationHistoryEventType.TARGET_EXECUTION_STARTED,
            tenant_id="tenant_a",
            environment="production",
            ring_name="canary",
            region="us-east-1",
            actor_id="user_1",
            source_type="federation_service",
            source_id="frp_plan1",
            message="Target execution started",
            metadata={"key": "value"},
            created_at=datetime.now(timezone.utc),
        )
        assert event.target_id == "frt_target1"
        assert event.environment == "production"
        assert event.metadata == {"key": "value"}


class TestFederationTargetTimeline:
    """Tests for FederationTargetTimeline model."""

    def test_minimal_target_timeline(self) -> None:
        """Can create a minimal FederationTargetTimeline."""
        tl = FederationTargetTimeline(target_id="frt_1")
        assert tl.target_id == "frt_1"
        assert tl.events == []
        assert tl.metadata == {}

    def test_full_target_timeline(self) -> None:
        """Can create a full FederationTargetTimeline."""
        now = datetime.now(timezone.utc)
        tl = FederationTargetTimeline(
            target_id="frt_1",
            rollout_id="rp_1",
            tenant_id="t1",
            environment="staging",
            ring_name="ring_1",
            region="us-west-2",
            status="succeeded",
            started_at=now,
            completed_at=now,
            duration_seconds=120.0,
            events=[],
        )
        assert tl.rollout_id == "rp_1"
        assert tl.duration_seconds == 120.0


class TestFederationWaveTimeline:
    """Tests for FederationWaveTimeline model."""

    def test_minimal_wave_timeline(self) -> None:
        """Can create a minimal FederationWaveTimeline."""
        wt = FederationWaveTimeline(wave_id="frw_1")
        assert wt.wave_id == "frw_1"
        assert wt.target_ids == []
        assert wt.target_timelines == []

    def test_wave_with_targets(self) -> None:
        """Can create a wave with target timelines."""
        wt = FederationWaveTimeline(
            wave_id="frw_1",
            name="Wave 1",
            status="succeeded",
            target_ids=["frt_1", "frt_2"],
            target_timelines=[
                FederationTargetTimeline(target_id="frt_1"),
                FederationTargetTimeline(target_id="frt_2"),
            ],
        )
        assert len(wt.target_timelines) == 2


class TestFederationTimeline:
    """Tests for FederationTimeline model."""

    def test_minimal_federation_timeline(self) -> None:
        """Can create a minimal FederationTimeline."""
        ft = FederationTimeline(federation_id="frp_1")
        assert ft.federation_id == "frp_1"
        assert ft.waves == []
        assert ft.targets == []
        assert ft.events == []
        assert ft.conflicts == []

    def test_full_federation_timeline(self) -> None:
        """Can create a full FederationTimeline."""
        now = datetime.now(timezone.utc)
        ft = FederationTimeline(
            federation_id="frp_1",
            name="My Federation",
            bundle_id="bundle_1",
            strategy="SEQUENTIAL",
            status="completed",
            created_at=now,
            started_at=now,
            completed_at=now,
            duration_seconds=300.0,
            waves=[FederationWaveTimeline(wave_id="frw_1")],
            targets=[FederationTargetTimeline(target_id="frt_1")],
            events=[],
            conflicts=[{"conflict_type": "DUPLICATE_TARGET"}],
        )
        assert ft.strategy == "SEQUENTIAL"
        assert len(ft.waves) == 1
        assert len(ft.conflicts) == 1


class TestFederationAnalyticsModels:
    """Tests for analytics summary models."""

    def test_target_health_summary_defaults(self) -> None:
        """FederationTargetHealthSummary defaults to zeros."""
        s = FederationTargetHealthSummary()
        assert s.total_targets == 0
        assert s.enabled_targets == 0
        assert s.failed_targets == 0

    def test_wave_outcome_summary_defaults(self) -> None:
        """FederationWaveOutcomeSummary defaults to zeros."""
        s = FederationWaveOutcomeSummary()
        assert s.total_waves == 0
        assert s.succeeded_waves == 0

    def test_conflict_summary_defaults(self) -> None:
        """FederationConflictSummary defaults to zeros."""
        s = FederationConflictSummary()
        assert s.total_conflicts == 0
        assert s.by_type == []

    def test_analytics_report_minimal(self) -> None:
        """Can create a minimal FederationAnalyticsReport."""
        now = datetime.now(timezone.utc)
        r = FederationAnalyticsReport(
            report_id="far_1",
            generated_at=now,
        )
        assert r.report_id == "far_1"
        assert r.total_federations == 0
        assert isinstance(r.target_health, FederationTargetHealthSummary)
        assert isinstance(r.wave_outcomes, FederationWaveOutcomeSummary)
        assert isinstance(r.conflicts, FederationConflictSummary)

    def test_analytics_report_id_prefix(self) -> None:
        """report_id must start with far_."""
        now = datetime.now(timezone.utc)
        with pytest.raises(ValueError):
            FederationAnalyticsReport(
                report_id="bad_id",
                generated_at=now,
            )

    def test_analytics_report_full(self) -> None:
        """Can create a full FederationAnalyticsReport."""
        now = datetime.now(timezone.utc)
        r = FederationAnalyticsReport(
            report_id="far_1",
            generated_at=now,
            window_start=now,
            window_end=now,
            total_federations=10,
            active_federations=3,
            completed_federations=5,
            failed_federations=1,
            cancelled_federations=1,
            blocked_federations=0,
            target_health=FederationTargetHealthSummary(
                total_targets=20,
                enabled_targets=18,
                disabled_targets=2,
                succeeded_targets=15,
                failed_targets=2,
                blocked_targets=1,
                skipped_targets=2,
            ),
            wave_outcomes=FederationWaveOutcomeSummary(
                total_waves=8,
                succeeded_waves=6,
                failed_waves=1,
                blocked_waves=0,
                pending_waves=1,
            ),
            conflicts=FederationConflictSummary(
                total_conflicts=3,
                error_conflicts=1,
                warning_conflicts=2,
                by_type=[{"type": "DUPLICATE_TARGET", "count": 2}],
            ),
            top_failed_targets=[{"target_id": "frt_1", "count": 3}],
            top_blocked_targets=[{"target_id": "frt_2", "count": 1}],
            environment_summary=[{"environment": "production", "total": 5}],
            region_summary=[{"region": "us-east-1", "total": 3}],
            tenant_summary=[{"tenant_id": "t1", "total": 2}],
        )
        assert r.total_federations == 10
        assert r.target_health.total_targets == 20
        assert r.wave_outcomes.total_waves == 8
        assert r.conflicts.total_conflicts == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_rollout_federation_history_model.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write the models**

Create `agent_app/governance/policy_rollout_federation_history.py`:

```python
"""Phase 47: Federation history, timeline, and analytics models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FederationHistoryEventType(str, Enum):
    """Event types for federation history."""

    FEDERATION_CREATED = "federation.created"
    FEDERATION_STARTED = "federation.started"
    FEDERATION_COMPLETED = "federation.completed"
    FEDERATION_FAILED = "federation.failed"
    FEDERATION_CANCELLED = "federation.cancelled"
    FEDERATION_BLOCKED = "federation.blocked"

    TARGET_CREATED = "federation.target.created"
    TARGET_ENABLED = "federation.target.enabled"
    TARGET_DISABLED = "federation.target.disabled"

    TARGET_EXECUTION_STARTED = "federation.target_execution.started"
    TARGET_EXECUTION_SUCCEEDED = "federation.target_execution.succeeded"
    TARGET_EXECUTION_FAILED = "federation.target_execution.failed"
    TARGET_EXECUTION_BLOCKED = "federation.target_execution.blocked"
    TARGET_EXECUTION_SKIPPED = "federation.target_execution.skipped"
    TARGET_EXECUTION_CANCELLED = "federation.target_execution.cancelled"

    WAVE_STARTED = "federation.wave.started"
    WAVE_SUCCEEDED = "federation.wave.succeeded"
    WAVE_FAILED = "federation.wave.failed"
    WAVE_BLOCKED = "federation.wave.blocked"

    CONFLICT_DETECTED = "federation.conflict.detected"
    NOTIFICATION_CREATED = "federation.notification.created"
    NOTIFICATION_SENT = "federation.notification.sent"
    NOTIFICATION_FAILED = "federation.notification.failed"


class FederationHistoryEvent(BaseModel):
    """A single federation history event."""

    history_event_id: str
    federation_id: str | None = None
    target_id: str | None = None
    rollout_id: str | None = None
    wave_id: str | None = None
    event_type: FederationHistoryEventType
    tenant_id: str | None = None
    environment: str | None = None
    ring_name: str | None = None
    region: str | None = None
    actor_id: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("history_event_id")
    @classmethod
    def _validate_id_prefix(cls, v: str) -> str:
        if not v.startswith("fhe_"):
            raise ValueError("history_event_id must start with fhe_")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class FederationTargetTimeline(BaseModel):
    """Timeline for a single federation target execution."""

    target_id: str
    rollout_id: str | None = None
    tenant_id: str | None = None
    environment: str | None = None
    ring_name: str | None = None
    region: str | None = None
    status: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    events: list[FederationHistoryEvent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FederationWaveTimeline(BaseModel):
    """Timeline for a single federation wave."""

    wave_id: str
    name: str | None = None
    status: str | None = None
    target_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    target_timelines: list[FederationTargetTimeline] = Field(default_factory=list)
    events: list[FederationHistoryEvent] = Field(default_factory=list)


class FederationTimeline(BaseModel):
    """Full timeline for a federated rollout plan."""

    federation_id: str
    name: str | None = None
    bundle_id: str | None = None
    strategy: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    waves: list[FederationWaveTimeline] = Field(default_factory=list)
    targets: list[FederationTargetTimeline] = Field(default_factory=list)
    events: list[FederationHistoryEvent] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FederationTargetHealthSummary(BaseModel):
    """Summary of target health across federations."""

    total_targets: int = 0
    enabled_targets: int = 0
    disabled_targets: int = 0
    succeeded_targets: int = 0
    failed_targets: int = 0
    blocked_targets: int = 0
    skipped_targets: int = 0


class FederationWaveOutcomeSummary(BaseModel):
    """Summary of wave outcomes across federations."""

    total_waves: int = 0
    succeeded_waves: int = 0
    failed_waves: int = 0
    blocked_waves: int = 0
    pending_waves: int = 0


class FederationConflictSummary(BaseModel):
    """Summary of conflicts across federations."""

    total_conflicts: int = 0
    error_conflicts: int = 0
    warning_conflicts: int = 0
    by_type: list[dict[str, Any]] = Field(default_factory=list)


class FederationAnalyticsReport(BaseModel):
    """Analytics report for federation executions."""

    report_id: str
    generated_at: datetime
    window_start: datetime | None = None
    window_end: datetime | None = None
    total_federations: int = 0
    active_federations: int = 0
    completed_federations: int = 0
    failed_federations: int = 0
    cancelled_federations: int = 0
    blocked_federations: int = 0
    target_health: FederationTargetHealthSummary = Field(default_factory=FederationTargetHealthSummary)
    wave_outcomes: FederationWaveOutcomeSummary = Field(default_factory=FederationWaveOutcomeSummary)
    conflicts: FederationConflictSummary = Field(default_factory=FederationConflictSummary)
    top_failed_targets: list[dict[str, Any]] = Field(default_factory=list)
    top_blocked_targets: list[dict[str, Any]] = Field(default_factory=list)
    environment_summary: list[dict[str, Any]] = Field(default_factory=list)
    region_summary: list[dict[str, Any]] = Field(default_factory=list)
    tenant_summary: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("report_id")
    @classmethod
    def _validate_report_id_prefix(cls, v: str) -> str:
        if not v.startswith("far_"):
            raise ValueError("report_id must start with far_")
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_rollout_federation_history_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/governance/policy_rollout_federation_history.py tests/unit/test_policy_rollout_federation_history_model.py
git commit -m "feat: Phase 47 Task 1 — federation history, timeline, and analytics models"
```

---

### Task 2: Federation history store

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_history_store.py`
- Test: `tests/unit/test_policy_rollout_federation_history_store.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Phase 47 Task 2 tests — federation history store."""

import pytest
from datetime import datetime, timezone, timedelta

from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEventType,
    FederationHistoryEvent,
)
from agent_app.runtime.policy_rollout_federation_history_store import (
    InMemoryFederationHistoryStore,
    SQLiteFederationHistoryStore,
    create_federation_history_store,
)


def _make_event(
    event_id: str = "fhe_001",
    federation_id: str | None = "frp_plan1",
    target_id: str | None = None,
    rollout_id: str | None = None,
    wave_id: str | None = None,
    event_type: FederationHistoryEventType = FederationHistoryEventType.FEDERATION_CREATED,
    environment: str | None = None,
    created_at: datetime | None = None,
) -> FederationHistoryEvent:
    return FederationHistoryEvent(
        history_event_id=event_id,
        federation_id=federation_id,
        target_id=target_id,
        rollout_id=rollout_id,
        wave_id=wave_id,
        event_type=event_type,
        environment=environment,
        created_at=created_at or datetime.now(timezone.utc),
    )


class TestInMemoryFederationHistoryStore:
    """Tests for InMemoryFederationHistoryStore."""

    @pytest.fixture()
    def store(self) -> InMemoryFederationHistoryStore:
        return InMemoryFederationHistoryStore()

    @pytest.mark.asyncio
    async def test_append_and_get(self, store: InMemoryFederationHistoryStore) -> None:
        """Append then get returns the event."""
        event = _make_event()
        result = await store.append(event)
        assert result.history_event_id == "fhe_001"
        fetched = await store.get("fhe_001")
        assert fetched is not None
        assert fetched.history_event_id == "fhe_001"

    @pytest.mark.asyncio
    async def test_get_missing(self, store: InMemoryFederationHistoryStore) -> None:
        """Get missing returns None."""
        assert await store.get("fhe_missing") is None

    @pytest.mark.asyncio
    async def test_list_all(self, store: InMemoryFederationHistoryStore) -> None:
        """List returns all events in chronological order."""
        now = datetime.now(timezone.utc)
        e1 = _make_event("fhe_001", created_at=now - timedelta(seconds=2))
        e2 = _make_event("fhe_002", created_at=now - timedelta(seconds=1))
        e3 = _make_event("fhe_003", created_at=now)
        await store.append(e2)
        await store.append(e1)
        await store.append(e3)
        result = await store.list()
        assert len(result) == 3
        assert result[0].history_event_id == "fhe_001"
        assert result[2].history_event_id == "fhe_003"

    @pytest.mark.asyncio
    async def test_list_by_federation_id(self, store: InMemoryFederationHistoryStore) -> None:
        """List filters by federation_id."""
        await store.append(_make_event("fhe_001", federation_id="frp_plan1"))
        await store.append(_make_event("fhe_002", federation_id="frp_plan2"))
        result = await store.list(federation_id="frp_plan1")
        assert len(result) == 1
        assert result[0].federation_id == "frp_plan1"

    @pytest.mark.asyncio
    async def test_list_by_target_id(self, store: InMemoryFederationHistoryStore) -> None:
        """List filters by target_id."""
        await store.append(_make_event("fhe_001", target_id="frt_1"))
        await store.append(_make_event("fhe_002", target_id="frt_2"))
        result = await store.list(target_id="frt_1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_by_rollout_id(self, store: InMemoryFederationHistoryStore) -> None:
        """List filters by rollout_id."""
        await store.append(_make_event("fhe_001", rollout_id="rp_1"))
        await store.append(_make_event("fhe_002", rollout_id="rp_2"))
        result = await store.list(rollout_id="rp_1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_by_wave_id(self, store: InMemoryFederationHistoryStore) -> None:
        """List filters by wave_id."""
        await store.append(_make_event("fhe_001", wave_id="frw_1"))
        await store.append(_make_event("fhe_002", wave_id="frw_2"))
        result = await store.list(wave_id="frw_1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_by_event_type(self, store: InMemoryFederationHistoryStore) -> None:
        """List filters by event_type."""
        await store.append(_make_event("fhe_001", event_type=FederationHistoryEventType.FEDERATION_CREATED))
        await store.append(_make_event("fhe_002", event_type=FederationHistoryEventType.FEDERATION_STARTED))
        result = await store.list(event_type=FederationHistoryEventType.FEDERATION_CREATED)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_with_limit(self, store: InMemoryFederationHistoryStore) -> None:
        """List respects limit."""
        for i in range(5):
            await store.append(_make_event(f"fhe_{i:03d}"))
        result = await store.list(limit=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_list_by_time_window(self, store: InMemoryFederationHistoryStore) -> None:
        """List filters by time window."""
        base = datetime(2026, 6, 1, tzinfo=timezone.utc)
        await store.append(_make_event("fhe_001", created_at=base))
        await store.append(_make_event("fhe_002", created_at=base + timedelta(days=5)))
        await store.append(_make_event("fhe_003", created_at=base + timedelta(days=10)))
        result = await store.list(window_start=base + timedelta(days=3), window_end=base + timedelta(days=7))
        assert len(result) == 1
        assert result[0].history_event_id == "fhe_002"


class TestSQLiteFederationHistoryStore:
    """Tests for SQLiteFederationHistoryStore."""

    @pytest.mark.asyncio
    async def test_sqlite_persistence(self, tmp_path) -> None:
        """SQLite store persists across instances."""
        db_path = str(tmp_path / "fed_history.db")
        store1 = SQLiteFederationHistoryStore(db_path)
        event = _make_event()
        await store1.append(event)
        # New instance with same path
        store2 = SQLiteFederationHistoryStore(db_path)
        fetched = await store2.get("fhe_001")
        assert fetched is not None
        assert fetched.history_event_id == "fhe_001"


class TestCreateFederationHistoryStore:
    """Tests for create_federation_history_store factory."""

    def test_create_memory(self) -> None:
        """Factory creates InMemory store."""
        store = create_federation_history_store(type="memory")
        assert isinstance(store, InMemoryFederationHistoryStore)

    def test_create_sqlite(self, tmp_path) -> None:
        """Factory creates SQLite store."""
        store = create_federation_history_store(type="sqlite", path=str(tmp_path / "test.db"))
        assert isinstance(store, SQLiteFederationHistoryStore)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_rollout_federation_history_store.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the store**

Create `agent_app/runtime/policy_rollout_federation_history_store.py`. Follow the exact pattern from `policy_rollout_federation_store.py` (Protocol + InMemory + SQLite + factory). The Protocol is:

```python
class FederationHistoryStore(Protocol):
    async def append(self, event: FederationHistoryEvent) -> FederationHistoryEvent: ...
    async def get(self, history_event_id: str) -> FederationHistoryEvent | None: ...
    async def list(
        self,
        federation_id: str | None = None,
        target_id: str | None = None,
        rollout_id: str | None = None,
        wave_id: str | None = None,
        event_type: FederationHistoryEventType | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int | None = None,
    ) -> list[FederationHistoryEvent]: ...
```

SQLite table:

```sql
CREATE TABLE IF NOT EXISTS policy_federation_history_events (
    history_event_id TEXT PRIMARY KEY,
    federation_id TEXT,
    target_id TEXT,
    rollout_id TEXT,
    wave_id TEXT,
    event_type TEXT NOT NULL,
    tenant_id TEXT,
    environment TEXT,
    ring_name TEXT,
    region TEXT,
    actor_id TEXT,
    source_type TEXT,
    source_id TEXT,
    message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
```

Key implementation notes:
- `InMemoryFederationHistoryStore`: store events in a `dict[str, FederationHistoryEvent]`, `list()` sorts by `created_at`, applies all filters
- `SQLiteFederationHistoryStore`: use `json.dumps` / `json.loads` for `metadata_json`, build WHERE clauses dynamically for filters, `datetime` columns as ISO strings
- `create_federation_history_store(type, path=None)`: factory function matching Phase 46 pattern

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_rollout_federation_history_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_federation_history_store.py tests/unit/test_policy_rollout_federation_history_store.py
git commit -m "feat: Phase 47 Task 2 — federation history store (InMemory + SQLite)"
```

---

### Task 3: Federation history recorder

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_history_recorder.py`
- Test: `tests/unit/test_policy_rollout_federation_history_recorder.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Phase 47 Task 3 tests — federation history recorder."""

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEventType,
    FederationHistoryEvent,
)
from agent_app.runtime.policy_rollout_federation_history_store import InMemoryFederationHistoryStore
from agent_app.runtime.policy_rollout_federation_history_recorder import FederationHistoryRecorder


class TestFederationHistoryRecorder:
    """Tests for FederationHistoryRecorder."""

    @pytest.fixture()
    def store(self) -> InMemoryFederationHistoryStore:
        return InMemoryFederationHistoryStore()

    @pytest.fixture()
    def recorder(self, store: InMemoryFederationHistoryStore) -> FederationHistoryRecorder:
        return FederationHistoryRecorder(history_store=store)

    @pytest.mark.asyncio
    async def test_record_creates_event(self, recorder: FederationHistoryRecorder, store: InMemoryFederationHistoryStore) -> None:
        """record() creates and appends a FederationHistoryEvent."""
        event = await recorder.record(
            event_type=FederationHistoryEventType.FEDERATION_CREATED,
            federation_id="frp_plan1",
        )
        assert event.history_event_id.startswith("fhe_")
        assert event.event_type == FederationHistoryEventType.FEDERATION_CREATED
        assert event.federation_id == "frp_plan1"
        # Stored in store
        fetched = await store.get(event.history_event_id)
        assert fetched is not None

    @pytest.mark.asyncio
    async def test_record_preserves_all_fields(self, recorder: FederationHistoryRecorder) -> None:
        """record() preserves all optional fields."""
        event = await recorder.record(
            event_type=FederationHistoryEventType.TARGET_EXECUTION_STARTED,
            federation_id="frp_1",
            target_id="frt_1",
            rollout_id="rp_1",
            wave_id="frw_1",
            tenant_id="t1",
            environment="production",
            ring_name="canary",
            region="us-east-1",
            actor_id="user_1",
            source_type="federation_service",
            source_id="frp_1",
            message="Started target execution",
            metadata={"key": "value"},
        )
        assert event.target_id == "frt_1"
        assert event.rollout_id == "rp_1"
        assert event.wave_id == "frw_1"
        assert event.tenant_id == "t1"
        assert event.environment == "production"
        assert event.ring_name == "canary"
        assert event.region == "us-east-1"
        assert event.actor_id == "user_1"
        assert event.source_type == "federation_service"
        assert event.source_id == "frp_1"
        assert event.message == "Started target execution"
        assert event.metadata == {"key": "value"}

    @pytest.mark.asyncio
    async def test_record_generates_id(self, recorder: FederationHistoryRecorder) -> None:
        """record() generates a unique fhe_ prefixed id."""
        e1 = await recorder.record(event_type=FederationHistoryEventType.FEDERATION_CREATED)
        e2 = await recorder.record(event_type=FederationHistoryEventType.FEDERATION_STARTED)
        assert e1.history_event_id != e2.history_event_id
        assert e1.history_event_id.startswith("fhe_")
        assert e2.history_event_id.startswith("fhe_")

    @pytest.mark.asyncio
    async def test_record_timezone_aware(self, recorder: FederationHistoryRecorder) -> None:
        """record() creates timezone-aware created_at."""
        event = await recorder.record(event_type=FederationHistoryEventType.FEDERATION_CREATED)
        assert event.created_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_record_with_no_audit_logger(self, recorder: FederationHistoryRecorder) -> None:
        """record() works without audit_logger (no error)."""
        event = await recorder.record(event_type=FederationHistoryEventType.FEDERATION_CREATED)
        assert event is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_rollout_federation_history_recorder.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the recorder**

Create `agent_app/runtime/policy_rollout_federation_history_recorder.py`:

```python
"""Phase 47: Federation history recorder."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEventType,
    FederationHistoryEvent,
)

if TYPE_CHECKING:
    from agent_app.runtime.policy_rollout_federation_history_store import FederationHistoryStore
    from agent_app.governance.policy_audit import AuditLogger


class FederationHistoryRecorder:
    """Records federation history events to the history store."""

    def __init__(
        self,
        history_store: FederationHistoryStore,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._store = history_store
        self._audit_logger = audit_logger

    async def record(
        self,
        event_type: FederationHistoryEventType,
        federation_id: str | None = None,
        target_id: str | None = None,
        rollout_id: str | None = None,
        wave_id: str | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        ring_name: str | None = None,
        region: str | None = None,
        actor_id: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FederationHistoryEvent:
        """Create and append a federation history event."""
        event = FederationHistoryEvent(
            history_event_id=f"fhe_{uuid.uuid4().hex[:16]}",
            federation_id=federation_id,
            target_id=target_id,
            rollout_id=rollout_id,
            wave_id=wave_id,
            event_type=event_type,
            tenant_id=tenant_id,
            environment=environment,
            ring_name=ring_name,
            region=region,
            actor_id=actor_id,
            source_type=source_type,
            source_id=source_id,
            message=message,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
        )
        result = await self._store.append(event)

        # Best-effort audit
        if self._audit_logger is not None:
            try:
                self._audit_logger.log(
                    action="policy.federation.history.recorded",
                    actor_id=actor_id or "system",
                    target_type="federation_history_event",
                    target_id=event.history_event_id,
                    details={
                        "federation_id": federation_id,
                        "event_type": event_type.value,
                        "target_id": target_id,
                    },
                )
            except Exception:
                pass  # Best-effort

        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_rollout_federation_history_recorder.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_federation_history_recorder.py tests/unit/test_policy_rollout_federation_history_recorder.py
git commit -m "feat: Phase 47 Task 3 — federation history recorder"
```

---

### Task 4: Federation observability service

**Files:**
- Create: `agent_app/runtime/policy_rollout_federation_observability_service.py`
- Test: `tests/unit/test_policy_rollout_federation_observability_service.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Phase 47 Task 4 tests — federation observability service."""

import pytest
from datetime import datetime, timezone, timedelta

from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEventType,
    FederationHistoryEvent,
    FederationTimeline,
    FederationAnalyticsReport,
    FederationTargetHealthSummary,
)
from agent_app.runtime.policy_rollout_federation_history_store import InMemoryFederationHistoryStore
from agent_app.runtime.policy_rollout_federation_observability_service import FederationObservabilityService
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedTargetStatus,
    FederatedRolloutTargetExecution,
    FederatedRolloutTargetExecutionStatus,
)
from agent_app.runtime.policy_rollout_federation_store import (
    InMemoryFederatedRolloutPlanStore,
    InMemoryFederatedRolloutTargetStore,
)


def _make_event(
    event_id: str = "fhe_001",
    federation_id: str = "frp_plan1",
    target_id: str | None = None,
    wave_id: str | None = None,
    event_type: FederationHistoryEventType = FederationHistoryEventType.FEDERATION_CREATED,
    environment: str | None = None,
    tenant_id: str | None = None,
    region: str | None = None,
    created_at: datetime | None = None,
) -> FederationHistoryEvent:
    return FederationHistoryEvent(
        history_event_id=event_id,
        federation_id=federation_id,
        target_id=target_id,
        wave_id=wave_id,
        event_type=event_type,
        environment=environment,
        tenant_id=tenant_id,
        region=region,
        created_at=created_at or datetime.now(timezone.utc),
    )


class TestFederationObservabilityTimeline:
    """Tests for FederationObservabilityService.get_timeline."""

    @pytest.mark.asyncio
    async def test_timeline_from_history_events(self) -> None:
        """Timeline built from history events."""
        store = InMemoryFederationHistoryStore()
        now = datetime.now(timezone.utc)
        await store.append(_make_event("fhe_001", event_type=FederationHistoryEventType.FEDERATION_CREATED, created_at=now - timedelta(seconds=10)))
        await store.append(_make_event("fhe_002", event_type=FederationHistoryEventType.FEDERATION_STARTED, created_at=now - timedelta(seconds=5)))
        await store.append(_make_event("fhe_003", event_type=FederationHistoryEventType.FEDERATION_COMPLETED, created_at=now))

        svc = FederationObservabilityService(history_store=store)
        timeline = await svc.get_timeline("frp_plan1")
        assert isinstance(timeline, FederationTimeline)
        assert timeline.federation_id == "frp_plan1"
        assert len(timeline.events) == 3

    @pytest.mark.asyncio
    async def test_timeline_includes_target_executions(self) -> None:
        """Timeline includes target execution events."""
        store = InMemoryFederationHistoryStore()
        now = datetime.now(timezone.utc)
        await store.append(_make_event("fhe_001", event_type=FederationHistoryEventType.FEDERATION_CREATED, created_at=now - timedelta(seconds=5)))
        await store.append(_make_event("fhe_002", target_id="frt_1", event_type=FederationHistoryEventType.TARGET_EXECUTION_STARTED, created_at=now - timedelta(seconds=3)))
        await store.append(_make_event("fhe_003", target_id="frt_1", event_type=FederationHistoryEventType.TARGET_EXECUTION_SUCCEEDED, created_at=now))

        svc = FederationObservabilityService(history_store=store)
        timeline = await svc.get_timeline("frp_plan1")
        assert len(timeline.targets) >= 1
        target_tl = timeline.targets[0]
        assert target_tl.target_id == "frt_1"

    @pytest.mark.asyncio
    async def test_timeline_includes_waves(self) -> None:
        """Timeline includes wave events."""
        store = InMemoryFederationHistoryStore()
        now = datetime.now(timezone.utc)
        await store.append(_make_event("fhe_001", wave_id="frw_1", event_type=FederationHistoryEventType.WAVE_STARTED, created_at=now - timedelta(seconds=2)))
        await store.append(_make_event("fhe_002", wave_id="frw_1", event_type=FederationHistoryEventType.WAVE_SUCCEEDED, created_at=now))

        svc = FederationObservabilityService(history_store=store)
        timeline = await svc.get_timeline("frp_plan1")
        assert len(timeline.waves) >= 1
        assert timeline.waves[0].wave_id == "frw_1"

    @pytest.mark.asyncio
    async def test_timeline_enriched_from_plan_store(self) -> None:
        """Timeline enriched from federated plan store."""
        history_store = InMemoryFederationHistoryStore()
        plan_store = InMemoryFederatedRolloutPlanStore()

        now = datetime.now(timezone.utc)
        plan = FederatedRolloutPlan(
            plan_id="frp_plan1",
            bundle_id="bundle_1",
            strategy="SEQUENTIAL",
            status=FederatedRolloutPlanStatus.COMPLETED,
            name="Test Plan",
            created_at=now,
        )
        await plan_store.create(plan)

        await history_store.append(_make_event(event_type=FederationHistoryEventType.FEDERATION_CREATED))

        svc = FederationObservabilityService(
            history_store=history_store,
            federation_plan_store=plan_store,
        )
        timeline = await svc.get_timeline("frp_plan1")
        assert timeline.name == "Test Plan"
        assert timeline.strategy == "SEQUENTIAL"
        assert timeline.status == "COMPLETED"


class TestFederationObservabilityReport:
    """Tests for FederationObservabilityService.generate_report."""

    @pytest.mark.asyncio
    async def test_report_empty_sources(self) -> None:
        """Report with no events produces empty summaries."""
        store = InMemoryFederationHistoryStore()
        svc = FederationObservabilityService(history_store=store)
        report = await svc.generate_report()
        assert isinstance(report, FederationAnalyticsReport)
        assert report.total_federations == 0
        assert report.target_health.total_targets == 0

    @pytest.mark.asyncio
    async def test_report_counts_completed_failed(self) -> None:
        """Report counts completed and failed federations."""
        store = InMemoryFederationHistoryStore()
        now = datetime.now(timezone.utc)
        await store.append(_make_event("fhe_001", federation_id="frp_1", event_type=FederationHistoryEventType.FEDERATION_CREATED, created_at=now - timedelta(seconds=5)))
        await store.append(_make_event("fhe_002", federation_id="frp_1", event_type=FederationHistoryEventType.FEDERATION_COMPLETED, created_at=now))
        await store.append(_make_event("fhe_003", federation_id="frp_2", event_type=FederationHistoryEventType.FEDERATION_CREATED, created_at=now - timedelta(seconds=5)))
        await store.append(_make_event("fhe_004", federation_id="frp_2", event_type=FederationHistoryEventType.FEDERATION_FAILED, created_at=now))

        svc = FederationObservabilityService(history_store=store)
        report = await svc.generate_report()
        assert report.total_federations == 2
        assert report.completed_federations == 1
        assert report.failed_federations == 1

    @pytest.mark.asyncio
    async def test_report_target_health(self) -> None:
        """Report target health summary."""
        store = InMemoryFederationHistoryStore()
        now = datetime.now(timezone.utc)
        await store.append(_make_event("fhe_001", target_id="frt_1", event_type=FederationHistoryEventType.TARGET_EXECUTION_STARTED, created_at=now - timedelta(seconds=3)))
        await store.append(_make_event("fhe_002", target_id="frt_1", event_type=FederationHistoryEventType.TARGET_EXECUTION_SUCCEEDED, created_at=now))
        await store.append(_make_event("fhe_003", target_id="frt_2", event_type=FederationHistoryEventType.TARGET_EXECUTION_FAILED, created_at=now))

        svc = FederationObservabilityService(history_store=store)
        report = await svc.generate_report()
        assert report.target_health.succeeded_targets == 1
        assert report.target_health.failed_targets == 1

    @pytest.mark.asyncio
    async def test_report_wave_outcomes(self) -> None:
        """Report wave outcome summary."""
        store = InMemoryFederationHistoryStore()
        now = datetime.now(timezone.utc)
        await store.append(_make_event("fhe_001", wave_id="frw_1", event_type=FederationHistoryEventType.WAVE_STARTED, created_at=now - timedelta(seconds=2)))
        await store.append(_make_event("fhe_002", wave_id="frw_1", event_type=FederationHistoryEventType.WAVE_SUCCEEDED, created_at=now))

        svc = FederationObservabilityService(history_store=store)
        report = await svc.generate_report()
        assert report.wave_outcomes.succeeded_waves == 1

    @pytest.mark.asyncio
    async def test_report_conflict_summary(self) -> None:
        """Report conflict summary."""
        store = InMemoryFederationHistoryStore()
        now = datetime.now(timezone.utc)
        await store.append(_make_event("fhe_001", event_type=FederationHistoryEventType.CONFLICT_DETECTED, metadata={"severity": "error"}, created_at=now))

        svc = FederationObservabilityService(history_store=store)
        report = await svc.generate_report()
        assert report.conflicts.total_conflicts == 1

    @pytest.mark.asyncio
    async def test_report_environment_summary(self) -> None:
        """Report environment summary."""
        store = InMemoryFederationHistoryStore()
        now = datetime.now(timezone.utc)
        await store.append(_make_event("fhe_001", environment="production", event_type=FederationHistoryEventType.FEDERATION_CREATED, created_at=now))
        await store.append(_make_event("fhe_002", environment="staging", event_type=FederationHistoryEventType.FEDERATION_CREATED, created_at=now))

        svc = FederationObservabilityService(history_store=store)
        report = await svc.generate_report()
        assert len(report.environment_summary) == 2

    @pytest.mark.asyncio
    async def test_missing_optional_stores_produce_partial_report(self) -> None:
        """Missing optional stores produce partial report without error."""
        store = InMemoryFederationHistoryStore()
        svc = FederationObservabilityService(history_store=store)
        report = await svc.generate_report()
        assert report is not None


class TestFederationObservabilityListEvents:
    """Tests for FederationObservabilityService.list_history_events."""

    @pytest.mark.asyncio
    async def test_list_events(self) -> None:
        """list_history_events returns filtered events."""
        store = InMemoryFederationHistoryStore()
        now = datetime.now(timezone.utc)
        await store.append(_make_event("fhe_001", federation_id="frp_1", created_at=now))
        await store.append(_make_event("fhe_002", federation_id="frp_2", created_at=now))

        svc = FederationObservabilityService(history_store=store)
        result = await svc.list_history_events(federation_id="frp_1")
        assert len(result) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_rollout_federation_observability_service.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the observability service**

Create `agent_app/runtime/policy_rollout_federation_observability_service.py`. The service should:

1. `get_timeline(federation_id)`:
   - Fetch all events for federation_id from history_store
   - Group events by target_id → build `FederationTargetTimeline` list
   - Group events by wave_id → build `FederationWaveTimeline` list
   - If federation_plan_store available, enrich name/bundle_id/strategy/status/created_at/started_at/completed_at
   - Calculate `duration_seconds` from started_at to completed_at when both available
   - Build and return `FederationTimeline`

2. `generate_report(window_start=None, window_end=None)`:
   - Fetch all events (with time window filter)
   - Count unique federation_ids, categorize by terminal event type (COMPLETED/FAILED/CANCELLED/BLOCKED), active = has STARTED but no terminal
   - Build `FederationTargetHealthSummary` from TARGET_EXECUTION_* events
   - Build `FederationWaveOutcomeSummary` from WAVE_* events
   - Build `FederationConflictSummary` from CONFLICT_DETECTED events
   - Build environment/region/tenant summaries from event fields
   - Build top_failed/blocked targets from event counts
   - Generate `far_` prefixed report_id
   - Return `FederationAnalyticsReport`

3. `list_history_events(...)`: delegate to `history_store.list(...)`

Constructor accepts optional stores: `history_store`, `federation_plan_store`, `federation_target_store`, `rollout_history_service`, `notification_store`, `audit_logger`. All optional — missing stores produce partial results.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_rollout_federation_observability_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/runtime/policy_rollout_federation_observability_service.py tests/unit/test_policy_rollout_federation_observability_service.py
git commit -m "feat: Phase 47 Task 4 — federation observability service"
```

---

### Task 5: Service integrations + export helpers

**Files:**
- Modify: `agent_app/runtime/policy_rollout_federation_service.py`
- Modify: `agent_app/runtime/policy_notification_service.py`
- Modify: `agent_app/runtime/policy_compliance_export.py`
- Test: `tests/unit/test_policy_rollout_federation_history_model.py` (add export tests)

- [ ] **Step 1: Write the failing export tests**

Append to `tests/unit/test_policy_rollout_federation_history_model.py`:

```python
from agent_app.runtime.policy_compliance_export import (
    federation_timeline_to_json,
    federation_analytics_report_to_json,
    federation_analytics_report_to_csv_rows,
)


class TestFederationExportHelpers:
    """Tests for federation export helpers."""

    def test_timeline_to_json(self) -> None:
        """federation_timeline_to_json produces valid JSON."""
        tl = FederationTimeline(federation_id="frp_1", name="Test")
        result = federation_timeline_to_json(tl)
        assert isinstance(result, str)
        assert "frp_1" in result
        assert "Test" in result

    def test_analytics_report_to_json(self) -> None:
        """federation_analytics_report_to_json produces valid JSON."""
        report = FederationAnalyticsReport(
            report_id="far_1",
            generated_at=datetime.now(timezone.utc),
            total_federations=5,
        )
        result = federation_analytics_report_to_json(report)
        assert isinstance(result, str)
        assert "far_1" in result
        assert "5" in result

    def test_analytics_report_to_csv_rows(self) -> None:
        """federation_analytics_report_to_csv_rows produces list of dicts."""
        report = FederationAnalyticsReport(
            report_id="far_1",
            generated_at=datetime.now(timezone.utc),
            total_federations=5,
            environment_summary=[{"environment": "production", "total": 3}],
        )
        rows = federation_analytics_report_to_csv_rows(report)
        assert isinstance(rows, list)
        assert len(rows) > 0
        # Should include summary row
        first = rows[0]
        assert "report_id" in first or "section" in first
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_rollout_federation_history_model.py::TestFederationExportHelpers -v`
Expected: FAIL — import error

- [ ] **Step 3: Add export helpers to policy_compliance_export.py**

Append these three functions to `agent_app/runtime/policy_compliance_export.py`:

```python
def federation_timeline_to_json(timeline: FederationTimeline) -> str:
    """Export a FederationTimeline to JSON string."""
    return timeline.model_dump_json(indent=2)


def federation_analytics_report_to_json(report: FederationAnalyticsReport) -> str:
    """Export a FederationAnalyticsReport to JSON string."""
    return report.model_dump_json(indent=2)


def federation_analytics_report_to_csv_rows(report: FederationAnalyticsReport) -> list[dict[str, Any]]:
    """Export a FederationAnalyticsReport to flat CSV-compatible rows."""
    rows: list[dict[str, Any]] = []
    # Summary row
    rows.append({
        "section": "summary",
        "report_id": report.report_id,
        "generated_at": report.generated_at.isoformat(),
        "window_start": report.window_start.isoformat() if report.window_start else "",
        "window_end": report.window_end.isoformat() if report.window_end else "",
        "total_federations": report.total_federations,
        "active_federations": report.active_federations,
        "completed_federations": report.completed_federations,
        "failed_federations": report.failed_federations,
        "cancelled_federations": report.cancelled_federations,
        "blocked_federations": report.blocked_federations,
    })
    # Target health row
    rows.append({
        "section": "target_health",
        "total_targets": report.target_health.total_targets,
        "enabled_targets": report.target_health.enabled_targets,
        "disabled_targets": report.target_health.disabled_targets,
        "succeeded_targets": report.target_health.succeeded_targets,
        "failed_targets": report.target_health.failed_targets,
        "blocked_targets": report.target_health.blocked_targets,
        "skipped_targets": report.target_health.skipped_targets,
    })
    # Wave outcomes row
    rows.append({
        "section": "wave_outcomes",
        "total_waves": report.wave_outcomes.total_waves,
        "succeeded_waves": report.wave_outcomes.succeeded_waves,
        "failed_waves": report.wave_outcomes.failed_waves,
        "blocked_waves": report.wave_outcomes.blocked_waves,
        "pending_waves": report.wave_outcomes.pending_waves,
    })
    # Conflict summary row
    rows.append({
        "section": "conflicts",
        "total_conflicts": report.conflicts.total_conflicts,
        "error_conflicts": report.conflicts.error_conflicts,
        "warning_conflicts": report.conflicts.warning_conflicts,
    })
    # Environment summary rows
    for env in report.environment_summary:
        rows.append({"section": "environment_summary", **env})
    # Region summary rows
    for reg in report.region_summary:
        rows.append({"section": "region_summary", **reg})
    # Tenant summary rows
    for ten in report.tenant_summary:
        rows.append({"section": "tenant_summary", **ten})
    return rows
```

Also add imports at top of `policy_compliance_export.py`:

```python
from agent_app.governance.policy_rollout_federation_history import (
    FederationTimeline,
    FederationAnalyticsReport,
)
```

- [ ] **Step 4: Modify RolloutFederationService to accept and use recorder**

In `agent_app/runtime/policy_rollout_federation_service.py`:

1. Add `federation_recorder: FederationHistoryRecorder | None = None` parameter to `__init__`
2. In each lifecycle method (`create_target`, `create_federated_plan`, `start_federated_plan`, `run_next_target`, `run_all_available`, `cancel_federated_plan`, `detect_conflicts`), add best-effort recorder calls:

```python
if self._federation_recorder is not None:
    try:
        await self._federation_recorder.record(
            event_type=FederationHistoryEventType.TARGET_CREATED,
            federation_id=target.target_id,
            target_id=target.target_id,
            ...
        )
    except Exception:
        pass  # Best-effort
```

Map each action to the appropriate `FederationHistoryEventType`:
- `create_target` → `TARGET_CREATED`
- `create_federated_plan` → `FEDERATION_CREATED`
- `start_federated_plan` → `FEDERATION_STARTED`
- `run_next_target` execution start → `TARGET_EXECUTION_STARTED`
- `run_next_target` execution success → `TARGET_EXECUTION_SUCCEEDED`
- `run_next_target` execution failure → `TARGET_EXECUTION_FAILED`
- `run_next_target` execution skip → `TARGET_EXECUTION_SKIPPED`
- `cancel_federated_plan` → `FEDERATION_CANCELLED`
- `detect_conflicts` → `CONFLICT_DETECTED`

- [ ] **Step 5: Modify PolicyNotificationService to accept and use federation recorder**

In `agent_app/runtime/policy_notification_service.py`:

1. Add `federation_recorder: FederationHistoryRecorder | None = None` parameter to `__init__`
2. In `send_notification`, after sending, check if `metadata.get("federation_id")` exists or `source_type` starts with `federation`:
   - If yes, record `NOTIFICATION_CREATED`, `NOTIFICATION_SENT`, or `NOTIFICATION_FAILED` (best-effort)

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_rollout_federation_history_model.py::TestFederationExportHelpers -v`
Expected: PASS

Also run existing Phase 46 tests to verify backward compatibility:

Run: `pytest tests/unit/test_policy_rollout_federation_service.py tests/unit/test_policy_notification_service.py -v --timeout=60`
Expected: PASS (existing tests should still pass — recorder is optional, default None)

- [ ] **Step 7: Commit**

```bash
git add agent_app/runtime/policy_rollout_federation_service.py agent_app/runtime/policy_notification_service.py agent_app/runtime/policy_compliance_export.py tests/unit/test_policy_rollout_federation_history_model.py
git commit -m "feat: Phase 47 Task 5 — service integrations and export helpers"
```

---

### Task 6: Config, loader, RBAC, change events, AgentApp properties

**Files:**
- Modify: `agent_app/governance/policy_rbac.py`
- Modify: `agent_app/governance/policy_change_event.py`
- Modify: `agent_app/config/schema.py`
- Modify: `agent_app/config/loader.py`
- Modify: `agent_app/core/app.py`
- Test: `tests/unit/test_policy_rollout_federation_history_config.py`
- Modify: `tests/unit/test_policy_change_event.py` (81 → 88)
- Modify: `tests/unit/test_policy_rollout_gate_config.py` (81 → 88)
- Modify: `tests/unit/test_policy_notification_config.py` (81 → 88)
- Modify: `tests/unit/test_policy_rollout_history_config.py` (81 → 88)

- [ ] **Step 1: Write the config test file**

Create `tests/unit/test_policy_rollout_federation_history_config.py` with these test classes:

1. `TestRolloutFederationHistoryConfig` — test `RolloutFederationHistoryConfig(enabled=False, store=None)` defaults, enabled config, store config
2. `TestFederationHistoryRBAC` — test `FEDERATION_HISTORY_VIEW`, `FEDERATION_ANALYTICS_VIEW`, `FEDERATION_ANALYTICS_EXPORT` exist, values correct, view permissions in `_DEFAULT_ALLOWED`
3. `TestFederationHistoryChangeEvents` — test 7 new event types exist (81→88), test each value
4. `TestAgentAppFederationHistoryProperties` — test `federation_history_store`, `federation_history_recorder`, `federation_observability_service` default None, set/get works

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_rollout_federation_history_config.py -v`
Expected: FAIL

- [ ] **Step 3: Add RBAC permissions**

In `agent_app/governance/policy_rbac.py`, add to `PolicyReleasePermission`:

```python
FEDERATION_HISTORY_VIEW = "policy.federation.history.view"
FEDERATION_ANALYTICS_VIEW = "policy.federation.analytics.view"
FEDERATION_ANALYTICS_EXPORT = "policy.federation.analytics.export"
```

Add `FEDERATION_HISTORY_VIEW` and `FEDERATION_ANALYTICS_VIEW` to `_DEFAULT_ALLOWED`.

- [ ] **Step 4: Add change event types**

In `agent_app/governance/policy_change_event.py`, add 7 new event types:

```python
FEDERATION_HISTORY_RECORDED = "policy.federation.history.recorded"
FEDERATION_HISTORY_VIEWED = "policy.federation.history.viewed"
FEDERATION_TIMELINE_GENERATED = "policy.federation.timeline.generated"
FEDERATION_ANALYTICS_GENERATED = "policy.federation.analytics.generated"
FEDERATION_ANALYTICS_EXPORT_GENERATED = "policy.federation.analytics.export_generated"
FEDERATION_ANALYTICS_EXPORT_FAILED = "policy.federation.analytics.export_failed"
FEDERATION_ANALYTICS_PERMISSION_DENIED = "policy.federation.analytics.permission_denied"
```

Total count goes from 81 to 88.

- [ ] **Step 5: Add config schema**

In `agent_app/config/schema.py`, add:

```python
class RolloutFederationHistoryConfig(BaseModel):
    """Configuration for rollout federation history."""
    enabled: bool = False
    store: PolicyReleaseStoreConfig | None = None
```

Add `rollout_federation_history: RolloutFederationHistoryConfig | None = None` to `PolicyReleaseConfig`.

- [ ] **Step 6: Add AgentApp properties**

In `agent_app/core/app.py`, add three properties following existing pattern:

```python
@property
def federation_history_store(self) -> Any:
    return self._extras.get("federation_history_store")

@federation_history_store.setter
def federation_history_store(self, value: Any) -> None:
    self._extras["federation_history_store"] = value

@property
def federation_history_recorder(self) -> Any:
    return self._extras.get("federation_history_recorder")

@federation_history_recorder.setter
def federation_history_recorder(self, value: Any) -> None:
    self._extras["federation_history_recorder"] = value

@property
def federation_observability_service(self) -> Any:
    return self._extras.get("federation_observability_service")

@federation_observability_service.setter
def federation_observability_service(self, value: Any) -> None:
    self._extras["federation_observability_service"] = value
```

- [ ] **Step 7: Add loader wiring**

In `agent_app/config/loader.py`, add Phase 47 wiring block after Phase 46 block:

```python
# Phase 47: Rollout Federation History
fed_hist_cfg = getattr(release_cfg, "rollout_federation_history", None)
if fed_hist_cfg and fed_hist_cfg.enabled:
    from agent_app.runtime.policy_rollout_federation_history_store import create_federation_history_store
    from agent_app.runtime.policy_rollout_federation_history_recorder import FederationHistoryRecorder
    from agent_app.runtime.policy_rollout_federation_observability_service import FederationObservabilityService

    fed_hist_store = create_federation_history_store(
        type=fed_hist_cfg.store.type if fed_hist_cfg.store else "memory",
        path=fed_hist_cfg.store.path if fed_hist_cfg.store else None,
    )
    fed_hist_recorder = FederationHistoryRecorder(
        history_store=fed_hist_store,
        audit_logger=audit_logger,
    )
    fed_obs_service = FederationObservabilityService(
        history_store=fed_hist_store,
        federation_plan_store=app.federated_rollout_plan_store,
        federation_target_store=app.federated_rollout_target_store,
        audit_logger=audit_logger,
    )
    app.federation_history_store = fed_hist_store
    app.federation_history_recorder = fed_hist_recorder
    app.federation_observability_service = fed_obs_service

    # Inject recorder into federation service
    if app.rollout_federation_service is not None:
        app.rollout_federation_service._federation_recorder = fed_hist_recorder
    # Inject recorder into notification service
    if app.notification_service is not None:
        app.notification_service._federation_recorder = fed_hist_recorder
```

- [ ] **Step 8: Update event count assertions**

In these 4 test files, change `assert len(PolicyChangeEventType) == 81` to `assert len(PolicyChangeEventType) == 88`:
- `tests/unit/test_policy_change_event.py`
- `tests/unit/test_policy_rollout_gate_config.py`
- `tests/unit/test_policy_notification_config.py`
- `tests/unit/test_policy_rollout_history_config.py`

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_rollout_federation_history_config.py tests/unit/test_policy_change_event.py tests/unit/test_policy_rollout_gate_config.py tests/unit/test_policy_notification_config.py tests/unit/test_policy_rollout_history_config.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add agent_app/governance/policy_rbac.py agent_app/governance/policy_change_event.py agent_app/config/schema.py agent_app/config/loader.py agent_app/core/app.py tests/unit/test_policy_rollout_federation_history_config.py tests/unit/test_policy_change_event.py tests/unit/test_policy_rollout_gate_config.py tests/unit/test_policy_notification_config.py tests/unit/test_policy_rollout_history_config.py
git commit -m "feat: Phase 47 Task 6 — config, loader, RBAC, change events, AgentApp properties"
```

---

### Task 7: CLI federation history/timeline/analytics/export commands

**Files:**
- Modify: `agent_app/cli.py`
- Test: `tests/unit/test_policy_rollout_federation_history_cli.py`

- [ ] **Step 1: Write the CLI test file**

Create `tests/unit/test_policy_rollout_federation_history_cli.py` with tests for:

1. `federation history --federation-id frp_1` — lists events
2. `federation timeline --federation-id frp_1` — shows timeline
3. `federation timeline --federation-id frp_1 --json` — shows JSON timeline
4. `federation analytics` — shows analytics report
5. `federation analytics --since 2026-06-01T00:00:00Z` — filters by time
6. `federation analytics export --format json --output /tmp/report.json` — exports JSON
7. `federation analytics export --format csv --output /tmp/report.csv` — exports CSV
8. Missing federation exits non-zero
9. Invalid datetime exits non-zero
10. Unsupported format exits non-zero

Follow the existing `test_policy_rollout_federation_cli.py` pattern with `_run_cli` helper.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_rollout_federation_history_cli.py -v`
Expected: FAIL

- [ ] **Step 3: Implement CLI commands**

In `agent_app/cli.py`, add these subcommands under `policy federation`:

1. `history` — calls `observability_service.list_history_events(federation_id=...)`
2. `timeline` — calls `observability_service.get_timeline(federation_id=...)`; with `--json` calls `federation_timeline_to_json()`
3. `analytics` — calls `observability_service.generate_report(window_start=..., window_end=...)`
4. `analytics export` — calls `generate_report()`, then `federation_analytics_report_to_json()` or `federation_analytics_report_to_csv_rows()`, writes to `--output` file

Error handling:
- Missing federation_id → exit 1
- Invalid datetime → exit 1
- Unsupported format → exit 1
- Output write failure → exit 1

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_rollout_federation_history_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/cli.py tests/unit/test_policy_rollout_federation_history_cli.py
git commit -m "feat: Phase 47 Task 7 — CLI federation history/timeline/analytics commands"
```

---

### Task 8: Console federation history/timeline/analytics pages

**Files:**
- Modify: `agent_app/console/router.py`
- Modify: `agent_app/adapters/fastapi.py`
- Create: `agent_app/console/templates/policy_federation_history.html`
- Create: `agent_app/console/templates/policy_federation_timeline.html`
- Create: `agent_app/console/templates/policy_federation_analytics.html`
- Test: `tests/unit/test_policy_rollout_federation_history_console.py`

- [ ] **Step 1: Write the console test file**

Create `tests/unit/test_policy_rollout_federation_history_console.py` with tests for:

1. `GET /policy-console/federation/plans/{federation_id}/history` — renders history events
2. `GET /policy-console/federation/plans/{federation_id}/timeline` — renders timeline
3. `GET /policy-console/federation/analytics` — renders analytics
4. `POST /policy-console/federation/analytics` — renders analytics with window
5. Federation plan detail page shows links to history/timeline
6. Errors render clearly (no traceback)
7. Skip when FastAPI/Jinja2 not installed

Follow the existing `test_policy_rollout_federation_console.py` pattern with `pytest.importorskip("fastapi")`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_policy_rollout_federation_history_console.py -v`
Expected: FAIL or SKIP (if no FastAPI)

- [ ] **Step 3: Add routes to router.py**

Add to `agent_app/console/router.py`:

1. `GET /federation/plans/{federation_id}/history` — list history events, render `policy_federation_history.html`
2. `GET /federation/plans/{federation_id}/timeline` — get timeline, render `policy_federation_timeline.html`
3. `GET /federation/analytics` — show analytics form, render `policy_federation_analytics.html`
4. `POST /federation/analytics` — generate report with window, render `policy_federation_analytics.html`

Add `federation_observability_service` parameter to `build_policy_console_router()`.

Update federation plan detail template link to include history/timeline links.

- [ ] **Step 4: Create templates**

Create three HTML templates following existing `policy_federation_*.html` patterns:

1. `policy_federation_history.html` — table of history events (event_type, target_id, wave_id, created_at, message)
2. `policy_federation_timeline.html` — timeline view with waves, targets, events sections
3. `policy_federation_analytics.html` — analytics cards + tables (target health, wave outcomes, conflict summary, environment/region/tenant summaries)

- [ ] **Step 5: Wire FastAPI adapter**

In `agent_app/adapters/fastapi.py`, add `getattr(app, "federation_observability_service", None)` to `build_policy_console_router()` call.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_policy_rollout_federation_history_console.py -v`
Expected: PASS or SKIP

- [ ] **Step 7: Commit**

```bash
git add agent_app/console/router.py agent_app/adapters/fastapi.py agent_app/console/templates/policy_federation_history.html agent_app/console/templates/policy_federation_timeline.html agent_app/console/templates/policy_federation_analytics.html tests/unit/test_policy_rollout_federation_history_console.py
git commit -m "feat: Phase 47 Task 8 — console federation history/timeline/analytics pages"
```

---

### Task 9: Documentation and final verification

**Files:**
- Modify: `docs/policy_release.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Create: `docs/release_checklist_phase47.md`

- [ ] **Step 1: Update docs/policy_release.md**

Add Phase 47 section documenting:
1. Federation history purpose
2. Federation timeline model
3. Federation analytics report
4. Target health summary
5. Wave outcome summary
6. Conflict summary
7. CLI workflows
8. Console workflow
9. Export format
10. Known limitations

- [ ] **Step 2: Update CHANGELOG.md**

Add `## v0.35.0` entry at top:

```
## v0.35.0

### Phase 47: Policy Rollout Federation Observability and Reporting

- Add `FederationHistoryEventType` and `FederationHistoryEvent` models
- Add `FederationTargetTimeline`, `FederationWaveTimeline`, `FederationTimeline` models
- Add `FederationTargetHealthSummary`, `FederationWaveOutcomeSummary`, `FederationConflictSummary` models
- Add `FederationAnalyticsReport` model
- Add `FederationHistoryStore` (InMemory + SQLite)
- Add `FederationHistoryRecorder`
- Add `FederationObservabilityService` (timeline, analytics, list_history_events)
- Integrate recorder with `RolloutFederationService` and `PolicyNotificationService`
- Add federation export helpers (JSON, CSV)
- Add 3 RBAC permissions: FEDERATION_HISTORY_VIEW, FEDERATION_ANALYTICS_VIEW, FEDERATION_ANALYTICS_EXPORT
- Add 7 change event types (81 → 88)
- Add CLI federation history/timeline/analytics/export commands
- Add console federation history/timeline/analytics pages
```

- [ ] **Step 3: Update README.md**

Add Phase 47 to the roadmap table.

- [ ] **Step 4: Create release checklist**

Create `docs/release_checklist_phase47.md` documenting verification steps, new files, modified files, and known limitations.

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/unit/ -v --timeout=120 -q 2>&1 | tail -20`
Expected: 0 failures

- [ ] **Step 6: Run Phase 46 backward compatibility tests**

Run: `pytest tests/unit/test_policy_rollout_federation*.py -v --timeout=60`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add docs/policy_release.md CHANGELOG.md README.md docs/release_checklist_phase47.md
git commit -m "docs: Phase 47 federation observability documentation"
```

---

### Task 10: Final verification

**Files:** None (verification only)

- [ ] **Step 1: Run all Phase 47-specific tests**

Run: `pytest tests/unit/test_policy_rollout_federation_history*.py -v --timeout=60`
Expected: PASS

- [ ] **Step 2: Run full policy regression suite**

Run: `pytest tests/unit/ -v --timeout=120 -q 2>&1 | tail -20`
Expected: 0 failures

- [ ] **Step 3: Run import boundary tests**

Run: `pytest tests/unit/test_import_boundaries.py -v` (if exists)
Expected: PASS

- [ ] **Step 4: Report results**

Report:
1. Modified files
2. New files
3. New tests
4. Full test result
5. Example CLI federation history flow
6. Example CLI federation analytics export flow
7. Example console flow
8. Current limitations
9. Phase 48 recommendation
