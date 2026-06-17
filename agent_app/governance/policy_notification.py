"""Policy notification models — notification messages and rules for governance events.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class PolicyNotificationSeverity(StrEnum):
    """Severity level for policy notifications."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class PolicyNotificationStatus(StrEnum):
    """Delivery status of a notification."""
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class PolicyNotificationMessage(BaseModel):
    """A notification message derived from a policy event."""
    notification_id: str = Field(..., description="Unique notification ID (pn_ prefix)")
    event_type: str = Field(..., description="Policy event type that triggered this notification")
    severity: PolicyNotificationSeverity = Field(..., description="Notification severity")
    title: str = Field(..., description="Notification title")
    body: str = Field(..., description="Notification body")
    source_type: str | None = Field(default=None, description="Source type (e.g. rollout_step)")
    source_id: str | None = Field(default=None, description="Source ID")
    actor_id: str | None = Field(default=None, description="Actor who triggered the event")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    status: PolicyNotificationStatus = Field(
        default=PolicyNotificationStatus.PENDING,
        description="Delivery status",
    )
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    sent_at: datetime | None = Field(default=None, description="Timezone-aware sent timestamp")
    error: dict[str, Any] | None = Field(default=None, description="Error details if delivery failed")

    @field_validator("notification_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("pn_"):
            raise ValueError("notification_id must use pn_ prefix")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class PolicyNotificationRuleStatus(StrEnum):
    """Status of a notification rule."""
    ENABLED = "enabled"
    DISABLED = "disabled"


class PolicyNotificationRule(BaseModel):
    """A rule that matches policy events to produce notifications."""
    rule_id: str = Field(..., description="Unique rule ID (pnr_ prefix)")
    name: str = Field(..., description="Human-readable rule name")
    event_types: list[str] = Field(..., description="Event types this rule matches")
    severity: PolicyNotificationSeverity = Field(
        default=PolicyNotificationSeverity.INFO,
        description="Default severity for notifications from this rule",
    )
    status: PolicyNotificationRuleStatus = Field(
        default=PolicyNotificationRuleStatus.ENABLED,
        description="Whether this rule is active",
    )
    source_types: list[str] = Field(
        default_factory=list,
        description="Source types to match (empty = match any)",
    )
    channels: list[str] = Field(
        default_factory=lambda: ["log"],
        description="Channels to deliver through",
    )
    title_template: str | None = Field(default=None, description="Title template with {placeholders}")
    body_template: str | None = Field(default=None, description="Body template with {placeholders}")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Rule metadata")

    @field_validator("rule_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("pnr_"):
            raise ValueError("rule_id must use pnr_ prefix")
        return v

    @model_validator(mode="after")
    def _validate_event_types(self) -> "PolicyNotificationRule":
        if not self.event_types:
            raise ValueError("event_types must not be empty")
        return self
