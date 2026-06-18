"""Policy rollout federation models — framework-level coordinated rollouts.

Phase 46: Federated rollout targets, plans, executions, waves, and conflicts.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_app.governance.policy_rollout import RolloutStep


class FederatedTargetStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class FederatedRolloutTarget(BaseModel):
    target_id: str
    name: str
    tenant_id: str | None = None
    environment: str
    ring_name: str | None = None
    region: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    status: FederatedTargetStatus = FederatedTargetStatus.ENABLED
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("target_id")
    @classmethod
    def _validate_target_id(cls, value: str) -> str:
        if not value.startswith("frt_"):
            raise ValueError(f"ID must start with 'frt_', got '{value}'")
        return value

    @field_validator("created_at")
    @classmethod
    def _validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class FederatedRolloutPlanStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class FederationExecutionStrategy(StrEnum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    WAVE = "wave"


class FederatedRolloutTargetExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class FederatedRolloutTargetExecution(BaseModel):
    execution_id: str
    target_id: str
    rollout_id: str | None = None
    status: FederatedRolloutTargetExecutionStatus = FederatedRolloutTargetExecutionStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("execution_id")
    @classmethod
    def _validate_execution_id(cls, value: str) -> str:
        if not value.startswith("fre_"):
            raise ValueError(f"ID must start with 'fre_', got '{value}'")
        return value

    @field_validator("started_at", "completed_at")
    @classmethod
    def _validate_optional_datetimes(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.tzinfo.utcoffset(value) is None):
            raise ValueError("datetime fields must be timezone-aware")
        return value


class FederatedRolloutWave(BaseModel):
    wave_id: str
    name: str | None = None
    target_ids: list[str]
    require_all_successful: bool = True
    status: FederatedRolloutTargetExecutionStatus = FederatedRolloutTargetExecutionStatus.PENDING
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("wave_id")
    @classmethod
    def _validate_wave_id(cls, value: str) -> str:
        if not value.startswith("frw_"):
            raise ValueError(f"ID must start with 'frw_', got '{value}'")
        return value

    @model_validator(mode="after")
    def _validate_targets(self) -> "FederatedRolloutWave":
        if not self.target_ids:
            raise ValueError("Federated rollout wave must include at least one target_id")
        seen: set[str] = set()
        for target_id in self.target_ids:
            if target_id in seen:
                raise ValueError(f"Duplicate target_id in wave: {target_id}")
            seen.add(target_id)
        return self


class FederatedRolloutPlan(BaseModel):
    federation_id: str
    name: str
    bundle_id: str
    strategy: FederationExecutionStrategy = FederationExecutionStrategy.SEQUENTIAL
    status: FederatedRolloutPlanStatus = FederatedRolloutPlanStatus.DRAFT
    target_ids: list[str] = Field(default_factory=list)
    waves: list[FederatedRolloutWave] = Field(default_factory=list)
    executions: list[FederatedRolloutTargetExecution] = Field(default_factory=list)
    rollout_template_steps: list[RolloutStep] = Field(default_factory=list)
    created_by: str
    reason: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("federation_id")
    @classmethod
    def _validate_federation_id(cls, value: str) -> str:
        if not value.startswith("frp_"):
            raise ValueError(f"ID must start with 'frp_', got '{value}'")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_datetimes(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at and updated_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validate_plan(self) -> "FederatedRolloutPlan":
        if not self.target_ids and not self.waves:
            raise ValueError("Federated rollout plan must include target_ids or waves")
        if self.strategy == FederationExecutionStrategy.WAVE and not self.waves:
            raise ValueError("WAVE strategy requires at least one wave")
        seen_targets: set[str] = set()
        for target_id in self.target_ids:
            if target_id in seen_targets:
                raise ValueError(f"Duplicate target_id: {target_id}")
            seen_targets.add(target_id)
        if self.target_ids:
            for wave in self.waves:
                for target_id in wave.target_ids:
                    if target_id not in seen_targets:
                        raise ValueError(f"Wave '{wave.wave_id}' references unknown target_id '{target_id}'")
        seen_executions: set[str] = set()
        for execution in self.executions:
            if execution.execution_id in seen_executions:
                raise ValueError(f"Duplicate execution_id: {execution.execution_id}")
            seen_executions.add(execution.execution_id)
        return self


class RolloutConflictSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class RolloutConflictType(StrEnum):
    TARGET_ALREADY_ACTIVE = "target_already_active"
    ENVIRONMENT_RING_CONFLICT = "environment_ring_conflict"
    BUNDLE_CONFLICT = "bundle_conflict"
    DISABLED_TARGET = "disabled_target"
    DUPLICATE_TARGET = "duplicate_target"
    MISSING_TARGET = "missing_target"


class RolloutConflict(BaseModel):
    conflict_id: str
    conflict_type: RolloutConflictType
    severity: RolloutConflictSeverity
    target_id: str | None = None
    environment: str | None = None
    ring_name: str | None = None
    existing_rollout_id: str | None = None
    existing_federation_id: str | None = None
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("conflict_id")
    @classmethod
    def _validate_conflict_id(cls, value: str) -> str:
        if not value.startswith("frc_"):
            raise ValueError(f"ID must start with 'frc_', got '{value}'")
        return value
