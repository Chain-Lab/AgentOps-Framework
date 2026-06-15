"""Rollout step approval model — tracks approval requests for rollout steps requiring human sign-off."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class RolloutStepApprovalStatus(str, Enum):
    """Status of a rollout step approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


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
