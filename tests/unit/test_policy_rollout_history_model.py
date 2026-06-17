"""Tests for rollout history models — event types, history events, timelines, and analytics."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_rollout_history import (
    RolloutAnalyticsReport,
    RolloutApprovalOutcomeSummary,
    RolloutGateOutcomeSummary,
    RolloutHistoryEvent,
    RolloutHistoryEventType,
    RolloutStepTimeline,
    RolloutTimeline,
)


# ---------------------------------------------------------------------------
# 1. RolloutHistoryEventType — all 24 values
# ---------------------------------------------------------------------------


def test_rollout_history_event_type_values() -> None:
    """Verify all 24 enum values are present and correct."""
    expected = {
        "ROLLOUT_CREATED": "rollout.created",
        "ROLLOUT_STARTED": "rollout.started",
        "ROLLOUT_CANCELLED": "rollout.cancelled",
        "ROLLOUT_COMPLETED": "rollout.completed",
        "ROLLOUT_FAILED": "rollout.failed",
        "STEP_STARTED": "rollout.step.started",
        "STEP_SUCCEEDED": "rollout.step.succeeded",
        "STEP_BLOCKED": "rollout.step.blocked",
        "STEP_FAILED": "rollout.step.failed",
        "STEP_SKIPPED": "rollout.step.skipped",
        "APPROVAL_REQUESTED": "rollout.approval.requested",
        "APPROVAL_DECISION_RECORDED": "rollout.approval.decision_recorded",
        "APPROVAL_APPROVED": "rollout.approval.approved",
        "APPROVAL_REJECTED": "rollout.approval.rejected",
        "APPROVAL_EXPIRED": "rollout.approval.expired",
        "GATE_RUN": "rollout.gate.run",
        "GATE_SATISFIED": "rollout.gate.satisfied",
        "GATE_BLOCKED": "rollout.gate.blocked",
        "GATE_FAILED": "rollout.gate.failed",
        "GATE_SKIPPED": "rollout.gate.skipped",
        "GATE_EXPIRED": "rollout.gate.expired",
        "NOTIFICATION_CREATED": "rollout.notification.created",
        "NOTIFICATION_SENT": "rollout.notification.sent",
        "NOTIFICATION_FAILED": "rollout.notification.failed",
    }
    assert len(RolloutHistoryEventType) == 24
    for name, value in expected.items():
        assert RolloutHistoryEventType[name].value == value


# ---------------------------------------------------------------------------
# 2. Valid RolloutHistoryEvent
# ---------------------------------------------------------------------------


def test_valid_history_event() -> None:
    """Create a valid RolloutHistoryEvent with required fields."""
    now = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
    event = RolloutHistoryEvent(
        history_event_id="rhe_abc123",
        rollout_id="ro_001",
        event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
        step_id="step_1",
        environment="production",
        ring_name="canary",
        actor_id="user_1",
        source_type="rollout_step",
        source_id="step_1",
        message="Rollout started",
        metadata={"key": "value"},
        created_at=now,
    )
    assert event.history_event_id == "rhe_abc123"
    assert event.rollout_id == "ro_001"
    assert event.event_type == RolloutHistoryEventType.ROLLOUT_STARTED
    assert event.step_id == "step_1"
    assert event.environment == "production"
    assert event.ring_name == "canary"
    assert event.actor_id == "user_1"
    assert event.source_type == "rollout_step"
    assert event.source_id == "step_1"
    assert event.message == "Rollout started"
    assert event.metadata == {"key": "value"}
    assert event.created_at == now


# ---------------------------------------------------------------------------
# 3. history_event_id prefix — valid
# ---------------------------------------------------------------------------


def test_history_event_id_prefix() -> None:
    """Verify rhe_ prefix is accepted."""
    event = RolloutHistoryEvent(
        history_event_id="rhe_valid",
        rollout_id="ro_001",
        event_type=RolloutHistoryEventType.ROLLOUT_CREATED,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert event.history_event_id == "rhe_valid"


# ---------------------------------------------------------------------------
# 4. history_event_id prefix — invalid
# ---------------------------------------------------------------------------


def test_history_event_id_bad_prefix() -> None:
    """Verify ValueError for wrong prefix on history_event_id."""
    with pytest.raises(ValidationError, match="rhe_"):
        RolloutHistoryEvent(
            history_event_id="bad_prefix_123",
            rollout_id="ro_001",
            event_type=RolloutHistoryEventType.ROLLOUT_CREATED,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


# ---------------------------------------------------------------------------
# 5. created_at tz-aware — valid
# ---------------------------------------------------------------------------


def test_history_event_tz_aware() -> None:
    """Verify tz-aware created_at is accepted."""
    event = RolloutHistoryEvent(
        history_event_id="rhe_tz",
        rollout_id="ro_001",
        event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
        created_at=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert event.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# 6. created_at naive datetime — invalid
# ---------------------------------------------------------------------------


def test_history_event_naive_datetime() -> None:
    """Verify ValueError for naive datetime on created_at."""
    with pytest.raises(ValidationError, match="timezone-aware"):
        RolloutHistoryEvent(
            history_event_id="rhe_naive",
            rollout_id="ro_001",
            event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
            created_at=datetime(2026, 6, 16, 12, 0, 0),
        )


# ---------------------------------------------------------------------------
# 7. RolloutStepTimeline
# ---------------------------------------------------------------------------


def test_rollout_step_timeline() -> None:
    """Create a valid RolloutStepTimeline."""
    now = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
    step = RolloutStepTimeline(
        step_id="step_1",
        step_type="activate",
        environment="production",
        ring_name="canary",
        status="succeeded",
        started_at=now,
        completed_at=now,
        duration_seconds=0.0,
        gate_status="satisfied",
        approval_status="approved",
        error=None,
        events=[],
    )
    assert step.step_id == "step_1"
    assert step.step_type == "activate"
    assert step.environment == "production"
    assert step.ring_name == "canary"
    assert step.status == "succeeded"
    assert step.duration_seconds == 0.0
    assert step.gate_status == "satisfied"
    assert step.approval_status == "approved"
    assert step.error is None
    assert step.events == []


# ---------------------------------------------------------------------------
# 8. RolloutTimeline
# ---------------------------------------------------------------------------


def test_rollout_timeline() -> None:
    """Create a valid RolloutTimeline."""
    now = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
    event = RolloutHistoryEvent(
        history_event_id="rhe_tl1",
        rollout_id="ro_001",
        event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
        created_at=now,
    )
    step = RolloutStepTimeline(
        step_id="step_1",
        status="succeeded",
        events=[event],
    )
    timeline = RolloutTimeline(
        rollout_id="ro_001",
        name="My Rollout",
        bundle_id="pb_001",
        status="completed",
        created_at=now,
        started_at=now,
        completed_at=now,
        duration_seconds=10.5,
        steps=[step],
        events=[event],
    )
    assert timeline.rollout_id == "ro_001"
    assert timeline.name == "My Rollout"
    assert timeline.bundle_id == "pb_001"
    assert timeline.status == "completed"
    assert timeline.duration_seconds == 10.5
    assert len(timeline.steps) == 1
    assert len(timeline.events) == 1


# ---------------------------------------------------------------------------
# 9. RolloutGateOutcomeSummary
# ---------------------------------------------------------------------------


def test_gate_outcome_summary() -> None:
    """Create a valid RolloutGateOutcomeSummary."""
    summary = RolloutGateOutcomeSummary(
        total=10,
        satisfied=6,
        blocked=2,
        failed=1,
        skipped=1,
        expired=0,
    )
    assert summary.total == 10
    assert summary.satisfied == 6
    assert summary.blocked == 2
    assert summary.failed == 1
    assert summary.skipped == 1
    assert summary.expired == 0


# ---------------------------------------------------------------------------
# 10. RolloutApprovalOutcomeSummary
# ---------------------------------------------------------------------------


def test_approval_outcome_summary() -> None:
    """Create a valid RolloutApprovalOutcomeSummary."""
    summary = RolloutApprovalOutcomeSummary(
        total=5,
        pending=1,
        approved=3,
        rejected=1,
        expired=0,
        average_latency_seconds=120.5,
    )
    assert summary.total == 5
    assert summary.pending == 1
    assert summary.approved == 3
    assert summary.rejected == 1
    assert summary.expired == 0
    assert summary.average_latency_seconds == 120.5


# ---------------------------------------------------------------------------
# 11. RolloutAnalyticsReport — valid
# ---------------------------------------------------------------------------


def test_analytics_report() -> None:
    """Create a valid RolloutAnalyticsReport."""
    now = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
    report = RolloutAnalyticsReport(
        report_id="rar_report1",
        generated_at=now,
        window_start=now,
        window_end=now,
        total_rollouts=20,
        completed_rollouts=15,
        failed_rollouts=3,
        cancelled_rollouts=1,
        blocked_rollouts=1,
        gate_outcomes=RolloutGateOutcomeSummary(total=20, satisfied=15),
        approval_outcomes=RolloutApprovalOutcomeSummary(total=10, approved=8),
        top_blocked_steps=[{"step_id": "step_1", "count": 5}],
        top_failed_gates=[{"gate_id": "gate_1", "count": 3}],
        environment_summary=[{"environment": "production", "total": 10}],
        ring_summary=[{"ring": "canary", "total": 5}],
        metadata={"version": "1.0"},
    )
    assert report.report_id == "rar_report1"
    assert report.total_rollouts == 20
    assert report.completed_rollouts == 15
    assert report.failed_rollouts == 3
    assert report.cancelled_rollouts == 1
    assert report.blocked_rollouts == 1
    assert report.gate_outcomes.total == 20
    assert report.approval_outcomes.total == 10
    assert len(report.top_blocked_steps) == 1
    assert len(report.top_failed_gates) == 1
    assert len(report.environment_summary) == 1
    assert len(report.ring_summary) == 1
    assert report.metadata == {"version": "1.0"}


# ---------------------------------------------------------------------------
# 12. report_id prefix — valid
# ---------------------------------------------------------------------------


def test_analytics_report_id_prefix() -> None:
    """Verify rar_ prefix is accepted."""
    report = RolloutAnalyticsReport(
        report_id="rar_valid",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert report.report_id == "rar_valid"


# ---------------------------------------------------------------------------
# 13. report_id prefix — invalid
# ---------------------------------------------------------------------------


def test_analytics_report_bad_prefix() -> None:
    """Verify ValueError for wrong prefix on report_id."""
    with pytest.raises(ValidationError, match="rar_"):
        RolloutAnalyticsReport(
            report_id="bad_prefix_123",
            generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


# ---------------------------------------------------------------------------
# 14. generated_at tz-aware — valid
# ---------------------------------------------------------------------------


def test_analytics_report_tz_aware() -> None:
    """Verify tz-aware generated_at is accepted."""
    report = RolloutAnalyticsReport(
        report_id="rar_tz",
        generated_at=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert report.generated_at.tzinfo is not None
