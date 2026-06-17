"""Release gate Requirement model — tracks simulation gate requirements for promotions and rollout steps.

Phase 42: Policy Release Automation and Simulation Gate Enforcement.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ReleaseGateRequirementStatus(StrEnum):
    """Status of a release gate requirement."""

    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    SATISFIED = "satisfied"
    FAILED = "failed"
    EXPIRED = "expired"


class ReleaseGateRequirement(BaseModel):
    """A requirement that a passing simulation gate result be attached before promotion/rollout proceeds.

    Attributes:
        requirement_id: Unique identifier (rgr_ prefix).
        source_type: What this requirement is for — "promotion" or "rollout_step".
        source_id: The ID of the source (promotion_id or step_id).
        gate_result_id: The attached gate result ID, once known.
        simulation_id: The simulation ID associated with the gate result.
        required: Whether the gate requirement is active.
        status: Current status of the requirement.
        max_age_seconds: If set, gate result becomes stale after this many seconds.
        created_at: When the requirement was created (timezone-aware).
        satisfied_at: When the requirement was satisfied (timezone-aware).
        metadata: Arbitrary metadata.
    """

    requirement_id: str = Field(..., description="Unique requirement ID (rgr_ prefix)")
    source_type: str = Field(..., description="Source type: promotion | rollout_step")
    source_id: str = Field(..., description="Source ID (promotion_id or step_id)")
    gate_result_id: str | None = Field(default=None, description="Attached gate result ID")
    simulation_id: str | None = Field(default=None, description="Simulation ID")
    required: bool = Field(default=True, description="Whether gate is required")
    status: ReleaseGateRequirementStatus = Field(
        default=ReleaseGateRequirementStatus.REQUIRED,
        description="Current requirement status",
    )
    max_age_seconds: int | None = Field(default=None, description="Gate freshness in seconds")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp",
    )
    satisfied_at: datetime | None = Field(default=None, description="Satisfaction timestamp")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")
