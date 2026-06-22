"""Federation history models — event types, history events, timelines, and analytics reports.

Phase 47: Federation observability — history tracking, timeline reconstruction, and analytics.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FederationHistoryEventType(str, Enum):
    """Types of events recorded during a federated rollout."""

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
    APPROVAL_CREATED = "approval.created"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_ESCALATED = "approval.escalated"
    APPROVAL_CANCELLED = "approval.cancelled"
    ESCALATION_WORKER_TICKED = "federation.escalation.worker_ticked"
    ESCALATION_LOCK_SKIPPED = "federation.escalation.lock_skipped"
    NOTIFICATION_DLQ_CREATED = "federation.notification.dlq_created"
    NOTIFICATION_DLQ_RETRIED = "federation.notification.dlq_retried"
    SCHEDULED_WORKER_TICK = "federation.scheduled.worker_ticked"
    NOTIFICATION_TEMPLATE_CHANGED = "federation.notification.template_changed"
    NOTIFICATION_PREFERENCE_CHANGED = "federation.notification.preference_changed"
    WEBHOOK_REPLAY = "federation.webhook.replay"
    NOTIFICATION_DELIVERY_EVENT_RECORDED = "notification.delivery.event_recorded"
    NOTIFICATION_SLA_VIOLATION_DETECTED = "notification.sla.violation_detected"
    NOTIFICATION_ALERT_CREATED = "notification.alert.created"
    NOTIFICATION_ALERT_ACKNOWLEDGED = "notification.alert.acknowledged"
    NOTIFICATION_ALERT_RESOLVED = "notification.alert.resolved"
    NOTIFICATION_OBSERVABILITY_REPORT_EXPORTED = "notification.observability.report_exported"


class FederationHistoryEvent(BaseModel):
    """A single event recorded during a federated rollout."""

    history_event_id: str = Field(..., description="Unique event identifier (fhe_ prefix)")
    federation_id: str | None = Field(default=None, description="Related federation ID")
    target_id: str | None = Field(default=None, description="Related target ID")
    rollout_id: str | None = Field(default=None, description="Related rollout ID")
    wave_id: str | None = Field(default=None, description="Related wave ID")
    event_type: FederationHistoryEventType = Field(..., description="Type of federation history event")
    tenant_id: str | None = Field(default=None, description="Affected tenant ID")
    environment: str | None = Field(default=None, description="Affected environment")
    ring_name: str | None = Field(default=None, description="Affected ring name")
    region: str | None = Field(default=None, description="Affected region")
    actor_id: str | None = Field(default=None, description="ID of the actor who triggered the event")
    source_type: str | None = Field(default=None, description="Source type (e.g. federation, wave, target)")
    source_id: str | None = Field(default=None, description="Source entity ID")
    message: str | None = Field(default=None, description="Human-readable event message")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional event metadata")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")

    @field_validator("history_event_id")
    @classmethod
    def _validate_id_prefix(cls, v: str) -> str:
        if not v.startswith("fhe_"):
            raise ValueError(f"ID must start with 'fhe_', got '{v}'")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class FederationTargetTimeline(BaseModel):
    """Timeline view for a single federation target, with associated events."""

    target_id: str = Field(..., description="Target identifier")
    rollout_id: str | None = Field(default=None, description="Related rollout ID")
    tenant_id: str | None = Field(default=None, description="Target tenant ID")
    environment: str | None = Field(default=None, description="Target environment")
    ring_name: str | None = Field(default=None, description="Target ring name")
    region: str | None = Field(default=None, description="Target region")
    status: str | None = Field(default=None, description="Target status")
    started_at: datetime | None = Field(default=None, description="Target start timestamp")
    completed_at: datetime | None = Field(default=None, description="Target completion timestamp")
    duration_seconds: float | None = Field(default=None, description="Target duration in seconds")
    events: list[FederationHistoryEvent] = Field(default_factory=list, description="Events for this target")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional target metadata")


class FederationWaveTimeline(BaseModel):
    """Timeline view for a single federation wave, with target timelines and events."""

    wave_id: str = Field(..., description="Wave identifier")
    name: str | None = Field(default=None, description="Wave name")
    status: str | None = Field(default=None, description="Wave status")
    target_ids: list[str] = Field(default_factory=list, description="Target IDs in this wave")
    started_at: datetime | None = Field(default=None, description="Wave start timestamp")
    completed_at: datetime | None = Field(default=None, description="Wave completion timestamp")
    duration_seconds: float | None = Field(default=None, description="Wave duration in seconds")
    target_timelines: list[FederationTargetTimeline] = Field(default_factory=list, description="Target timelines in this wave")
    events: list[FederationHistoryEvent] = Field(default_factory=list, description="Events for this wave")


class FederationTimeline(BaseModel):
    """Timeline view for an entire federation, with waves, targets, and events."""

    federation_id: str = Field(..., description="Federation identifier")
    name: str | None = Field(default=None, description="Federation name")
    bundle_id: str | None = Field(default=None, description="Related policy bundle ID")
    strategy: str | None = Field(default=None, description="Federation execution strategy")
    status: str | None = Field(default=None, description="Federation status")
    created_at: datetime | None = Field(default=None, description="Federation creation timestamp")
    started_at: datetime | None = Field(default=None, description="Federation start timestamp")
    completed_at: datetime | None = Field(default=None, description="Federation completion timestamp")
    duration_seconds: float | None = Field(default=None, description="Federation duration in seconds")
    waves: list[FederationWaveTimeline] = Field(default_factory=list, description="Wave timelines")
    targets: list[FederationTargetTimeline] = Field(default_factory=list, description="Target timelines")
    events: list[FederationHistoryEvent] = Field(default_factory=list, description="Federation-level events")
    conflicts: list[dict[str, Any]] = Field(default_factory=list, description="Conflicts detected during federation")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional federation metadata")


class FederationTargetHealthSummary(BaseModel):
    """Summary of target health across a federation or set of federations."""

    total_targets: int = 0
    enabled_targets: int = 0
    disabled_targets: int = 0
    succeeded_targets: int = 0
    failed_targets: int = 0
    blocked_targets: int = 0
    skipped_targets: int = 0


class FederationWaveOutcomeSummary(BaseModel):
    """Summary of wave outcomes across a federation or set of federations."""

    total_waves: int = 0
    succeeded_waves: int = 0
    failed_waves: int = 0
    blocked_waves: int = 0
    pending_waves: int = 0


class FederationConflictSummary(BaseModel):
    """Summary of conflicts across a federation or set of federations."""

    total_conflicts: int = 0
    error_conflicts: int = 0
    warning_conflicts: int = 0
    by_type: list[dict[str, Any]] = Field(default_factory=list)


class FederationAnalyticsReport(BaseModel):
    """Aggregated analytics report for federation history."""

    report_id: str = Field(..., description="Unique report identifier (far_ prefix)")
    generated_at: datetime = Field(..., description="Timezone-aware report generation timestamp")
    window_start: datetime | None = Field(default=None, description="Report window start")
    window_end: datetime | None = Field(default=None, description="Report window end")
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
    def _validate_id_prefix(cls, v: str) -> str:
        if not v.startswith("far_"):
            raise ValueError(f"ID must start with 'far_', got '{v}'")
        return v

    @field_validator("generated_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("generated_at must be timezone-aware")
        return v
