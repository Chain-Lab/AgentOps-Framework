"""Rollout step approval model — tracks approval requests for rollout steps requiring human sign-off."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class RolloutStepApprovalStatus(str, Enum):
    """Status of a rollout step approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class RolloutApprovalPolicyType(str, Enum):
    """Type of approval policy."""

    SINGLE = "single"
    QUORUM = "quorum"


class RolloutApprovalPolicy(BaseModel):
    """Approval policy — determines how many approvals are needed and who can approve."""

    policy_type: RolloutApprovalPolicyType = RolloutApprovalPolicyType.SINGLE
    required_approvals: int = 1
    allowed_approver_permissions: list[str] = Field(default_factory=list)
    allowed_approver_roles: list[str] = Field(default_factory=list)
    prohibit_requester_approval: bool = True
    prohibit_creator_approval: bool = False
    prohibit_step_actor_approval: bool = False
    expires_after_seconds: int | None = None
    require_reason: bool = False

    @field_validator("required_approvals")
    @classmethod
    def _required_approvals_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("required_approvals must be >= 1")
        return v

    @field_validator("required_approvals")
    @classmethod
    def _single_requires_one(cls, v: int, info) -> int:
        # Only enforce when policy_type is explicitly SINGLE in the input data
        if (
            info.data.get("policy_type") == RolloutApprovalPolicyType.SINGLE
            and v != 1
        ):
            raise ValueError("SINGLE policy requires required_approvals == 1")
        return v

    @field_validator("expires_after_seconds")
    @classmethod
    def _expires_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("expires_after_seconds must be positive if provided")
        return v


class RolloutApprovalDecisionType(str, Enum):
    """Type of approval decision."""

    APPROVE = "approve"
    REJECT = "reject"


class RolloutApprovalDecision(BaseModel):
    """A single approval decision (approve or reject) by one actor."""

    decision_id: str  # rsd_ prefix
    approval_id: str
    decision_type: RolloutApprovalDecisionType
    decided_by: str
    reason: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    created_at: datetime

    @field_validator("decision_id")
    @classmethod
    def _decision_id_prefix(cls, v: str) -> str:
        if not v.startswith("rsd_"):
            raise ValueError("decision_id must start with 'rsd_'")
        return v

    @field_validator("created_at")
    @classmethod
    def _created_at_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class RolloutStepApproval(BaseModel):
    """Tracks an approval request for a rollout step that requires human sign-off."""

    approval_id: str  # rsa_ prefix
    rollout_id: str
    step_id: str
    bundle_id: str
    environment: str
    ring_name: str | None = None
    requested_by: str
    requested_reason: str | None = None
    status: RolloutStepApprovalStatus = RolloutStepApprovalStatus.PENDING
    resolved_by: str | None = None
    resolved_reason: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    policy: RolloutApprovalPolicy = Field(default_factory=RolloutApprovalPolicy)
    decisions: list[RolloutApprovalDecision] = Field(default_factory=list)
    expires_at: datetime | None = None
