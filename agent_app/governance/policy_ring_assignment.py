"""Ring activation assignment models — maps activations to release rings."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class RingActivationAssignmentStatus(StrEnum):
    """Status of a ring activation assignment."""
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DISABLED = "disabled"


class RingActivationAssignment(BaseModel):
    """Records which activation is assigned to a ring in an environment."""
    assignment_id: str = Field(..., description="Unique assignment identifier (ra_ prefix)")
    environment: str = Field(..., description="Target environment")
    ring_name: str = Field(..., description="Target ring name")
    activation_id: str = Field(..., description="Assigned activation ID")
    bundle_id: str = Field(..., description="Bundle ID (convenience copy)")
    config_hash: str = Field(..., description="Config hash (integrity check)")
    status: RingActivationAssignmentStatus = Field(
        default=RingActivationAssignmentStatus.ACTIVE,
        description="Assignment status",
    )
    assigned_by: str = Field(..., description="Who assigned this activation")
    reason: str | None = Field(default=None, description="Assignment reason")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp",
    )
    superseded_at: datetime | None = Field(default=None, description="Supersession timestamp")
    superseded_by_assignment_id: str | None = Field(default=None, description="Assignment that superseded this one")
