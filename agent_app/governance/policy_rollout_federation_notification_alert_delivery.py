"""Federation notification alert delivery models — targets, attempts, retry policy.

Phase 53 Task 1: Alert delivery domain models.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Reuse sensitive key set from observability module
# ---------------------------------------------------------------------------

def _get_sensitive_keys() -> set[str]:
    """Lazy import to avoid circular deps."""
    from agent_app.governance.policy_rollout_federation_notification_observability import _SENSITIVE_KEYS
    return _SENSITIVE_KEYS


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AlertDeliveryChannelType(StrEnum):
    """Channel types for alert delivery."""

    MEMORY = "memory"
    WEBHOOK = "webhook"
    EMAIL = "email"
    SLACK = "slack"
    CONSOLE = "console"


class AlertDeliveryStatus(StrEnum):
    """Delivery attempt statuses."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    DLQ = "dlq"
    SUPPRESSED = "suppressed"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AlertDeliveryRetryPolicy(BaseModel):
    """Retry policy for alert delivery."""

    max_attempts: int = Field(default=3, description="Maximum delivery attempts before DLQ")
    base_delay_seconds: int = Field(default=60, description="Base delay between retries in seconds")
    max_delay_seconds: int = Field(default=3600, description="Maximum delay between retries in seconds")


class AlertDeliveryTarget(BaseModel):
    """A target for alert delivery."""

    target_id: str = Field(..., description="Unique target identifier (ndt_ prefix)")
    name: str = Field(..., description="Human-readable target name")
    channel_type: AlertDeliveryChannelType = Field(..., description="Delivery channel type")
    enabled: bool = Field(default=True, description="Whether target is active")
    severity_filter: list[str] = Field(default_factory=list, description="Empty = no filter")
    channel_filter: list[str] = Field(default_factory=list, description="Empty = no filter")
    federation_filter: list[str] = Field(default_factory=list, description="Empty = no filter")
    endpoint: str | None = Field(default=None, description="Delivery endpoint URL")
    headers: dict[str, str] = Field(default_factory=dict, description="Additional headers — sensitive fields redacted")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata — sensitive fields redacted")
    webhook_secret: str | None = Field(default=None, description="Secret for HMAC-SHA256 webhook signing")

    @field_validator("target_id")
    @classmethod
    def _validate_target_id_prefix(cls, v: str) -> str:
        if not v.startswith("ndt_"):
            raise ValueError(f"target_id must start with 'ndt_', got '{v}'")
        return v

    @model_validator(mode="after")
    def _sanitize_sensitive(self) -> "AlertDeliveryTarget":
        _SENSITIVE_KEYS = _get_sensitive_keys()
        if self.headers:
            self.headers = {
                k: "[REDACTED]" if k.lower() in _SENSITIVE_KEYS else v
                for k, v in self.headers.items()
            }
        if self.metadata:
            self.metadata = {
                k: "[REDACTED]" if k.lower() in _SENSITIVE_KEYS else v
                for k, v in self.metadata.items()
            }
        return self


class AlertDeliveryAttempt(BaseModel):
    """A single delivery attempt for an alert."""

    attempt_id: str = Field(..., description="Unique attempt identifier (nda_ prefix)")
    alert_id: str = Field(..., description="Alert being delivered")
    target_id: str = Field(..., description="Target being delivered to")
    channel_type: AlertDeliveryChannelType = Field(..., description="Channel used")
    status: AlertDeliveryStatus = Field(..., description="Current attempt status")
    attempt: int = Field(default=1, description="Attempt number (1-based)")
    next_retry_at: datetime | None = Field(default=None, description="When to retry (for RETRY_SCHEDULED)")
    error_code: str | None = Field(default=None, description="Error code if failed")
    error_message: str | None = Field(default=None, description="Error message — sensitive values redacted")
    payload_preview: dict[str, Any] = Field(default_factory=dict, description="Payload preview — sensitive values redacted")
    priority: int = Field(default=0, description="Delivery priority (higher = more urgent, derived from alert severity)")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    delivered_at: datetime | None = Field(default=None, description="Timezone-aware delivery timestamp")

    @field_validator("attempt_id")
    @classmethod
    def _validate_attempt_id_prefix(cls, v: str) -> str:
        if not v.startswith("nda_"):
            raise ValueError(f"attempt_id must start with 'nda_', got '{v}'")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v

    @field_validator("delivered_at")
    @classmethod
    def _validate_delivered_at_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("delivered_at must be timezone-aware")
        return v

    @model_validator(mode="after")
    def _sanitize_sensitive(self) -> "AlertDeliveryAttempt":
        _SENSITIVE_KEYS = _get_sensitive_keys()
        if self.error_message is not None:
            self.error_message = _redact_in_string(self.error_message)
        if self.payload_preview:
            self.payload_preview = {
                k: "[REDACTED]" if k.lower() in _SENSITIVE_KEYS else v
                for k, v in self.payload_preview.items()
            }
        return self


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact_in_string(value: str) -> str:
    """Replace sensitive values in a string with [REDACTED]."""
    _SENSITIVE_KEYS = _get_sensitive_keys()
    result = value
    for key in _SENSITIVE_KEYS:
        for sep in ("=", ":", " "):
            pattern = f"{key}{sep}"
            if pattern.lower() in result.lower():
                result = _redact_after_key(result, key, sep)
                break
    return result


_SEVERITY_PRIORITY: dict[str, int] = {
    "critical": 100,
    "error": 75,
    "warning": 50,
    "info": 25,
}


def severity_to_priority(severity: str) -> int:
    """Map alert severity to delivery priority integer.

    Higher values = more urgent. Unknown severity defaults to 0.
    """
    return _SEVERITY_PRIORITY.get(severity.lower().strip(), 0)


def _redact_after_key(text: str, key: str, sep: str) -> str:
    """Redact the value after a key-separator pair."""
    lower_text = text.lower()
    lower_key = key.lower()
    idx = lower_text.find(lower_key)
    if idx == -1:
        return text
    sep_idx = idx + len(lower_key)
    if sep_idx >= len(lower_text):
        return text
    actual_sep = text[sep_idx]
    if actual_sep.lower() != sep.lower():
        return text
    start_value = sep_idx + 1
    end_delimiters = {",", ";", "|", "\n", "\r"}
    end_value = len(text)
    for i in range(start_value, len(text)):
        if text[i] in end_delimiters:
            end_value = i
            break
    return text[:start_value] + "[REDACTED]" + text[end_value:]
