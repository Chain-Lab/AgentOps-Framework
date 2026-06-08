"""DAG execution snapshot — persistent recovery points for long-running workflows.

Phase 16.0: Introduces a lightweight snapshot mechanism that captures the
execution state of a DAG workflow run at key state transitions.  Snapshots
are recovery aids — they do NOT guarantee exactly-once execution and do NOT
replace lease renewal or business-level idempotency.

Snapshot semantics:
  * Written at node-level state transitions (start, complete, fail, interrupt).
  * NOT written per-token or per-stream-delta.
  * Separate from the lease table — snapshots survive lease expiry.
  * resume() reads the latest snapshot to skip completed nodes.
  * Snapshot failure is surfaced as a stable error (not silently swallowed).

This is NOT a distributed transaction log, NOT Celery, NOT Temporal.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DagSnapshotStatus(StrEnum):
    """Overall status of a workflow run as captured in a snapshot."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"
    INTERRUPTED = "interrupted"


# ---------------------------------------------------------------------------
# Node-level snapshot
# ---------------------------------------------------------------------------


class DagNodeSnapshot(BaseModel):
    """Execution state of a single node captured in a snapshot.

    Attributes:
        node_id: DAG node identifier.
        status: Node execution status at snapshot time.
        attempts: Number of execution attempts so far.
        input: Input data passed to this node (JSON-serializable).
        output: Node output (JSON-serializable), or None.
        error: Structured error info, or None.
        started_at: When execution began, or None.
        completed_at: When execution finished, or None.
    """

    node_id: str = Field(..., description="DAG node identifier")
    status: str = Field(..., description="Node execution status")
    attempts: int = Field(default=0, description="Execution attempt count")
    input: dict[str, Any] | None = Field(
        default=None, description="Node input data (JSON-serializable)"
    )
    output: Any | None = Field(default=None, description="Node output (JSON-serializable)")
    error: dict[str, Any] | None = Field(default=None, description="Error details")
    started_at: datetime | None = Field(default=None, description="Execution start time")
    completed_at: datetime | None = Field(
        default=None, description="Execution end time"
    )


# ---------------------------------------------------------------------------
# Workflow-level snapshot
# ---------------------------------------------------------------------------


class DagRunSnapshot(BaseModel):
    """Persistent recovery point for a DAG workflow run.

    Snapshots are written at key state transitions during DAG execution
    and are used by ``DagExecutor.resume()`` to determine where to
    continue after a crash or interruption.

    Attributes:
        snapshot_id: Unique snapshot identifier (UUID).
        run_id: Parent workflow run identifier.
        workflow_name: Name of the DAG workflow definition.
        status: Overall run status at snapshot time.
        schema_version: Snapshot schema version (currently 1).
        current_node_ids: Node IDs currently being executed (in progress).
        completed_node_ids: Node IDs that completed successfully.
        failed_node_ids: Node IDs that failed.
        pending_node_ids: Node IDs that have not started yet.
        nodes: Per-node execution state snapshots.
        execution_context: Serialized execution context (input, permissions, etc.).
        pending_approvals: List of pending approval requests.
        compensation_state: Serialized compensation state, or None.
        created_at: When this snapshot was first created.
        updated_at: When this snapshot was last modified.
    """

    snapshot_id: str = Field(..., description="Unique snapshot identifier")
    run_id: str = Field(..., description="Parent workflow run ID")
    workflow_name: str | None = Field(default=None, description="DAG workflow name")
    status: str = Field(
        default=DagSnapshotStatus.RUNNING.value,
        description="Overall run status at snapshot time",
    )
    schema_version: int = Field(
        default=1, ge=1, description="Snapshot schema version"
    )

    current_node_ids: list[str] = Field(
        default_factory=list, description="Nodes currently in progress"
    )
    completed_node_ids: list[str] = Field(
        default_factory=list, description="Successfully completed node IDs"
    )
    failed_node_ids: list[str] = Field(
        default_factory=list, description="Failed node IDs"
    )
    pending_node_ids: list[str] = Field(
        default_factory=list, description="Node IDs not yet started"
    )

    nodes: dict[str, DagNodeSnapshot] = Field(
        default_factory=dict, description="Per-node execution state"
    )

    execution_context: dict[str, Any] = Field(
        default_factory=dict, description="Serialized execution context"
    )
    pending_approvals: list[dict[str, Any]] = Field(
        default_factory=list, description="Pending approval requests"
    )
    compensation_state: dict[str, Any] | None = Field(
        default=None, description="Serialized compensation state"
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Snapshot creation time",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last modification time",
    )

    def to_json(self) -> str:
        """Serialize the snapshot to a JSON string.

        All datetime fields are converted to ISO format strings.

        Returns:
            JSON string representation of the snapshot.
        """
        data = self.model_dump(mode="json")
        return json.dumps(data, ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, json_str: str) -> DagRunSnapshot:
        """Deserialize a snapshot from a JSON string.

        Args:
            json_str: JSON string produced by ``to_json()``.

        Returns:
            The deserialized DagRunSnapshot.

        Raises:
            ValueError: If the JSON is malformed or the data is invalid.
        """
        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid snapshot JSON: {exc}") from exc
        try:
            return cls.model_validate(data)
        except Exception as exc:
            raise ValueError(f"Invalid snapshot data: {exc}") from exc


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SnapshotWriteError(Exception):
    """Raised when snapshot persistence fails.

    This is a stable error type that callers can catch and handle
    (e.g., abort execution rather than continuing in an unrecoverable state).

    Attributes:
        run_id: The workflow run ID.
        message: Human-readable error description.
    """

    def __init__(self, *, run_id: str, message: str) -> None:
        self.run_id = run_id
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a stable error dictionary."""
        return {
            "type": "snapshot_write_error",
            "run_id": self.run_id,
            "message": self.message,
        }


class SnapshotUnsupportedVersionError(Exception):
    """Raised when a snapshot's schema_version is not supported.

    Attributes:
        run_id: The workflow run ID.
        version: The unsupported schema version.
    """

    def __init__(self, *, run_id: str, version: int) -> None:
        self.run_id = run_id
        self.version = version
        super().__init__(
            f"Snapshot schema version {version} is not supported "
            f"(run_id='{run_id}')."
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a stable error dictionary."""
        return {
            "type": "snapshot_unsupported_version",
            "run_id": self.run_id,
            "version": self.version,
        }


class SnapshotCorruptionError(Exception):
    """Raised when a stored snapshot is corrupted or unreadable.

    Attributes:
        run_id: The workflow run ID.
        message: Human-readable error description.
    """

    def __init__(self, *, run_id: str, message: str) -> None:
        self.run_id = run_id
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a stable error dictionary."""
        return {
            "type": "snapshot_corruption",
            "run_id": self.run_id,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _new_snapshot_id() -> str:
    """Generate a unique snapshot identifier."""
    import uuid

    return str(uuid.uuid4())


def snapshot_status_is_resumable(status: str) -> bool:
    """Return True if the given snapshot status allows resume.

    Resumable statuses: running, partial, failed, interrupted.
    Non-resumable: completed.

    Args:
        status: Snapshot status string.

    Returns:
        True if the run can be resumed from this snapshot.
    """
    return status in (
        DagSnapshotStatus.RUNNING.value,
        DagSnapshotStatus.PARTIAL.value,
        DagSnapshotStatus.FAILED.value,
        DagSnapshotStatus.INTERRUPTED.value,
    )
