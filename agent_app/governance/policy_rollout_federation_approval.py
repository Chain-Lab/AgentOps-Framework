"""Federation approval models — approval requests, decisions, escalation, and dashboard summaries.

Phase 48: Federation approval workflow.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FederationApprovalStatus(StrEnum):
    """Status of a federation approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class FederationApprovalRequest(BaseModel):
    """A request for approval of a federation action."""

    approval_id: str = Field(..., description="Unique approval identifier (fap_ prefix)")
    federation_id: str = Field(..., description="Related federation ID")
    rollout_id: str | None = Field(default=None, description="Related rollout ID")
    target_id: str | None = Field(default=None, description="Related target ID")
    wave_id: str | None = Field(default=None, description="Related wave ID")
    tenant_id: str | None = Field(default=None, description="Affected tenant ID")
    environment: str | None = Field(default=None, description="Affected environment")
    region: str | None = Field(default=None, description="Affected region")
    ring: str | None = Field(default=None, description="Affected ring")
    action: str = Field(..., description="Federation action requiring approval (e.g. federation.plan.start)")
    requested_by: str = Field(..., description="ID of the actor who requested approval")
    required_approvers: list[str] = Field(default_factory=list, description="List of required approver IDs")
    delegated_approvers: list[str] = Field(default_factory=list, description="List of delegated approver IDs")
    approvers_who_approved: list[str] = Field(default_factory=list, description="List of approver IDs who approved")
    approvers_who_rejected: list[str] = Field(default_factory=list, description="List of approver IDs who rejected")
    status: FederationApprovalStatus = Field(
        default=FederationApprovalStatus.PENDING, description="Current approval status"
    )
    reason: str | None = Field(default=None, description="Reason for the approval request")
    rejection_reason: str | None = Field(default=None, description="Reason for rejection, if rejected")
    escalation_level: int = Field(default=0, description="Current escalation level (0 = not escalated)")
    escalation_reason: str | None = Field(default=None, description="Reason for escalation, if escalated")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    resolved_at: datetime | None = Field(default=None, description="Timezone-aware resolution timestamp")
    resolved_by: str | None = Field(default=None, description="ID of the actor who resolved the request")
    expires_at: datetime | None = Field(default=None, description="Timezone-aware expiration timestamp")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional approval metadata")

    @field_validator("approval_id")
    @classmethod
    def _validate_id_prefix(cls, v: str) -> str:
        if not v.startswith("fap_"):
            raise ValueError(f"ID must start with 'fap_', got '{v}'")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class FederationApprovalPolicy(BaseModel):
    """Policy configuration for federation approval workflows."""

    enabled: bool = Field(default=False, description="Whether approval is required for federation actions")
    require_approval_for: list[str] = Field(
        default_factory=list, description="List of action strings that require approval"
    )
    default_required_approvers: list[str] = Field(
        default_factory=list, description="Default list of required approver IDs"
    )
    delegation_enabled: bool = Field(default=False, description="Whether approval delegation is allowed")
    escalation_enabled: bool = Field(default=False, description="Whether escalation is enabled")
    escalation_after_minutes: int = Field(
        default=60, description="Minutes before an unresolved request is escalated"
    )
    escalate_to: list[str] = Field(default_factory=list, description="List of approver IDs to escalate to")


class FederationApprovalDecision(BaseModel):
    """A decision (approve or reject) on a federation approval request."""

    approval_id: str = Field(..., description="Related approval request ID")
    actor_id: str = Field(..., description="ID of the actor making the decision")
    decision: FederationApprovalStatus = Field(
        ..., description="Decision status (should be APPROVED or REJECTED)"
    )
    reason: str | None = Field(default=None, description="Reason for the decision")
    is_delegated: bool = Field(default=False, description="Whether this decision was made via delegation")
    delegated_by: str | None = Field(default=None, description="ID of the original approver who delegated")
    created_at: datetime = Field(..., description="Timezone-aware decision timestamp")

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class FederationApprovalEscalation(BaseModel):
    """An escalation event on a federation approval request."""

    approval_id: str = Field(..., description="Related approval request ID")
    from_level: int = Field(..., description="Escalation level before escalation")
    to_level: int = Field(..., description="Escalation level after escalation")
    escalated_by: str | None = Field(default=None, description="ID of the actor who triggered escalation")
    reason: str | None = Field(default=None, description="Reason for escalation")
    new_required_approvers: list[str] = Field(
        default_factory=list, description="List of new required approver IDs after escalation"
    )
    created_at: datetime = Field(..., description="Timezone-aware escalation timestamp")

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class FederationApprovalDashboardSummary(BaseModel):
    """Dashboard summary of federation approval states and metrics."""

    total_pending: int = Field(default=0, description="Total pending approval requests")
    total_approved: int = Field(default=0, description="Total approved requests")
    total_rejected: int = Field(default=0, description="Total rejected requests")
    total_expired: int = Field(default=0, description="Total expired requests")
    total_escalated: int = Field(default=0, description="Total escalated requests")
    total_cancelled: int = Field(default=0, description="Total cancelled requests")
    average_approval_latency_seconds: float | None = Field(
        default=None, description="Average time from request to approval in seconds"
    )
    by_tenant: dict[str, int] = Field(
        default_factory=dict, description="Mapping of tenant ID to pending approval count"
    )
    by_action: dict[str, int] = Field(
        default_factory=dict, description="Mapping of action string to pending approval count"
    )
    blocked_federation_actions: int = Field(
        default=0, description="Number of federation actions currently blocked by pending approvals"
    )
