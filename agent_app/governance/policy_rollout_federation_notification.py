"""Federation notification models — notification messages, delivery, policy, dispatch, DLQ, and retry for federation approval workflows.

Phase 49: Federation Notification Models.
Phase 50: DLQ Models and Retry Policy.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FederationNotificationChannel(StrEnum):
    """Channel through which a federation notification is delivered."""

    EMAIL = "email"
    SLACK = "slack"
    WEBHOOK = "webhook"
    CONSOLE = "console"
    NOOP = "noop"


class FederationNotificationStatus(StrEnum):
    """Delivery status of a federation notification."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    DEAD_LETTERED = "dead_lettered"
    SUPPRESSED = "suppressed"
    TEMPLATE_FAILED = "template_failed"
    SIGNATURE_FAILED = "signature_failed"


class FederationNotificationEventType(StrEnum):
    """Event types that trigger federation notifications."""

    APPROVAL_CREATED = "approval.created"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_ESCALATED = "approval.escalated"
    APPROVAL_CANCELLED = "approval.cancelled"
    APPROVAL_EXPIRED = "approval.expired"


class FederationNotificationDLQStatus(StrEnum):
    """Status of a notification dead-letter queue entry."""

    PENDING = "pending"
    RETRIED = "retried"
    PURGED = "purged"
    RESOLVED = "resolved"


class FederationNotificationDLQReason(StrEnum):
    """Reason a notification entered the dead-letter queue."""

    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
    DELIVERY_FAILED = "delivery_failed"
    ADAPTER_ERROR = "adapter_error"
    INVALID_RECIPIENT = "invalid_recipient"
    MANUAL = "manual"


class FederationNotificationMessage(BaseModel):
    """A notification message for a federation approval event."""

    notification_id: str = Field(..., description="Unique notification identifier (fn_ prefix)")
    approval_id: str = Field(..., description="Related approval request ID")
    federation_id: str | None = Field(default=None, description="Related federation ID")
    event_type: FederationNotificationEventType = Field(..., description="Event type that triggered this notification")
    channel: FederationNotificationChannel = Field(..., description="Channel for delivery")
    recipients: list[str] = Field(default_factory=list, description="List of recipient identifiers")
    subject: str | None = Field(default=None, description="Notification subject line")
    body: str = Field(..., description="Notification body content")
    payload: dict[str, Any] = Field(default_factory=dict, description="Additional structured payload data")
    status: FederationNotificationStatus = Field(
        default=FederationNotificationStatus.PENDING, description="Delivery status"
    )
    attempt_count: int = Field(default=0, description="Number of delivery attempts made")
    max_attempts: int = Field(default=3, description="Maximum number of delivery attempts")
    last_error: str | None = Field(default=None, description="Last error message if delivery failed")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    sent_at: datetime | None = Field(default=None, description="Timezone-aware sent timestamp")
    next_attempt_at: datetime | None = Field(default=None, description="Timezone-aware next retry timestamp")

    @field_validator("notification_id")
    @classmethod
    def _validate_id_prefix(cls, v: str) -> str:
        if not v.startswith("fn_"):
            raise ValueError(f"ID must start with 'fn_', got '{v}'")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class FederationNotificationDelivery(BaseModel):
    """Record of a single notification delivery attempt."""

    notification_id: str = Field(..., description="Related notification ID")
    channel: FederationNotificationChannel = Field(..., description="Channel used for delivery")
    status: FederationNotificationStatus = Field(..., description="Delivery status for this attempt")
    error: str | None = Field(default=None, description="Error message if delivery failed")
    delivered_at: datetime | None = Field(default=None, description="Timezone-aware delivery timestamp")


class FederationNotificationPolicy(BaseModel):
    """Policy configuration for federation notification delivery."""

    enabled: bool = Field(default=False, description="Whether federation notifications are enabled")
    default_channels: list[FederationNotificationChannel] = Field(
        default_factory=lambda: [FederationNotificationChannel.CONSOLE],
        description="Default channels for federation notifications",
    )
    recipients_by_channel: dict[str, list[str]] = Field(
        default_factory=dict, description="Mapping of channel name to list of recipient identifiers"
    )
    max_attempts: int = Field(default=3, description="Maximum delivery attempts per notification")
    backoff_seconds: int = Field(default=60, description="Backoff interval between retry attempts in seconds")
    webhook_url: str | None = Field(default=None, description="Webhook URL for webhook channel delivery")
    webhook_timeout_seconds: int = Field(default=5, description="Timeout for webhook requests in seconds")


class FederationNotificationTarget(BaseModel):
    """A notification target specifying channel, recipients, and config."""

    channel: FederationNotificationChannel = Field(..., description="Channel for delivery")
    recipients: list[str] = Field(default_factory=list, description="List of recipient identifiers")
    config: dict[str, Any] = Field(default_factory=dict, description="Channel-specific configuration")


class FederationNotificationDispatchResult(BaseModel):
    """Aggregate result of a notification dispatch operation."""

    total_dispatched: int = Field(default=0, description="Total notifications dispatched")
    total_sent: int = Field(default=0, description="Total notifications successfully sent")
    total_failed: int = Field(default=0, description="Total notifications that failed delivery")
    total_skipped: int = Field(default=0, description="Total notifications skipped")
    errors: list[str] = Field(default_factory=list, description="List of error messages from failed dispatches")


class FederationNotificationDeadLetter(BaseModel):
    """A notification that has been moved to the dead-letter queue after exhausting retries."""

    dlq_id: str = Field(..., description="Unique DLQ entry identifier (fdlq_ prefix)")
    notification_id: str = Field(..., description="Original notification ID")
    approval_id: str | None = Field(default=None, description="Related approval ID")
    federation_id: str | None = Field(default=None, description="Related federation ID")
    channel: str = Field(..., description="Notification channel")
    adapter: str | None = Field(default=None, description="Adapter that failed")
    recipient: str | None = Field(default=None, description="Intended recipient")
    reason: FederationNotificationDLQReason = Field(..., description="Reason for DLQ entry")
    status: FederationNotificationDLQStatus = Field(default=FederationNotificationDLQStatus.PENDING, description="DLQ entry status")
    failure_count: int = Field(default=0, description="Number of delivery failures")
    last_error: str | None = Field(default=None, description="Last error message")
    payload: dict[str, Any] = Field(default_factory=dict, description="Original notification payload")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    updated_at: datetime = Field(..., description="Timezone-aware last update timestamp")
    retried_at: datetime | None = Field(default=None, description="Timezone-aware retry timestamp")
    purged_at: datetime | None = Field(default=None, description="Timezone-aware purge timestamp")

    @field_validator("dlq_id")
    @classmethod
    def _validate_dlq_id(cls, v: str) -> str:
        if not v.startswith("fdlq_"):
            raise ValueError(f"ID must start with 'fdlq_', got '{v}'")
        return v

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("datetime must be timezone-aware")
        return v


class FederationNotificationRetryPolicy(BaseModel):
    """Retry policy for federation notification delivery."""

    max_attempts: int = Field(default=3, description="Maximum delivery attempts")
    backoff_seconds: int = Field(default=60, description="Backoff interval between retries in seconds")
    send_to_dlq: bool = Field(default=True, description="Whether to send to DLQ after max retries")
