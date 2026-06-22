"""Federation notification preference models — opt-in/opt-out management.

Phase 51: Preference decision enum, preference model, and preference explanation.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FederationNotificationPreferenceDecision(StrEnum):
    """Decision for notification delivery preference."""
    INHERIT = "inherit"
    OPT_IN = "opt_in"
    OPT_OUT = "opt_out"


class FederationNotificationPreferenceSubjectType(StrEnum):
    """Type of subject for a notification preference."""
    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    ROLE = "role"
    FEDERATION_MEMBER = "federation_member"


class FederationNotificationPreference(BaseModel):
    """A notification delivery preference rule."""
    preference_id: str = Field(..., description="Unique preference identifier (fnp_ prefix)")
    subject_type: FederationNotificationPreferenceSubjectType = Field(..., description="Type of subject")
    subject_id: str = Field(..., description="Subject identifier")
    federation_id: str | None = Field(default=None, description="Federation scope")
    approval_id: str | None = Field(default=None, description="Approval scope")
    event_type: str | None = Field(default=None, description="Event type scope")
    channel: str | None = Field(default=None, description="Channel scope")
    decision: FederationNotificationPreferenceDecision = Field(..., description="Preference decision")
    reason: str | None = Field(default=None, description="Human-readable reason")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    created_by: str | None = Field(default=None, description="Who created this preference")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    updated_at: datetime = Field(..., description="Timezone-aware last update timestamp")

    @field_validator("preference_id")
    @classmethod
    def _validate_preference_id(cls, v: str) -> str:
        if not v.startswith("fnp_"):
            raise ValueError(f"ID must start with 'fnp_', got '{v}'")
        return v

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("datetime must be timezone-aware")
        return v


class FederationNotificationPreferenceExplanation(BaseModel):
    """Explanation of why a notification was delivered or suppressed."""
    decision: FederationNotificationPreferenceDecision = Field(..., description="Final decision")
    matched_preference_id: str | None = Field(default=None, description="Preference rule that was matched")
    specificity: int = Field(default=0, description="Matched rule specificity (higher = more specific)")
    is_mandatory: bool = Field(default=False, description="Whether this is a mandatory notification")
    system_default: bool = Field(default=False, description="Whether system default was used")
    reason: str | None = Field(default=None, description="Human-readable explanation")
    reason_code: str | None = Field(default=None, description="Machine-readable reason code")
