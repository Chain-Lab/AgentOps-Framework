"""Policy Enforcement Point models — defines runtime policy decisions.

Phase 38: Unified enforcement for tool execution, approvals, and rollout steps.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agent_app.governance.policy_rollout_approval import RolloutApprovalPolicy


class PolicyActionType(str, Enum):
    """Type of action being governed by runtime policy."""

    TOOL_EXECUTE = "tool.execute"
    TOOL_RESUME = "tool.resume"
    APPROVAL_APPROVE = "approval.approve"
    APPROVAL_REJECT = "approval.reject"
    ROLLOUT_STEP_EXECUTE = "rollout.step.execute"
    POLICY_PROMOTION_EXECUTE = "policy.promotion.execute"


class PolicyDecisionStatus(str, Enum):
    """Status of a runtime policy enforcement decision."""

    ALLOWED = "allowed"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"


class PolicyEnforcementDecision(BaseModel):
    """Result of a runtime policy enforcement check."""

    decision_id: str  # ped_ prefix
    status: PolicyDecisionStatus
    action_type: PolicyActionType
    subject: str | None = None
    reason: str | None = None
    required_permissions: list[str] = Field(default_factory=list)
    required_roles: list[str] = Field(default_factory=list)
    approval_policy: RolloutApprovalPolicy | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("decision_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("ped_"):
            raise ValueError("decision_id must use ped_ prefix")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v
