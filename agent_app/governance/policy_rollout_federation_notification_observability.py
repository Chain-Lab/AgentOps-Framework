"""Federation notification observability models — delivery events, metrics, channel health, SLA, and alerting.

Phase 52 Task 1: Observability domain models.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Sensitive field keys to redact before storage
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = {
    "authorization",
    "token",
    "secret",
    "password",
    "api_key",
    "x-signature",
    "x-signature-key",
    "x-api-key",
    "x-secret",
    "x-auth-token",
    "x-webhook-secret",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "www-authenticate",
    "signature",
    "key",
    "private_key",
    "access_key",
}


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NotificationDeliveryEventType(StrEnum):
    """Delivery lifecycle events for a federation notification."""

    CREATED = "created"
    QUEUED = "queued"
    RENDERED = "rendered"
    SUPPRESSED = "suppressed"
    SEND_ATTEMPTED = "send_attempted"
    SENT = "sent"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    DLQ_CREATED = "dlq_created"
    DLQ_REPLAYED = "dlq_replayed"
    WEBHOOK_SIGNATURE_FAILED = "webhook_signature_failed"
    TEMPLATE_FAILED = "template_failed"


class ChannelHealthStatus(StrEnum):
    """Channel health states for observability monitoring."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class NotificationDeliveryEvent(BaseModel):
    """A delivery lifecycle event for a federation notification."""

    event_id: str = Field(..., description="Unique event identifier (nde_ prefix)")
    notification_id: str | None = Field(default=None, description="Related notification ID (fn_ prefix)")
    approval_id: str | None = Field(default=None, description="Related approval ID")
    federation_id: str | None = Field(default=None, description="Related federation ID")
    channel: str | None = Field(default=None, description="Notification channel")
    event_type: NotificationDeliveryEventType = Field(..., description="Type of delivery event")
    status: str | None = Field(default=None, description="Delivery status")
    attempt: int | None = Field(default=None, description="Delivery attempt number")
    latency_ms: int | None = Field(default=None, description="Delivery latency in milliseconds")
    error_code: str | None = Field(default=None, description="Error code if delivery failed")
    error_message: str | None = Field(default=None, description="Error message — sensitive values are redacted")
    adapter_name: str | None = Field(default=None, description="Adapter that handled the delivery")
    template_id: str | None = Field(default=None, description="Template used for rendering")
    preference_decision: str | None = Field(default=None, description="Preference routing decision")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata — sensitive fields redacted")
    created_at: datetime = Field(..., description="Timezone-aware event timestamp")

    @field_validator("event_id")
    @classmethod
    def _validate_event_id_prefix(cls, v: str) -> str:
        if not v.startswith("nde_"):
            raise ValueError(f"ID must start with 'nde_', got '{v}'")
        return v

    @field_validator("notification_id")
    @classmethod
    def _validate_notification_id_prefix(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("fn_"):
            raise ValueError(f"notification_id must start with 'fn_', got '{v}'")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v

    @model_validator(mode="after")
    def _sanitize_sensitive_fields(self) -> "NotificationDeliveryEvent":
        """Remove sensitive values from error_message and metadata."""
        if self.error_message is not None:
            self.error_message = _redact_sensitive_values(self.error_message)

        if self.metadata:
            self.metadata = {
                k: _redact_sensitive_values(v) if isinstance(v, str) else v
                for k, v in self.metadata.items()
            }
            # Drop any keys that are themselves sensitive
            sanitized_keys = {k for k in self.metadata if k.lower() in _SENSITIVE_KEYS}
            for key in sanitized_keys:
                self.metadata[key] = "[REDACTED]"

        return self


class NotificationMetricWindow(BaseModel):
    """Aggregated metrics over a time window for a channel or federation."""

    window_start: datetime = Field(..., description="Timezone-aware window start")
    window_end: datetime = Field(..., description="Timezone-aware window end")
    federation_id: str | None = Field(default=None, description="Related federation ID")
    channel: str | None = Field(default=None, description="Notification channel")
    total: int = Field(default=0, description="Total notifications in window")
    sent: int = Field(default=0, description="Successfully sent notifications")
    failed: int = Field(default=0, description="Failed notifications")
    suppressed: int = Field(default=0, description="Suppressed notifications")
    dlq: int = Field(default=0, description="Notifications sent to DLQ")
    retry_scheduled: int = Field(default=0, description="Notifications with retry scheduled")
    success_rate: float = Field(default=0.0, description="Fraction of notifications sent successfully")
    failure_rate: float = Field(default=0.0, description="Fraction of notifications that failed")
    dlq_rate: float = Field(default=0.0, description="Fraction of notifications sent to DLQ")
    avg_latency_ms: float | None = Field(default=None, description="Average delivery latency in ms")
    p95_latency_ms: float | None = Field(default=None, description="P95 delivery latency in ms")

    @field_validator("window_start")
    @classmethod
    def _validate_window_start_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("window_start must be timezone-aware")
        return v

    @field_validator("window_end")
    @classmethod
    def _validate_window_end_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("window_end must be timezone-aware")
        return v


class ChannelHealthSnapshot(BaseModel):
    """Point-in-time health snapshot for a notification channel."""

    channel: str = Field(..., description="Notification channel name")
    status: ChannelHealthStatus = Field(..., description="Current health status")
    window_start: datetime = Field(..., description="Timezone-aware observation window start")
    window_end: datetime = Field(..., description="Timezone-aware observation window end")
    total: int = Field(default=0, description="Total notifications in window")
    success_rate: float = Field(default=0.0, description="Fraction of notifications sent successfully")
    failure_rate: float = Field(default=0.0, description="Fraction of notifications that failed")
    dlq_rate: float = Field(default=0.0, description="Fraction of notifications sent to DLQ")
    avg_latency_ms: float | None = Field(default=None, description="Average delivery latency in ms")
    reason: str | None = Field(default=None, description="Reason for current health status")
    created_at: datetime = Field(..., description="Timezone-aware snapshot timestamp")

    @field_validator("window_start")
    @classmethod
    def _validate_window_start_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("window_start must be timezone-aware")
        return v

    @field_validator("window_end")
    @classmethod
    def _validate_window_end_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("window_end must be timezone-aware")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class NotificationChannelSlaOverride(BaseModel):
    """Per-channel SLA threshold overrides."""

    max_delivery_latency_ms: int | None = Field(default=None, description="Max delivery latency in ms")
    min_success_rate: float | None = Field(default=None, description="Minimum success rate (0.0-1.0)")
    max_failure_rate: float | None = Field(default=None, description="Maximum failure rate (0.0-1.0)")
    max_dlq_rate: float | None = Field(default=None, description="Maximum DLQ rate (0.0-1.0)")
    window_minutes: int | None = Field(default=None, description="Evaluation window in minutes")


class NotificationSlaPolicy(BaseModel):
    """SLA policy configuration for notification delivery."""

    enabled: bool = Field(default=True, description="Whether SLA monitoring is enabled")
    max_delivery_latency_ms: int = Field(default=30000, description="Max delivery latency in ms")
    min_success_rate: float = Field(default=0.95, description="Minimum success rate (0.0-1.0)")
    max_failure_rate: float = Field(default=0.05, description="Maximum failure rate (0.0-1.0)")
    max_dlq_rate: float = Field(default=0.01, description="Maximum DLQ rate (0.0-1.0)")
    window_minutes: int = Field(default=60, description="Evaluation window in minutes")
    channels: dict[str, NotificationChannelSlaOverride] = Field(
        default_factory=dict, description="Per-channel SLA overrides"
    )


class NotificationSlaViolation(BaseModel):
    """A recorded SLA policy violation."""

    violation_id: str = Field(..., description="Unique violation identifier (nsv_ prefix)")
    federation_id: str | None = Field(default=None, description="Related federation ID")
    channel: str | None = Field(default=None, description="Notification channel")
    metric: str = Field(..., description="Metric that was violated")
    observed_value: float = Field(..., description="Observed metric value")
    threshold: float = Field(..., description="SLA threshold value")
    severity: str = Field(..., description="Violation severity — 'warning' or 'critical'")
    window_start: datetime = Field(..., description="Timezone-aware violation window start")
    window_end: datetime = Field(..., description="Timezone-aware violation window end")
    message: str = Field(..., description="Human-readable violation description")
    created_at: datetime = Field(..., description="Timezone-aware violation timestamp")

    @field_validator("violation_id")
    @classmethod
    def _validate_violation_id_prefix(cls, v: str) -> str:
        if not v.startswith("nsv_"):
            raise ValueError(f"ID must start with 'nsv_', got '{v}'")
        return v

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, v: str) -> str:
        if v not in ("warning", "critical"):
            raise ValueError(f"severity must be 'warning' or 'critical', got '{v}'")
        return v

    @field_validator("window_start")
    @classmethod
    def _validate_window_start_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("window_start must be timezone-aware")
        return v

    @field_validator("window_end")
    @classmethod
    def _validate_window_end_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("window_end must be timezone-aware")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class NotificationAlertRule(BaseModel):
    """A rule that generates alerts when a metric crosses a threshold."""

    rule_id: str = Field(..., description="Unique rule identifier (nar_ prefix)")
    name: str = Field(..., description="Human-readable rule name")
    enabled: bool = Field(default=True, description="Whether the rule is active")
    metric: str = Field(..., description="Metric to monitor")
    operator: Literal[">", ">=", "<", "<=", "=="] = Field(..., description="Comparison operator")
    threshold: float = Field(..., description="Threshold value for the metric")
    severity: Literal["info", "warning", "critical"] = Field(
        default="warning", description="Alert severity level"
    )
    channel: str | None = Field(default=None, description="Channel to filter on")
    federation_id: str | None = Field(default=None, description="Federation to filter on")
    window_minutes: int = Field(default=60, description="Evaluation window in minutes")
    cooldown_minutes: int = Field(default=30, description="Cooldown between alerts in minutes")

    @field_validator("rule_id")
    @classmethod
    def _validate_rule_id_prefix(cls, v: str) -> str:
        if not v.startswith("nar_"):
            raise ValueError(f"ID must start with 'nar_', got '{v}'")
        return v


class NotificationAlertEvent(BaseModel):
    """A fired alert event generated by a NotificationAlertRule."""

    alert_id: str = Field(..., description="Unique alert identifier (nae_ prefix)")
    rule_id: str = Field(..., description="ID of the rule that fired")
    name: str = Field(..., description="Human-readable alert name")
    severity: str = Field(..., description="Alert severity")
    metric: str = Field(..., description="Metric that triggered the alert")
    observed_value: float = Field(..., description="Observed metric value")
    threshold: float = Field(..., description="Threshold that was crossed")
    federation_id: str | None = Field(default=None, description="Related federation ID")
    channel: str | None = Field(default=None, description="Related channel")
    message: str = Field(..., description="Human-readable alert description")
    status: Literal["open", "acknowledged", "resolved"] = Field(
        default="open", description="Current alert status"
    )
    created_at: datetime = Field(..., description="Timezone-aware alert creation timestamp")
    acknowledged_at: datetime | None = Field(default=None, description="Timezone-aware acknowledgment timestamp")
    acknowledged_by: str | None = Field(default=None, description="User who acknowledged the alert")
    resolved_at: datetime | None = Field(default=None, description="Timezone-aware resolution timestamp")
    resolved_by: str | None = Field(default=None, description="User who resolved the alert")

    @field_validator("alert_id")
    @classmethod
    def _validate_alert_id_prefix(cls, v: str) -> str:
        if not v.startswith("nae_"):
            raise ValueError(f"ID must start with 'nae_', got '{v}'")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v

    @field_validator("acknowledged_at")
    @classmethod
    def _validate_acknowledged_at_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("acknowledged_at must be timezone-aware")
        return v

    @field_validator("resolved_at")
    @classmethod
    def _validate_resolved_at_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("resolved_at must be timezone-aware")
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact_sensitive_values(value: str) -> str:
    """Replace sensitive values in a string with [REDACTED]."""
    result = value
    for key in _SENSITIVE_KEYS:
        # Match patterns like "key=value", "key: value", "key = value"
        for sep in ("=", ":", " "):
            pattern = f"{key}{sep}"
            if pattern.lower() in result.lower():
                # Replace the value after the key+sep with [REDACTED]
                result = _redact_after_key(result, key, sep)
                break
    return result


def _redact_after_key(text: str, key: str, sep: str) -> str:
    """Redact the value after a key-separator pair in a string."""
    lower_text = text.lower()
    lower_key = key.lower()
    idx = lower_text.find(lower_key)
    if idx == -1:
        return text

    # Find the separator position after the key
    sep_idx = idx + len(lower_key)
    if sep_idx >= len(lower_text):
        return text

    actual_sep = text[sep_idx]
    if actual_sep.lower() != sep.lower():
        return text

    # Find the end of the value (next separator or end of string)
    start_value = sep_idx + 1
    # Look for next separator (comma, semicolon, pipe, newline, or end)
    end_delimiters = {",", ";", "|", "\n", "\r"}
    end_value = len(text)
    for i in range(start_value, len(text)):
        if text[i] in end_delimiters:
            end_value = i
            break

    redacted = text[:start_value] + "[REDACTED]" + text[end_value:]
    return redacted
