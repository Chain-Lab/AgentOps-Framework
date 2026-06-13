"""Policy activation models -- environment-specific policy bundle activation."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pydantic import BaseModel, Field


class PolicyActivationStatus(StrEnum):
    """Lifecycle status of a policy activation."""
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"


class PolicyActivation(BaseModel):
    """Records that a policy bundle is the active policy for an environment."""
    activation_id: str = Field(..., description="Unique activation identifier (pa_ prefix)")
    environment: str = Field(..., description="Target environment")
    bundle_id: str = Field(..., description="Activated bundle ID")
    config_hash: str = Field(..., description="SHA-256 hash of bundle config")
    promotion_id: str | None = Field(default=None, description="Promotion request ID")
    activated_by: str = Field(..., description="Who activated this bundle")
    status: str = Field(default=PolicyActivationStatus.ACTIVE, description="Activation lifecycle status")
    reason: str | None = Field(default=None, description="Activation reason")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Creation timestamp")
    superseded_at: datetime | None = Field(default=None, description="Supersession timestamp")
    superseded_by_activation_id: str | None = Field(default=None, description="Activation that superseded this one")
    rollback_of_activation_id: str | None = Field(default=None, description="Activation being rolled back (the one that was active)")
    rollback_target_activation_id: str | None = Field(default=None, description="Activation being rolled back to (the target)")
