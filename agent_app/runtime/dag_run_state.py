"""DAG execution state models — persisted workflow/node/event/compensation state.

Phase 14.0: Extends the Phase 9 run-state foundation with DAG-specific
persistence for workflow runs, node executions, events, and compensation
handlers.  Enables crash inspection and recovery planning without changing
the existing ``RunStateStore`` / ``InterruptedRun`` layer.

Phase 14.1: Adds resume models (ResumePolicy, ResumePlan, ResumeResult,
NodeResumeDecision) and extends the WorkflowStateStore protocol with
resume-planning methods for explicit DAG resume semantics.

Phase 15: Adds worker identity, run-level lease, and idempotency models
for distributed execution readiness.  Lease provides best-effort safety
for workflow-run ownership — it does NOT guarantee exactly-once execution.

Phase 16.0: Extends the WorkflowStateStore protocol with snapshot methods
for DAG execution recovery points.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum, StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, field_validator

from agent_app.runtime.dag_snapshot import DagRunSnapshot  # noqa: E402


def _default_worker_id() -> str:
    """Generate a default worker identity ID."""
    return f"worker_{uuid.uuid4().hex[:12]}"


class WorkflowStateStore(Protocol):
    """Protocol for persisting and querying DAG execution state.

    Implementations store WorkflowRunState, NodeExecutionState,
    WorkflowEventState, and CompensationExecutionState instances and
    provide lifecycle methods for each.

    Phase 14.1: Extended with resume-planning methods for explicit
    DAG resume semantics.
    """

    async def create_run(self, state: WorkflowRunState) -> None: ...
    async def update_run(self, run_id: str, **updates: Any) -> None: ...
    async def get_run(self, run_id: str) -> WorkflowRunState: ...

    async def upsert_node(self, state: NodeExecutionState) -> None: ...
    async def get_node(self, run_id: str, node_id: str) -> NodeExecutionState | None: ...
    async def list_nodes(self, run_id: str) -> list[NodeExecutionState]: ...

    async def append_event(self, event: WorkflowEventState) -> None: ...
    async def list_events(self, run_id: str) -> list[WorkflowEventState]: ...

    async def upsert_compensation(self, state: CompensationExecutionState) -> None: ...
    async def list_compensations(self, run_id: str) -> list[CompensationExecutionState]: ...

    # Phase 14.1: Resume planning
    async def build_resume_plan(
        self, run_id: str, policy: ResumePolicy | None = None
    ) -> ResumePlan: ...
    async def get_node_outputs(self, run_id: str) -> dict[str, Any]: ...

    # Phase 15: Lease management (distributed execution readiness)
    async def acquire_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> LeaseAcquireResult: ...
    async def renew_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> WorkflowRunLease: ...
    async def release_run_lease(
        self,
        run_id: str,
        worker: WorkerIdentity,
    ) -> WorkflowRunLease: ...
    async def get_run_lease(
        self,
        run_id: str,
    ) -> WorkflowRunLease | None: ...
    async def list_expired_leases(
        self,
        before: datetime | None = None,
    ) -> list[WorkflowRunLease]: ...

    # Phase 15: Idempotency
    async def put_idempotency_record(
        self,
        record: IdempotencyRecord,
    ) -> IdempotencyRecord: ...
    async def get_idempotency_record(
        self,
        key: str,
    ) -> IdempotencyRecord | None: ...
    # Phase 15.1: Atomic idempotency reservation (API-level enforcement)
    async def reserve_idempotency_key(
        self,
        record: IdempotencyRecord,
    ) -> IdempotencyRecord: ...

    # Phase 16.0: DAG execution snapshots
    async def save_run_snapshot(
        self,
        snapshot: DagRunSnapshot,
    ) -> DagRunSnapshot: ...
    async def get_latest_run_snapshot(
        self,
        run_id: str,
    ) -> DagRunSnapshot | None: ...
    async def list_run_snapshots(
        self,
        run_id: str,
    ) -> list[DagRunSnapshot]: ...
    async def delete_run_snapshots(
        self,
        run_id: str,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class WorkflowRunStatus(StrEnum):
    """Lifecycle states for a DAG workflow run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    PARTIAL = "partial"


class NodeRunStatus(StrEnum):
    """Possible execution states for a DAG node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"


class CompensationRunStatus(StrEnum):
    """Possible execution states for a compensation handler."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class WorkflowRunState(BaseModel):
    """Persistent state of a DAG workflow execution.

    Attributes:
        run_id: Unique run identifier.
        workflow_name: Name of the DAG workflow definition.
        status: Current lifecycle status.
        input: Original user input.
        output: Final workflow output (if completed).
        error: Structured error details when status is FAILED/PARTIAL.
        started_at: When execution began.
        updated_at: Last modification timestamp.
        completed_at: When execution finished (if terminal).
        metadata: Arbitrary key/value pairs for extensibility.
    """

    run_id: str = Field(..., description="Unique run identifier")
    workflow_name: str | None = Field(default=None, description="DAG workflow name")
    status: str = Field(
        default=WorkflowRunStatus.PENDING.value,
        description="Current lifecycle status",
    )
    input: Any | None = Field(default=None, description="Original user input")
    output: Any | None = Field(default=None, description="Final workflow output")
    error: dict[str, Any] | None = Field(default=None, description="Error details")
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Execution start time",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last update time",
    )
    completed_at: datetime | None = Field(
        default=None, description="Execution end time"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Extensible key/value metadata"
    )


class NodeExecutionState(BaseModel):
    """Persistent state of a single DAG node execution.

    Attributes:
        run_id: Parent workflow run identifier.
        node_id: DAG node identifier.
        node_type: Node category (agent, tool, function, subworkflow, if_else, switch).
        status: Current node execution status.
        input: Input data passed to this node.
        output: Node output (agent response, tool result, etc.).
        error: Structured error info when status is FAILED/CANCELLED/etc.
        started_at: When execution began.
        completed_at: When execution finished.
        attempts: Number of execution attempts (retries counted).
        metadata: Arbitrary key/value pairs (e.g., retry details, timeout info).
    """

    run_id: str = Field(..., description="Parent workflow run ID")
    node_id: str = Field(..., description="DAG node identifier")
    node_type: str = Field(..., description="Node category")
    status: str = Field(
        default=NodeRunStatus.PENDING.value,
        description="Current node execution status",
    )
    input: Any | None = Field(default=None, description="Node input data")
    output: Any | None = Field(default=None, description="Node output data")
    error: dict[str, Any] | None = Field(default=None, description="Error details")
    started_at: datetime | None = Field(
        default=None, description="Execution start time"
    )
    completed_at: datetime | None = Field(
        default=None, description="Execution end time"
    )
    attempts: int = Field(default=0, description="Execution attempt count")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Extensible key/value metadata"
    )


class WorkflowEventState(BaseModel):
    """A single persisted workflow or node event.

    Attributes:
        event_id: Unique event identifier.
        run_id: Parent workflow run identifier.
        node_id: Related node ID (None for workflow-level events).
        event_type: Event category (e.g., workflow.started, node.completed).
        payload: Structured event data.
        created_at: When the event was emitted.
    """

    event_id: str = Field(..., description="Unique event identifier")
    run_id: str = Field(..., description="Parent workflow run ID")
    node_id: str | None = Field(default=None, description="Related node ID")
    event_type: str = Field(..., description="Event category")
    payload: dict[str, Any] = Field(
        default_factory=dict, description="Structured event data"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Event creation time",
    )


class CompensationExecutionState(BaseModel):
    """Persistent state of a single compensation handler execution.

    Attributes:
        run_id: Parent workflow run identifier.
        node_id: The node being compensated.
        handler_name: Name/ref of the compensation function.
        status: Current compensation status.
        error: Error details if compensation failed.
        started_at: When compensation began.
        completed_at: When compensation finished.
        metadata: Arbitrary key/value pairs.
    """

    run_id: str = Field(..., description="Parent workflow run ID")
    node_id: str = Field(..., description="Node being compensated")
    handler_name: str | None = Field(
        default=None, description="Compensation function name"
    )
    status: str = Field(
        default=CompensationRunStatus.PENDING.value,
        description="Current compensation status",
    )
    error: dict[str, Any] | None = Field(default=None, description="Error details")
    started_at: datetime | None = Field(
        default=None, description="Compensation start time"
    )
    completed_at: datetime | None = Field(
        default=None, description="Compensation end time"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Extensible key/value metadata"
    )


class RecoveryPlan(BaseModel):
    """Assessment of a workflow run's recoverability after an interruption.

    Phase 14.0 provides inspection + planning only; actual resumption is
    deferred to a future phase.

    Attributes:
        run_id: The assessed workflow run.
        resumable: Whether the run can be safely resumed.
        completed_nodes: Node IDs that completed successfully.
        interrupted_nodes: Node IDs that were running but lack completed_at.
        failed_nodes: Node IDs that failed.
        compensation_started: Whether compensation was triggered.
        reason: Explanation when resumable is False.
    """

    run_id: str = Field(..., description="Assessed workflow run ID")
    resumable: bool = Field(
        default=False, description="Whether the run can be resumed"
    )
    completed_nodes: list[str] = Field(
        default_factory=list, description="Successfully completed node IDs"
    )
    interrupted_nodes: list[str] = Field(
        default_factory=list,
        description="Node IDs running without completed_at (interrupted)",
    )
    failed_nodes: list[str] = Field(
        default_factory=list, description="Failed node IDs"
    )
    compensation_started: bool = Field(
        default=False, description="Whether compensation was triggered"
    )
    reason: str | None = Field(
        default=None, description="Explanation when not resumable"
    )


# ---------------------------------------------------------------------------
# Phase 14.1: Resume models
# ---------------------------------------------------------------------------


class ResumePolicy(BaseModel):
    """Policy controlling how a persisted DAG run is resumed.

    Attributes:
        retry_failed: Whether to retry nodes that previously failed.
        retry_interrupted: Whether to retry nodes that were interrupted
            (running without completed_at).
        skip_completed: Whether to skip nodes that previously completed
            and reuse their persisted outputs.
        allow_after_compensation_started: If True, allow resuming even
            when compensation has already started.  Phase 14.1 keeps
            this as a field but the default (False) blocks forward resume.
    """

    retry_failed: bool = Field(
        default=True, description="Retry nodes that previously failed"
    )
    retry_interrupted: bool = Field(
        default=True, description="Retry nodes that were interrupted"
    )
    skip_completed: bool = Field(
        default=True, description="Skip completed nodes and reuse outputs"
    )
    allow_after_compensation_started: bool = Field(
        default=False,
        description="Allow resume after compensation has started (Phase 14.1: not implemented)",
    )


class NodeResumeDecision(BaseModel):
    """Decision for a single node during resume planning.

    Attributes:
        node_id: The DAG node identifier.
        action: What to do: "skip" (use persisted output), "retry"
            (re-execute), "run" (execute normally), or "blocked"
            (cannot proceed due to policy or upstream failure).
        reason: Human-readable explanation for the decision.
    """

    node_id: str = Field(..., description="DAG node identifier")
    action: Literal["skip", "retry", "run", "blocked"] = Field(
        ..., description="Resume action for this node"
    )
    reason: str | None = Field(
        default=None, description="Explanation for the decision"
    )


class ResumePlan(BaseModel):
    """Plan for resuming a persisted DAG workflow run.

    Attributes:
        run_id: The workflow run being resumed.
        workflow_name: Name of the DAG workflow definition.
        resumable: Whether the run can be resumed given the current state
            and policy.
        decisions: Per-node resume decisions in topological order.
        completed_nodes: Node IDs that will be skipped (already completed).
        skipped_nodes: Node IDs skipped due to upstream failure or policy.
        retry_nodes: Node IDs that will be re-executed.
        blocked_nodes: Node IDs that cannot be executed (e.g., failed
            with retry_failed=False).
        reason: Explanation when resumable is False.
    """

    run_id: str = Field(..., description="Workflow run ID")
    workflow_name: str | None = Field(
        default=None, description="DAG workflow name"
    )
    resumable: bool = Field(
        default=False, description="Whether the run can be resumed"
    )
    decisions: list[NodeResumeDecision] = Field(
        default_factory=list, description="Per-node resume decisions"
    )
    completed_nodes: list[str] = Field(
        default_factory=list, description="Node IDs skipped (already completed)"
    )
    skipped_nodes: list[str] = Field(
        default_factory=list, description="Node IDs skipped due to policy/upstream"
    )
    retry_nodes: list[str] = Field(
        default_factory=list, description="Node IDs to re-execute"
    )
    blocked_nodes: list[str] = Field(
        default_factory=list, description="Node IDs that cannot be executed"
    )
    reason: str | None = Field(
        default=None, description="Explanation when not resumable"
    )


class ResumeResult(BaseModel):
    """Result of a DAG workflow resume operation.

    Attributes:
        run_id: The resumed workflow run.
        status: Overall execution status after resume.
        resumed: Whether the resume operation completed successfully.
        skipped_nodes: Node IDs that were skipped (already completed).
        retried_nodes: Node IDs that were re-executed.
        final_output: Final workflow output after resume.
        error: Error details if resume failed.
    """

    run_id: str = Field(..., description="Workflow run ID")
    status: str = Field(..., description="Overall execution status")
    resumed: bool = Field(default=False, description="Whether resume succeeded")
    skipped_nodes: list[str] = Field(
        default_factory=list, description="Node IDs skipped during resume"
    )
    retried_nodes: list[str] = Field(
        default_factory=list, description="Node IDs re-executed during resume"
    )
    final_output: Any | None = Field(
        default=None, description="Final workflow output"
    )
    error: dict[str, Any] | None = Field(
        default=None, description="Error details if resume failed"
    )


# ---------------------------------------------------------------------------
# Phase 15: Lease models (distributed execution readiness)
# ---------------------------------------------------------------------------


class WorkerIdentity(BaseModel):
    """Identifies a worker process that may hold workflow run leases.

    Attributes:
        worker_id: Unique worker identifier (auto-generated if not provided).
        hostname: Host name of the worker machine.
        process_id: OS process ID of the worker.
        app_version: Version string of the application.
        metadata: Arbitrary key/value pairs for extensibility.
    """

    worker_id: str = Field(
        default_factory=_default_worker_id,
        description="Unique worker identifier",
    )
    hostname: str | None = Field(default=None, description="Worker host name")
    process_id: int | None = Field(default=None, description="Worker OS process ID")
    app_version: str | None = Field(default=None, description="Application version")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Extensible key/value metadata"
    )


class LeaseStatus(str, Enum):
    """Possible states of a workflow run lease."""

    ACQUIRED = "acquired"
    DENIED = "denied"
    EXPIRED = "expired"
    RELEASED = "released"


class WorkflowRunLease(BaseModel):
    """Represents an exclusive lease on a workflow run.

    A lease grants a single worker exclusive permission to execute or
    resume a workflow run.  Leases expire after a TTL and can be renewed
    by the owner.  Expired leases may be stolen by other workers.

    Attributes:
        run_id: The workflow run being leased.
        owner_id: The worker_id of the current lease owner.
        acquired_at: When the lease was first acquired (timezone-aware UTC).
        expires_at: When the lease expires (timezone-aware UTC).
        renewed_at: When the lease was last renewed (None if never renewed).
        released_at: When the lease was released (None if still held).
        version: Lease version counter (incremented on renew).
    """

    run_id: str = Field(..., description="Workflow run ID")
    owner_id: str = Field(..., description="Worker ID of the lease owner")
    acquired_at: datetime = Field(
        ..., description="Lease acquisition time (UTC)"
    )
    expires_at: datetime = Field(
        ..., description="Lease expiration time (UTC)"
    )
    renewed_at: datetime | None = Field(
        default=None, description="Last renewal time (UTC)"
    )
    released_at: datetime | None = Field(
        default=None, description="Release time (UTC), None if still held"
    )
    version: int = Field(default=1, description="Lease version counter")

    @field_validator("acquired_at", "expires_at")
    @classmethod
    def _validate_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (use UTC)")
        return v


class LeasePolicy(BaseModel):
    """Policy controlling lease acquisition and renewal behavior.

    Attributes:
        ttl_seconds: Lease time-to-live in seconds (default 300 = 5 minutes).
        allow_steal_expired: Whether a new worker can acquire an expired lease.
        renew_before_seconds: Seconds before expiry to trigger auto-renewal.
    """

    ttl_seconds: int = Field(
        default=300,
        ge=1,
        description="Lease time-to-live in seconds",
    )
    allow_steal_expired: bool = Field(
        default=True,
        description="Allow acquiring expired leases",
    )
    renew_before_seconds: int = Field(
        default=60,
        ge=0,
        description="Seconds before expiry to auto-renew",
    )


class LeaseAcquireResult(BaseModel):
    """Result of a lease acquisition attempt.

    Attributes:
        acquired: Whether the lease was successfully acquired.
        run_id: The workflow run ID.
        owner_id: The worker ID that made the request.
        lease: The lease object if acquired, None otherwise.
        reason: Explanation if acquisition was denied.
        current_owner_id: The current lease owner if denied.
        expires_at: Expiry time of the current lease if denied.
    """

    acquired: bool = Field(..., description="Whether lease was acquired")
    run_id: str = Field(..., description="Workflow run ID")
    owner_id: str = Field(..., description="Worker ID that made the request")
    lease: WorkflowRunLease | None = Field(
        default=None, description="The acquired lease, if any"
    )
    reason: str | None = Field(default=None, description="Denial reason")
    current_owner_id: str | None = Field(
        default=None, description="Current lease owner if denied"
    )
    expires_at: datetime | None = Field(
        default=None, description="Current lease expiry if denied"
    )


# ---------------------------------------------------------------------------
# Phase 15.2: Lease lost error
# ---------------------------------------------------------------------------


class LeaseLostError(Exception):
    """Raised when a workflow lease is lost during execution.

    This is a stable, catchable error type.  The DagExecutor converts
    this into a failed workflow result.

    Attributes:
        run_id: The workflow run that lost its lease.
        worker_id: The worker that lost the lease.
        last_lease: The last known lease information.
    """

    def __init__(
        self,
        *,
        run_id: str,
        worker_id: str,
        last_lease: WorkflowRunLease | None = None,
    ) -> None:
        self.run_id = run_id
        self.worker_id = worker_id
        self.last_lease = last_lease
        super().__init__(
            f"Workflow lease lost for run '{run_id}' "
            f"(worker '{worker_id}')."
        )

    def to_dict(self) -> dict:
        """Serialize to a dict for error responses."""
        return {
            "type": "lease_lost",
            "message": str(self),
            "run_id": self.run_id,
            "worker_id": self.worker_id,
        }


# ---------------------------------------------------------------------------
# Phase 15: Idempotency models
# ---------------------------------------------------------------------------


class IdempotencyRecord(BaseModel):
    """Record for idempotency key tracking.

    Allows checking whether a given idempotency key has already been used
    to create or resume a workflow run, preventing duplicate executions.

    Phase 15.1: Extended with scope and request_fingerprint for strict
    API-level idempotency enforcement.

    Attributes:
        key: The idempotency key (unique constraint within scope).
        run_id: The workflow run associated with this key.
        operation: The operation type ("execute" or "resume").
        created_at: When the record was created (timezone-aware UTC).
        result_ref: Optional reference to the result (e.g., run_id).
        scope: Scoped namespace for the key (tenant + operation).
        request_fingerprint: SHA-256 fingerprint of the request payload,
            used to detect parameter changes for the same key.
    """

    key: str = Field(..., description="Idempotency key (unique within scope)")
    run_id: str = Field(..., description="Associated workflow run ID")
    operation: str = Field(..., description="Operation type: 'execute' or 'resume'")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Record creation time (UTC)",
    )
    result_ref: str | None = Field(
        default=None, description="Result reference (e.g., run_id)"
    )
    # Phase 15.1: scope isolation and fingerprint for duplicate/mismatch detection
    scope: str | None = Field(
        default=None,
        description="Scoped namespace: '{tenant_id}:{operation}'",
    )
    request_fingerprint: str | None = Field(
        default=None,
        description="SHA-256 fingerprint of the request payload",
    )
