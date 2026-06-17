"""Policy promotion request model for policy release RBAC workflow.

Phase 30: promotion request tracking with status lifecycle and audit fields.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class PromotionRequestStatus(str, Enum):
    """Lifecycle status of a policy promotion request.

    Attributes:
        PENDING: Awaiting review.
        APPROVED: Approved by an authorized reviewer.
        REJECTED: Rejected by an authorized reviewer.
        EXECUTED: Promotion has been executed.
        CANCELLED: Request was cancelled before resolution.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class PromotionRequest(BaseModel):
    """A policy promotion request tracking approval and execution.

    Attributes:
        promotion_id: Unique identifier for this promotion request (pr_ prefix).
        bundle_id: The policy bundle being promoted.
        gate_result_id: Optional gate evaluation result reference (gr_ prefix).
        requested_by: Identity of who submitted the request.
        tenant_id: Optional tenant scoping.
        status: Current lifecycle status of the request.
        reason: Optional reason provided by the requester.
        approval_reason: Optional reason provided by the approver.
        rejection_reason: Optional reason provided by the rejecter.
        created_at: When the request was submitted.
        resolved_at: When the request was approved/rejected/cancelled.
        resolved_by: Identity of who resolved the request.
        executed_at: When the promotion was executed (if applicable).
        executed_by: Identity of who executed the promotion.
    """

    promotion_id: str = Field(..., description="Unique promotion request identifier (pr_ prefix)")
    bundle_id: str = Field(..., description="Policy bundle being promoted")
    gate_result_id: str | None = Field(default=None, description="Gate evaluation result reference (gr_ prefix)")
    requested_by: str = Field(..., description="Identity of the requester")
    tenant_id: str | None = Field(default=None, description="Tenant scoping")
    status: str = Field(
        default=PromotionRequestStatus.PENDING,
        description="Current lifecycle status",
    )
    reason: str | None = Field(default=None, description="Requester's reason")
    approval_reason: str | None = Field(default=None, description="Approver's reason")
    rejection_reason: str | None = Field(default=None, description="Rejecter's reason")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Submission timestamp",
    )
    resolved_at: datetime | None = Field(default=None, description="Resolution timestamp")
    resolved_by: str | None = Field(default=None, description="Identity of resolver")
    executed_at: datetime | None = Field(default=None, description="Execution timestamp")
    executed_by: str | None = Field(default=None, description="Identity of executor")
    simulation_gate_required: bool = Field(
        default=False,
        description="Whether simulation gate is required for this promotion (Phase 42)",
    )
    simulation_gate_requirement_id: str | None = Field(
        default=None,
        description="Release gate requirement ID (rgr_ prefix, Phase 42)",
    )
    simulation_gate_result_id: str | None = Field(
        default=None,
        description="Simulation gate result ID (pg_ prefix, Phase 42)",
    )
    simulation_id: str | None = Field(
        default=None,
        description="Simulation ID (psim_ prefix, Phase 42)",
    )
