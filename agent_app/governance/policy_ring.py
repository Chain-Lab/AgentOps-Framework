"""Release ring models — controlled rollout rings for policy environments."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class ReleaseRingStatus(StrEnum):
    """Status of a release ring."""
    ENABLED = "enabled"
    DISABLED = "disabled"


class ReleaseRing(BaseModel):
    """A release ring within a policy environment for controlled rollout."""
    ring_id: str = Field(..., description="Unique ring identifier (ring_ prefix)")
    environment: str = Field(..., description="Owning environment")
    name: str = Field(..., description="Ring name (stable, canary, internal, etc.)")
    description: str | None = Field(default=None, description="Ring description")
    status: ReleaseRingStatus = Field(
        default=ReleaseRingStatus.ENABLED,
        description="Ring status",
    )
    is_default: bool = Field(default=False, description="Whether this is the default ring for the environment")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last update timestamp",
    )
