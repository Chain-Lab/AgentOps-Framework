"""Policy environment state -- tracks enabled/disabled status for policy environments."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class PolicyEnvironmentStatus(StrEnum):
    """Status of a policy environment."""
    ENABLED = "enabled"
    DISABLED = "disabled"


class PolicyEnvironmentState(BaseModel):
    """Tracks the enabled/disabled state of a policy environment."""
    environment: str = Field(..., description="Environment name")
    status: PolicyEnvironmentStatus = Field(
        default=PolicyEnvironmentStatus.ENABLED,
        description="Current environment status",
    )
    disabled_reason: str | None = Field(default=None, description="Why the environment was disabled")
    disabled_by: str | None = Field(default=None, description="Who disabled the environment")
    disabled_at: datetime | None = Field(default=None, description="When the environment was disabled")
    enabled_by: str | None = Field(default=None, description="Who last enabled the environment")
    enabled_at: datetime | None = Field(default=None, description="When the environment was last enabled")
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last update timestamp",
    )
