"""DAG compensation state models — per-run compensation execution tracking.

Phase 16.1: Introduces structured models for tracking compensation execution
at the action level.  A single CompensationExecutionState tracks all
compensation actions for one workflow run, with individual CompensationActionState
records for each action.

Compensation state is a recovery aid — it does NOT guarantee exactly-once
execution and does NOT replace lease renewal, snapshot, or business-level
idempotency.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CompensationActionStatus(StrEnum):
    """Status of a single compensation action."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CompensationRunStatus(StrEnum):
    """Overall status of a compensation run."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Action-level state
# ---------------------------------------------------------------------------


class CompensationActionState(BaseModel):
    """Execution state of a single compensation action.

    Attributes:
        action_id: Unique identifier for this action (auto-generated if not set).
        run_id: Parent workflow run identifier.
        workflow_name: Name of the workflow being compensated.
        node_id: The node ID that triggered this compensation action.
        compensating_for_node_id: The original node ID being compensated.
        status: Current action status.
        attempts: Number of execution attempts so far.
        max_attempts: Maximum number of attempts allowed.
        input: Resolved input for the compensation handler.
        output: Handler output (if completed).
        error: Error details (if failed), with type and message.
        idempotency_key: Optional idempotency key for the handler.
        started_at: When the action began executing.
        completed_at: When the action finished (success or failure).
    """

    action_id: str = Field(
        default_factory=lambda: f"action_{uuid.uuid4().hex[:12]}",
        description="Unique action identifier",
    )
    run_id: str = Field(..., description="Parent workflow run ID")
    workflow_name: str | None = Field(
        default=None, description="Workflow name"
    )
    node_id: str = Field(..., description="Node ID that triggered this action")
    compensating_for_node_id: str | None = Field(
        default=None, description="Original node being compensated"
    )

    status: str = Field(
        default=CompensationActionStatus.PENDING.value,
        description="Current action status",
    )
    attempts: int = Field(default=0, ge=0, description="Attempts so far")
    max_attempts: int = Field(default=1, ge=1, description="Max attempts allowed")

    input: dict[str, Any] | None = Field(
        default=None, description="Resolved handler input"
    )
    output: Any | None = Field(default=None, description="Handler output")
    error: dict[str, Any] | None = Field(
        default=None, description="Error details if failed"
    )

    idempotency_key: str | None = Field(
        default=None, description="Idempotency key for the handler"
    )

    started_at: datetime | None = Field(
        default=None, description="Action start time"
    )
    completed_at: datetime | None = Field(
        default=None, description="Action completion time"
    )

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        valid = {s.value for s in CompensationActionStatus}
        if v not in valid:
            raise ValueError(
                f"Invalid action status '{v}'. Must be one of: {sorted(valid)}"
            )
        return v

    def mark_running(self) -> None:
        """Transition action to RUNNING, updating timestamps."""
        self.status = CompensationActionStatus.RUNNING.value
        self.started_at = datetime.now(timezone.utc)
        self.attempts += 1

    def mark_completed(self, output: Any = None) -> None:
        """Transition action to COMPLETED with output."""
        self.status = CompensationActionStatus.COMPLETED.value
        self.output = output
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self, error: dict[str, Any] | Exception) -> None:
        """Transition action to FAILED with error details."""
        self.status = CompensationActionStatus.FAILED.value
        if isinstance(error, Exception):
            self.error = {
                "type": type(error).__name__,
                "message": str(error),
            }
        else:
            self.error = error
        self.completed_at = datetime.now(timezone.utc)

    def mark_skipped(self, reason: str | None = None) -> None:
        """Transition action to SKIPPED."""
        self.status = CompensationActionStatus.SKIPPED.value
        if reason:
            self.error = {"type": "skipped", "message": reason}
        self.completed_at = datetime.now(timezone.utc)

    def can_retry(self) -> bool:
        """Return True if this action can be retried."""
        return (
            self.status == CompensationActionStatus.FAILED.value
            and self.attempts < self.max_attempts
        )


# ---------------------------------------------------------------------------
# Run-level state
# ---------------------------------------------------------------------------


class CompensationExecutionState(BaseModel):
    """Persistent state of a compensation run for a workflow.

    Tracks all compensation actions for a single workflow run, maintaining
    the execution order and per-action status.  This is the primary
    persistence unit for compensation recovery.

    Attributes:
        compensation_id: Unique identifier (auto-generated).
        run_id: Parent workflow run identifier.
        workflow_name: Name of the workflow being compensated.
        status: Overall compensation run status.
        schema_version: Schema version for migration safety.
        actions: Dict of action_id -> CompensationActionState.
        action_order: Ordered list of action_ids (execution order).
        created_at: When the compensation state was first created.
        updated_at: When the compensation state was last modified.
        started_at: When compensation execution began.
        completed_at: When compensation execution finished.
    """

    compensation_id: str = Field(
        default_factory=lambda: f"comp_{uuid.uuid4().hex[:12]}",
        description="Unique compensation run identifier",
    )
    run_id: str = Field(
        ...,
        description="Parent workflow run ID (unique per run)",
    )
    workflow_name: str | None = Field(
        default=None,
        description="Workflow name",
    )
    status: str = Field(
        default=CompensationRunStatus.PENDING.value,
        description="Overall compensation status",
    )
    schema_version: int = Field(
        default=1,
        ge=1,
        description="Schema version for migration safety",
    )

    actions: dict[str, CompensationActionState] = Field(
        default_factory=dict,
        description="Per-action state keyed by action_id",
    )
    action_order: list[str] = Field(
        default_factory=list,
        description="Ordered action_ids (execution order)",
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation time",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last modification time",
    )
    started_at: datetime | None = Field(
        default=None,
        description="Compensation start time",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Compensation completion time",
    )

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        valid = {s.value for s in CompensationRunStatus}
        if v not in valid:
            raise ValueError(
                f"Invalid compensation status '{v}'. "
                f"Must be one of: {sorted(valid)}"
            )
        return v

    @model_validator(mode="after")
    def _sync_action_order(self) -> CompensationExecutionState:
        """Ensure action_order only contains valid action IDs."""
        valid_ids = set(self.actions.keys())
        self.action_order = [aid for aid in self.action_order if aid in valid_ids]
        # Add any actions not yet in action_order
        for aid in self.actions:
            if aid not in self.action_order:
                self.action_order.append(aid)
        return self

    def add_action(self, action: CompensationActionState) -> None:
        """Add a compensation action, maintaining order."""
        if action.action_id not in self.actions:
            self.actions[action.action_id] = action
            self.action_order.append(action.action_id)
        self.updated_at = datetime.now(timezone.utc)

    def get_action(self, action_id: str) -> CompensationActionState | None:
        """Get a specific action by ID."""
        return self.actions.get(action_id)

    def get_pending_actions(self) -> list[CompensationActionState]:
        """Get all actions that are still pending."""
        return [
            a for a in self.actions.values()
            if a.status == CompensationActionStatus.PENDING.value
        ]

    def get_failed_retryable_actions(self) -> list[CompensationActionState]:
        """Get failed actions that can still be retried."""
        return [a for a in self.actions.values() if a.can_retry()]

    def get_completed_actions(self) -> list[CompensationActionState]:
        """Get all completed actions."""
        return [
            a for a in self.actions.values()
            if a.status == CompensationActionStatus.COMPLETED.value
        ]

    def mark_running(self) -> None:
        """Mark the compensation run as running."""
        self.status = CompensationRunStatus.RUNNING.value
        self.started_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def mark_completed(self) -> None:
        """Mark the compensation run as completed."""
        self.status = CompensationRunStatus.COMPLETED.value
        self.completed_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def mark_partial_failed(self) -> None:
        """Mark the compensation run as partially failed."""
        self.status = CompensationRunStatus.PARTIAL_FAILED.value
        self.completed_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def mark_failed(self) -> None:
        """Mark the compensation run as failed."""
        self.status = CompensationRunStatus.FAILED.value
        self.completed_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

# CompensationExecutionState has `output: Any` which may not be JSON-serializable.
# We use model_dump(mode="json") for serialization, which handles datetime
# conversion.  Complex objects must provide their own JSON serialization.


def serialize_compensation_state(state: CompensationExecutionState) -> str:
    """Serialize a CompensationExecutionState to JSON.

    Args:
        state: The state to serialize.

    Returns:
        JSON string with timezone-aware ISO datetime fields.
    """
    return json.dumps(state.model_dump(mode="json"))


def deserialize_compensation_state(json_str: str) -> CompensationExecutionState:
    """Deserialize a CompensationExecutionState from JSON.

    Args:
        json_str: JSON string previously produced by serialize_compensation_state.

    Returns:
        Deserialized CompensationExecutionState.

    Raises:
        ValueError: If json_str is not valid JSON or missing required fields.
    """
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            f"Invalid compensation state JSON: {exc}"
        ) from exc
    try:
        return CompensationExecutionState.model_validate(data)
    except Exception as exc:
        raise ValueError(
            f"Invalid compensation state data: {exc}"
        ) from exc
