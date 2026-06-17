"""Rollout plan and step models — defines structured policy rollout workflows."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


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


class RolloutGateMode(StrEnum):
    """Gate automation mode for rollout steps."""

    DISABLED = "disabled"
    MANUAL = "manual"
    AUTO = "auto"


class RolloutGateFailureAction(StrEnum):
    """Action to take when simulation gate fails for a rollout step."""

    BLOCK = "block"
    FAIL = "fail"
    SKIP = "skip"


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
    requires_simulation_gate: bool = Field(
        default=False,
        description="Whether simulation gate is required for this step (Phase 42)",
    )
    simulation_gate_requirement_id: str | None = Field(
        default=None,
        description="Release gate requirement ID (Phase 42)",
    )
    simulation_gate_result_id: str | None = Field(
        default=None,
        description="Simulation gate result ID (Phase 42)",
    )
    # Phase 43: Rollout gate automation
    simulation_gate_mode: RolloutGateMode = Field(
        default=RolloutGateMode.DISABLED,
        description="Gate automation mode: disabled, manual, or auto (Phase 43)",
    )
    simulation_gate_failure_action: RolloutGateFailureAction = Field(
        default=RolloutGateFailureAction.BLOCK,
        description="Action when gate fails: block, fail, or skip (Phase 43)",
    )
    simulation_candidate_rules: list[Any] = Field(
        default_factory=list,
        description="Candidate runtime policy rules for auto gate (Phase 43)",
    )
    simulation_gate_rules: list[Any] = Field(
        default_factory=list,
        description="Gate rules for auto gate evaluation (Phase 43)",
    )
    simulation_window_start: datetime | None = Field(
        default=None,
        description="Audit window start for simulation (Phase 43)",
    )
    simulation_window_end: datetime | None = Field(
        default=None,
        description="Audit window end for simulation (Phase 43)",
    )
    simulation_limit: int | None = Field(
        default=None,
        description="Max audit cases for simulation (Phase 43)",
    )
    simulation_include_base: bool = Field(
        default=True,
        description="Include base rules alongside candidates in simulation (Phase 43)",
    )
    simulation_gate_max_age_seconds: int | None = Field(
        default=None,
        description="Max age in seconds for gate result freshness (Phase 43)",
    )
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
