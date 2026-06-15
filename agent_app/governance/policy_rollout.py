"""Rollout plan and step models — defines structured policy rollout workflows."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, model_validator


class RolloutPlanStatus(StrEnum):
    """Status of a rollout plan."""

    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RolloutStepStatus(StrEnum):
    """Status of an individual rollout step."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class RolloutStepType(StrEnum):
    """Type of action a rollout step performs."""

    ACTIVATE = "activate"
    ASSIGN_RING = "assign_ring"
    CANARY_EVAL = "canary_eval"
    PROMOTE_RING = "promote_ring"


class RolloutStep(BaseModel):
    """A single step within a rollout plan."""

    step_id: str
    step_type: RolloutStepType
    environment: str
    ring_name: str | None = None
    from_ring: str | None = None
    to_ring: str | None = None
    required_gate_status: str | None = None
    eval_suite: str | None = None
    requires_approval: bool = False
    require_previous_step: str | None = None
    status: RolloutStepStatus = RolloutStepStatus.PENDING
    activation_id: str | None = None
    assignment_id: str | None = None
    approval_id: str | None = None
    error: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RolloutPlan(BaseModel):
    """A structured rollout plan consisting of ordered steps."""

    rollout_id: str
    name: str
    bundle_id: str
    status: RolloutPlanStatus = RolloutPlanStatus.DRAFT
    steps: list[RolloutStep]
    created_by: str
    reason: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _validate_steps(self) -> "RolloutPlan":
        if not self.steps:
            raise ValueError("Rollout plan must have at least one step")
        seen: set[str] = set()
        for step in self.steps:
            if step.step_id in seen:
                raise ValueError(f"Duplicate step_id: {step.step_id}")
            seen.add(step.step_id)
        step_ids = {s.step_id for s in self.steps}
        for step in self.steps:
            if step.require_previous_step is not None:
                if step.require_previous_step not in step_ids:
                    raise ValueError(
                        f"Step '{step.step_id}' requires previous step "
                        f"'{step.require_previous_step}' which does not exist"
                    )
        return self
