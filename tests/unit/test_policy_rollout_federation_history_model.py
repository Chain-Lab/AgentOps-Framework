"""Tests for policy_rollout_federation_history models."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Return a timezone-aware UTC datetime for use in required fields."""
    return datetime.now(timezone.utc)


# ===========================================================================
# FederationHistoryEventType
# ===========================================================================

class TestFederationHistoryEventType:
    """Tests for the FederationHistoryEventType enum."""

    def test_all_28_types_exist(self) -> None:
        expected = [
            "federation.created",
            "federation.started",
            "federation.completed",
            "federation.failed",
            "federation.cancelled",
            "federation.blocked",
            "federation.target.created",
            "federation.target.enabled",
            "federation.target.disabled",
            "federation.target_execution.started",
            "federation.target_execution.succeeded",
            "federation.target_execution.failed",
            "federation.target_execution.blocked",
            "federation.target_execution.skipped",
            "federation.target_execution.cancelled",
            "federation.wave.started",
            "federation.wave.succeeded",
            "federation.wave.failed",
            "federation.wave.blocked",
            "federation.conflict.detected",
            "federation.notification.created",
            "federation.notification.sent",
            "federation.notification.failed",
            "approval.created",
            "approval.approved",
            "approval.rejected",
            "approval.escalated",
            "approval.cancelled",
            "federation.escalation.worker_ticked",
            "federation.escalation.lock_skipped",
        ]
        # There should be exactly 30 enum members
        assert len(FederationHistoryEventType) == 30
        for value in expected:
            assert value in [e.value for e in FederationHistoryEventType]

    def test_value_format_is_dotted_string(self) -> None:
        for member in FederationHistoryEventType:
            assert "." in member.value, f"{member.name} value should contain a dot"

    def test_specific_enum_values(self) -> None:
        assert FederationHistoryEventType.FEDERATION_CREATED.value == "federation.created"
        assert FederationHistoryEventType.TARGET_CREATED.value == "federation.target.created"
        assert FederationHistoryEventType.WAVE_STARTED.value == "federation.wave.started"
        assert FederationHistoryEventType.CONFLICT_DETECTED.value == "federation.conflict.detected"
        assert FederationHistoryEventType.NOTIFICATION_SENT.value == "federation.notification.sent"


# ===========================================================================
# FederationHistoryEvent
# ===========================================================================

class TestFederationHistoryEvent:
    """Tests for the FederationHistoryEvent model."""

    def test_valid_creation_minimal(self) -> None:
        event = FederationHistoryEvent(
            history_event_id="fhe_001",
            event_type=FederationHistoryEventType.FEDERATION_CREATED,
            created_at=_now(),
        )
        assert event.history_event_id == "fhe_001"
        assert event.event_type == FederationHistoryEventType.FEDERATION_CREATED
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
        assert event.metadata == {}

    def test_valid_creation_all_fields(self) -> None:
        now = _now()
        event = FederationHistoryEvent(
            history_event_id="fhe_abc123",
            federation_id="frp_plan1",
            target_id="frt_target1",
            rollout_id="rlo_roll1",
            wave_id="frw_wave1",
            event_type=FederationHistoryEventType.TARGET_EXECUTION_SUCCEEDED,
            tenant_id="tenant-42",
            environment="production",
            ring_name="ring-2",
            region="us-east-1",
            actor_id="actor-admin",
            source_type="api",
            source_id="src-001",
            message="Target execution succeeded",
            metadata={"key": "value"},
            created_at=now,
        )
        assert event.federation_id == "frp_plan1"
        assert event.target_id == "frt_target1"
        assert event.rollout_id == "rlo_roll1"
        assert event.wave_id == "frw_wave1"
        assert event.tenant_id == "tenant-42"
        assert event.environment == "production"
        assert event.ring_name == "ring-2"
        assert event.region == "us-east-1"
        assert event.actor_id == "actor-admin"
        assert event.source_type == "api"
        assert event.source_id == "src-001"
        assert event.message == "Target execution succeeded"
        assert event.metadata == {"key": "value"}
        assert event.created_at == now

    def test_history_event_id_requires_fhe_prefix(self) -> None:
        with pytest.raises(ValidationError, match="fhe_"):
            FederationHistoryEvent(
                history_event_id="bad_id",
                event_type=FederationHistoryEventType.FEDERATION_CREATED,
                created_at=_now(),
            )

    def test_created_at_requires_timezone(self) -> None:
        naive_dt = datetime(2026, 1, 1, 12, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederationHistoryEvent(
                history_event_id="fhe_001",
                event_type=FederationHistoryEventType.FEDERATION_CREATED,
                created_at=naive_dt,
            )

    def test_metadata_defaults_to_empty_dict(self) -> None:
        event = FederationHistoryEvent(
            history_event_id="fhe_001",
            event_type=FederationHistoryEventType.FEDERATION_STARTED,
            created_at=_now(),
        )
        assert event.metadata == {}
        # Ensure it's a new dict each time (not shared)
        event2 = FederationHistoryEvent(
            history_event_id="fhe_002",
            event_type=FederationHistoryEventType.FEDERATION_STARTED,
            created_at=_now(),
        )
        event.metadata["x"] = 1
        assert "x" not in event2.metadata


# ===========================================================================
# FederationTargetTimeline
# ===========================================================================

class TestFederationTargetTimeline:
    """Tests for the FederationTargetTimeline model."""

    def test_minimal_creation(self) -> None:
        tl = FederationTargetTimeline(target_id="frt_001")
        assert tl.target_id == "frt_001"
        assert tl.rollout_id is None
        assert tl.tenant_id is None
        assert tl.environment is None
        assert tl.ring_name is None
        assert tl.region is None
        assert tl.status is None
        assert tl.started_at is None
        assert tl.completed_at is None
        assert tl.duration_seconds is None
        assert tl.events == []
        assert tl.metadata == {}

    def test_full_creation(self) -> None:
        now = _now()
        event = FederationHistoryEvent(
            history_event_id="fhe_001",
            event_type=FederationHistoryEventType.TARGET_ENABLED,
            created_at=now,
        )
        tl = FederationTargetTimeline(
            target_id="frt_001",
            rollout_id="rlo_001",
            tenant_id="tenant-1",
            environment="staging",
            ring_name="ring-1",
            region="eu-west-1",
            status="succeeded",
            started_at=now,
            completed_at=now + timedelta(seconds=120),
            duration_seconds=120.0,
            events=[event],
            metadata={"key": "val"},
        )
        assert tl.target_id == "frt_001"
        assert tl.rollout_id == "rlo_001"
        assert tl.tenant_id == "tenant-1"
        assert tl.environment == "staging"
        assert tl.ring_name == "ring-1"
        assert tl.region == "eu-west-1"
        assert tl.status == "succeeded"
        assert tl.started_at == now
        assert tl.duration_seconds == 120.0
        assert len(tl.events) == 1
        assert tl.metadata == {"key": "val"}


# ===========================================================================
# FederationWaveTimeline
# ===========================================================================

class TestFederationWaveTimeline:
    """Tests for the FederationWaveTimeline model."""

    def test_minimal_creation(self) -> None:
        wt = FederationWaveTimeline(wave_id="frw_001")
        assert wt.wave_id == "frw_001"
        assert wt.name is None
        assert wt.status is None
        assert wt.target_ids == []
        assert wt.started_at is None
        assert wt.completed_at is None
        assert wt.duration_seconds is None
        assert wt.target_timelines == []
        assert wt.events == []

    def test_with_target_timelines(self) -> None:
        now = _now()
        target_tl = FederationTargetTimeline(
            target_id="frt_001",
            status="succeeded",
            started_at=now,
        )
        event = FederationHistoryEvent(
            history_event_id="fhe_001",
            event_type=FederationHistoryEventType.WAVE_STARTED,
            created_at=now,
        )
        wt = FederationWaveTimeline(
            wave_id="frw_001",
            name="Wave 1",
            status="succeeded",
            target_ids=["frt_001"],
            started_at=now,
            completed_at=now + timedelta(seconds=300),
            duration_seconds=300.0,
            target_timelines=[target_tl],
            events=[event],
        )
        assert wt.name == "Wave 1"
        assert wt.status == "succeeded"
        assert wt.target_ids == ["frt_001"]
        assert len(wt.target_timelines) == 1
        assert len(wt.events) == 1
        assert wt.duration_seconds == 300.0


# ===========================================================================
# FederationTimeline
# ===========================================================================

class TestFederationTimeline:
    """Tests for the FederationTimeline model."""

    def test_minimal_creation(self) -> None:
        ft = FederationTimeline(federation_id="frp_001")
        assert ft.federation_id == "frp_001"
        assert ft.name is None
        assert ft.bundle_id is None
        assert ft.strategy is None
        assert ft.status is None
        assert ft.created_at is None
        assert ft.started_at is None
        assert ft.completed_at is None
        assert ft.duration_seconds is None
        assert ft.waves == []
        assert ft.targets == []
        assert ft.events == []
        assert ft.conflicts == []
        assert ft.metadata == {}

    def test_full_creation(self) -> None:
        now = _now()
        event = FederationHistoryEvent(
            history_event_id="fhe_001",
            event_type=FederationHistoryEventType.FEDERATION_STARTED,
            federation_id="frp_001",
            created_at=now,
        )
        target_tl = FederationTargetTimeline(
            target_id="frt_001",
            status="succeeded",
            started_at=now,
        )
        wave_tl = FederationWaveTimeline(
            wave_id="frw_001",
            name="Wave 1",
            status="succeeded",
            target_timelines=[target_tl],
        )
        conflict = {
            "conflict_id": "frc_001",
            "severity": "error",
            "type": "concurrent_rollout",
            "message": "Concurrent rollout detected",
        }
        ft = FederationTimeline(
            federation_id="frp_001",
            name="Q2 Rollout",
            bundle_id="bundle-42",
            strategy="wave",
            status="completed",
            created_at=now,
            started_at=now,
            completed_at=now + timedelta(seconds=600),
            duration_seconds=600.0,
            waves=[wave_tl],
            targets=[target_tl],
            events=[event],
            conflicts=[conflict],
            metadata={"source": "api"},
        )
        assert ft.name == "Q2 Rollout"
        assert ft.bundle_id == "bundle-42"
        assert ft.strategy == "wave"
        assert ft.status == "completed"
        assert len(ft.waves) == 1
        assert len(ft.targets) == 1
        assert len(ft.events) == 1
        assert len(ft.conflicts) == 1
        assert ft.conflicts[0]["conflict_id"] == "frc_001"
        assert ft.metadata == {"source": "api"}


# ===========================================================================
# FederationTargetHealthSummary
# ===========================================================================

class TestFederationTargetHealthSummary:
    """Tests for the FederationTargetHealthSummary model."""

    def test_defaults_to_zeros(self) -> None:
        s = FederationTargetHealthSummary()
        assert s.total_targets == 0
        assert s.enabled_targets == 0
        assert s.disabled_targets == 0
        assert s.succeeded_targets == 0
        assert s.failed_targets == 0
        assert s.blocked_targets == 0
        assert s.skipped_targets == 0

    def test_with_values(self) -> None:
        s = FederationTargetHealthSummary(
            total_targets=10,
            enabled_targets=8,
            disabled_targets=2,
            succeeded_targets=6,
            failed_targets=1,
            blocked_targets=1,
            skipped_targets=2,
        )
        assert s.total_targets == 10
        assert s.enabled_targets == 8
        assert s.failed_targets == 1


# ===========================================================================
# FederationWaveOutcomeSummary
# ===========================================================================

class TestFederationWaveOutcomeSummary:
    """Tests for the FederationWaveOutcomeSummary model."""

    def test_defaults_to_zeros(self) -> None:
        s = FederationWaveOutcomeSummary()
        assert s.total_waves == 0
        assert s.succeeded_waves == 0
        assert s.failed_waves == 0
        assert s.blocked_waves == 0
        assert s.pending_waves == 0

    def test_with_values(self) -> None:
        s = FederationWaveOutcomeSummary(
            total_waves=5,
            succeeded_waves=3,
            failed_waves=1,
            blocked_waves=0,
            pending_waves=1,
        )
        assert s.total_waves == 5
        assert s.succeeded_waves == 3


# ===========================================================================
# FederationConflictSummary
# ===========================================================================

class TestFederationConflictSummary:
    """Tests for the FederationConflictSummary model."""

    def test_defaults_to_zeros(self) -> None:
        s = FederationConflictSummary()
        assert s.total_conflicts == 0
        assert s.error_conflicts == 0
        assert s.warning_conflicts == 0
        assert s.by_type == []

    def test_with_values(self) -> None:
        s = FederationConflictSummary(
            total_conflicts=3,
            error_conflicts=2,
            warning_conflicts=1,
            by_type=[{"type": "concurrent_rollout", "count": 2}],
        )
        assert s.total_conflicts == 3
        assert s.error_conflicts == 2
        assert len(s.by_type) == 1


# ===========================================================================
# FederationAnalyticsReport
# ===========================================================================

class TestFederationAnalyticsReport:
    """Tests for the FederationAnalyticsReport model."""

    def test_minimal_creation(self) -> None:
        report = FederationAnalyticsReport(
            report_id="far_001",
            generated_at=_now(),
        )
        assert report.report_id == "far_001"
        assert report.total_federations == 0
        assert report.active_federations == 0
        assert report.completed_federations == 0
        assert report.failed_federations == 0
        assert report.cancelled_federations == 0
        assert report.blocked_federations == 0
        assert isinstance(report.target_health, FederationTargetHealthSummary)
        assert isinstance(report.wave_outcomes, FederationWaveOutcomeSummary)
        assert isinstance(report.conflicts, FederationConflictSummary)
        assert report.top_failed_targets == []
        assert report.top_blocked_targets == []
        assert report.environment_summary == []
        assert report.region_summary == []
        assert report.tenant_summary == []
        assert report.metadata == {}

    def test_report_id_requires_far_prefix(self) -> None:
        with pytest.raises(ValidationError, match="far_"):
            FederationAnalyticsReport(
                report_id="bad_id",
                generated_at=_now(),
            )

    def test_generated_at_requires_timezone(self) -> None:
        naive_dt = datetime(2026, 1, 1, 12, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederationAnalyticsReport(
                report_id="far_001",
                generated_at=naive_dt,
            )

    def test_full_creation_with_all_summaries(self) -> None:
        now = _now()
        target_health = FederationTargetHealthSummary(
            total_targets=20,
            enabled_targets=15,
            disabled_targets=5,
            succeeded_targets=12,
            failed_targets=2,
            blocked_targets=1,
            skipped_targets=5,
        )
        wave_outcomes = FederationWaveOutcomeSummary(
            total_waves=8,
            succeeded_waves=6,
            failed_waves=1,
            blocked_waves=0,
            pending_waves=1,
        )
        conflict_summary = FederationConflictSummary(
            total_conflicts=4,
            error_conflicts=3,
            warning_conflicts=1,
            by_type=[{"type": "concurrent_rollout", "count": 3}],
        )
        report = FederationAnalyticsReport(
            report_id="far_report1",
            generated_at=now,
            window_start=now - timedelta(days=7),
            window_end=now,
            total_federations=10,
            active_federations=3,
            completed_federations=5,
            failed_federations=1,
            cancelled_federations=0,
            blocked_federations=1,
            target_health=target_health,
            wave_outcomes=wave_outcomes,
            conflicts=conflict_summary,
            top_failed_targets=[{"target_id": "frt_001", "fail_count": 3}],
            top_blocked_targets=[{"target_id": "frt_002", "block_count": 2}],
            environment_summary=[{"environment": "production", "count": 5}],
            region_summary=[{"region": "us-east-1", "count": 7}],
            tenant_summary=[{"tenant_id": "t-1", "count": 3}],
            metadata={"generated_by": "scheduler"},
        )
        assert report.total_federations == 10
        assert report.active_federations == 3
        assert report.completed_federations == 5
        assert report.target_health.total_targets == 20
        assert report.wave_outcomes.total_waves == 8
        assert report.conflicts.total_conflicts == 4
        assert len(report.top_failed_targets) == 1
        assert len(report.top_blocked_targets) == 1
        assert len(report.environment_summary) == 1
        assert len(report.region_summary) == 1
        assert len(report.tenant_summary) == 1
        assert report.metadata == {"generated_by": "scheduler"}


# ===========================================================================
# Federation Export Helpers
# ===========================================================================

class TestFederationExportHelpers:
    """Tests for federation export helpers."""

    def test_timeline_to_json(self) -> None:
        from agent_app.runtime.policy_compliance_export import federation_timeline_to_json
        tl = FederationTimeline(federation_id="frp_1", name="Test")
        result = federation_timeline_to_json(tl)
        assert isinstance(result, str)
        assert "frp_1" in result
        assert "Test" in result

    def test_analytics_report_to_json(self) -> None:
        from agent_app.runtime.policy_compliance_export import federation_analytics_report_to_json
        report = FederationAnalyticsReport(
            report_id="far_1",
            generated_at=datetime.now(timezone.utc),
            total_federations=5,
        )
        result = federation_analytics_report_to_json(report)
        assert isinstance(result, str)
        assert "far_1" in result

    def test_analytics_report_to_csv_rows(self) -> None:
        from agent_app.runtime.policy_compliance_export import federation_analytics_report_to_csv_rows
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
        sections = [r.get("section") for r in rows]
        assert "summary" in sections
        assert "target_health" in sections
