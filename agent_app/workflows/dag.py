"""DAG Workflow — directed acyclic graph execution engine (Phase 13 + 13.2 + 13.3)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_app.observability.events import RunEvent, RunEventType


def _now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    """Generate a unique identifier."""
    import uuid
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DagError(Exception):
    """Base error for DAG workflow failures."""


class CycleDetectedError(DagError):
    """Raised when the DAG contains a cycle."""


class NodeNotFoundError(DagError):
    """Raised when a dependency references a non-existent node."""


class DuplicateNodeIdError(DagError):
    """Raised when two nodes share the same id."""


class InvalidExecutionModeError(DagError):
    """Raised when execution_mode is not a valid value."""


class WorkflowDeadlineExceededError(DagError):
    """Raised when a DAG workflow exceeds its configured deadline_seconds."""

    def __init__(
        self,
        *,
        deadline_seconds: float,
        elapsed_seconds: float,
    ) -> None:
        self.deadline_seconds = deadline_seconds
        self.elapsed_seconds = elapsed_seconds
        super().__init__(
            f"Workflow deadline exceeded: {deadline_seconds}s limit, "
            f"{elapsed_seconds:.3f}s elapsed"
        )


class CompensationError(DagError):
    """Raised when a compensation handler fails."""
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NodeType(StrEnum):
    """Supported DAG node types."""

    AGENT = "agent"
    TOOL = "tool"
    FUNCTION = "function"
    SUBWORKFLOW = "subworkflow"
    IF_ELSE = "if_else"
    SWITCH = "switch"


class NodeExecutionStatus(StrEnum):
    """Possible execution states for a DAG node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    INTERRUPTED = "interrupted"


class CompensationStatus(str):
    """Possible execution states for a compensation handler."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class DagExecutionMode(StrEnum):
    """DAG execution mode."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


class RetryPolicy(BaseModel):
    """Retry configuration for a DAG node or workflow.

    Attributes:
        max_attempts: Maximum number of execution attempts (>= 1).
        backoff_seconds: Initial backoff delay before the first retry.
        backoff_multiplier: Multiplier applied to backoff after each retry.
        retry_on_statuses: Statuses that trigger a retry.
    """

    max_attempts: int = Field(
        default=1, ge=1, description="Maximum execution attempts (>= 1)"
    )
    backoff_seconds: float = Field(
        default=0.0, ge=0.0, description="Initial backoff delay (seconds)"
    )
    backoff_multiplier: float = Field(
        default=1.0, ge=1.0, description="Backoff multiplier per retry"
    )
    retry_on_statuses: list[NodeExecutionStatus] = Field(
        default_factory=lambda: [NodeExecutionStatus.FAILED],
        description="Statuses that trigger a retry",
    )

    @field_validator("retry_on_statuses")
    @classmethod
    def _no_retry_on_interrupt(cls, v: list[NodeExecutionStatus]) -> list[NodeExecutionStatus]:
        """Interrupted nodes (e.g. approval) should not be auto-retried."""
        if NodeExecutionStatus.INTERRUPTED in v:
            raise ValueError(
                "retry_on_statuses must not include 'interrupted' — "
                "approval-interrupted nodes require manual resolution"
            )
        return v


# ---------------------------------------------------------------------------
# Condition model (imported from condition.py — Phase 13.3)
# ---------------------------------------------------------------------------

from agent_app.workflows.condition import DagCondition  # noqa: E402

# ---------------------------------------------------------------------------
# Node types and models
# ---------------------------------------------------------------------------


class DagNode(BaseModel):
    """A single node in a DAG workflow.

    Attributes:
        id: Unique node identifier within the DAG.
        type: Node category (agent, tool, or function).
        ref: Registry name the node resolves to
             (agent name for type=agent, tool name for type=tool,
             function name for type=function).
        input: Static input overrides for this node.
        depends_on: IDs of nodes that must complete before this one runs.
        retry: Optional per-node retry policy.
        condition: Optional boolean condition gating execution.
        timeout_seconds: Optional per-node execution timeout (None = no timeout).
        permissions: Optional additional permissions required for this node
            (merged with function-level permissions for FUNCTION nodes).
    """

    id: str = Field(..., description="Unique node identifier")
    type: NodeType = Field(..., description="Node category")
    ref: str = Field(..., description="Registry name to resolve")
    input: dict[str, Any] = Field(
        default_factory=dict, description="Static input overrides"
    )
    depends_on: list[str] = Field(
        default_factory=list, description="Dependency node IDs"
    )
    retry: RetryPolicy | None = Field(
        default=None, description="Per-node retry policy"
    )
    condition: "DagCondition | None" = Field(
        default=None, description="Optional condition gating execution"
    )
    timeout_seconds: float | None = Field(
        default=None, ge=0.0, description="Per-node execution timeout (seconds)"
    )
    permissions: list[str] = Field(
        default_factory=list,
        description="Additional permissions required (merged with function-level)",
    )
    subworkflow_name: str | None = Field(
        default=None,
        description="Name of the subworkflow to execute (type=subworkflow only)",
    )
    then: list[str] = Field(
        default_factory=list,
        description="Node IDs to execute if condition is true (type=if_else only)",
    )
    else_branch: list[str] = Field(
        default_factory=list,
        description="Node IDs to execute if condition is false (type=if_else only)",
    )
    switch_expr: str | None = Field(
        default=None,
        description="Expression to evaluate for switch routing (type=switch only)",
    )
    cases: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of {value, node_ids} case definitions (type=switch only)",
    )
    compensate: dict[str, Any] | None = Field(
        default=None,
        description="Compensation handler configuration with keys: function/ref, inputs, "
        "timeout_seconds, retry",
    )


class NodeExecutionAttempt(BaseModel):
    """Record of a single execution attempt for a node.

    Attributes:
        attempt: 1-based attempt number.
        status: Outcome of this attempt.
        error: Error details if the attempt failed.
        started_at: When this attempt began.
        completed_at: When this attempt finished.
    """

    attempt: int = Field(..., description="1-based attempt number")
    status: NodeExecutionStatus = Field(..., description="Attempt outcome")
    error: dict[str, Any] | None = Field(default=None, description="Error details")
    started_at: Any | None = Field(default=None, description="Start timestamp")
    completed_at: Any | None = Field(default=None, description="End timestamp")


class NodeExecutionResult(BaseModel):
    """Result of executing a single DAG node.

    Attributes:
        node_id: The node that was executed.
        status: Execution outcome.
        output: Node output (agent response, tool result, etc.).
        error: Structured error info when status is failed/interrupted/skipped.
        started_at: When execution began.
        completed_at: When execution finished.
        attempts: All execution attempts (including retries).
    """

    node_id: str = Field(..., description="Node identifier")
    status: NodeExecutionStatus = Field(
        ..., description="Execution outcome"
    )
    output: Any | None = Field(default=None, description="Node output")
    error: dict[str, Any] | None = Field(
        default=None, description="Error details"
    )
    started_at: Any | None = Field(default=None, description="Start timestamp")
    completed_at: Any | None = Field(
        default=None, description="Completion timestamp"
    )
    attempts: list[NodeExecutionAttempt] = Field(
        default_factory=list,
        description="All execution attempts for this node",
    )


# ---------------------------------------------------------------------------
# Compensation models (Phase 13.9)
# ---------------------------------------------------------------------------


class NodeCompensationResult(BaseModel):
    """Result of executing a compensation handler for a single node.

    Attributes:
        node_id: The original node being compensated.
        status: Compensation execution outcome.
        started_at: When compensation began.
        completed_at: When compensation finished.
        attempts: Number of compensation attempts made.
        error: Error details if compensation failed.
        output: Compensation handler output (if any).
    """

    node_id: str = Field(..., description="Original node identifier")
    status: str = Field(..., description="Compensation status")
    started_at: Any | None = Field(default=None, description="Start timestamp")
    completed_at: Any | None = Field(default=None, description="Completion timestamp")
    attempts: int = Field(default=0, description="Number of attempts")
    error: dict[str, Any] | None = Field(default=None, description="Error details")
    output: Any | None = Field(default=None, description="Compensation output")


class WorkflowCompensationResult(BaseModel):
    """Aggregate result of workflow-level compensation execution.

    Attributes:
        status: Overall compensation outcome.
        compensated_nodes: Node IDs successfully compensated (in execution order).
        skipped_nodes: Node IDs skipped (no handler, wrong status, etc.).
        failed_nodes: Node IDs where compensation failed.
        results: Per-node compensation results.
    """

    status: str = Field(..., description="Overall compensation status")
    compensated_nodes: list[str] = Field(
        default_factory=list, description="Successfully compensated node IDs"
    )
    skipped_nodes: list[str] = Field(
        default_factory=list, description="Skipped compensation node IDs"
    )
    failed_nodes: list[str] = Field(
        default_factory=list, description="Failed compensation node IDs"
    )
    results: dict[str, NodeCompensationResult] = Field(
        default_factory=dict, description="Per-node compensation results"
    )


class DagWorkflow(BaseModel):
    """A directed acyclic graph of executable nodes.

    Attributes:
        name: Workflow identifier.
        nodes: Ordered list of DAG nodes.
        execution_mode: "sequential" (default) or "parallel".
        max_concurrency: Maximum concurrent node executions (None = unlimited).
        retry: Workflow-level default retry policy (overridden by node-level).
        timeout_seconds: Workflow-level default node timeout (overridden by node-level).
        deadline_seconds: Workflow-level execution deadline (seconds).
        compensation: Workflow-level compensation/rollback policy.
    """

    name: str = Field(..., description="Workflow identifier")
    nodes: list[DagNode] = Field(
        default_factory=list, description="DAG nodes"
    )
    execution_mode: DagExecutionMode = Field(
        default=DagExecutionMode.SEQUENTIAL,
        description="Execution mode: sequential or parallel",
    )
    max_concurrency: int | None = Field(
        default=None, ge=1, description="Max concurrent nodes (None = unlimited)"
    )
    retry: RetryPolicy | None = Field(
        default=None, description="Workflow-level default retry policy"
    )
    timeout_seconds: float | None = Field(
        default=None, ge=0.0, description="Workflow-level default node timeout (seconds)"
    )
    deadline_seconds: float | None = Field(
        default=None, gt=0.0, description="Workflow-level execution deadline (seconds)"
    )
    compensation: dict[str, Any] | None = Field(
        default=None,
        description="Compensation policy with keys: enabled, trigger_on, "
        "continue_on_failure, timeout_seconds",
    )

    @model_validator(mode="after")
    def _validate_node_ids(self) -> DagWorkflow:
        ids = [n.id for n in self.nodes]
        dupes = {n for n in ids if ids.count(n) > 1}
        if dupes:
            raise DuplicateNodeIdError(
                f"Duplicate node IDs: {sorted(dupes)}"
            )
        return self

    @model_validator(mode="after")
    def _validate_dependencies(self) -> DagWorkflow:
        node_ids = {n.id for n in self.nodes}
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in node_ids:
                    raise NodeNotFoundError(
                        f"Node '{node.id}' depends on '{dep}' which does not exist"
                    )
        return self

    @model_validator(mode="after")
    def _validate_no_cycles(self) -> DagWorkflow:
        """Detect cycles via Kahn's algorithm (topological sort check)."""
        node_ids = {n.id for n in self.nodes}
        in_degree: dict[str, int] = {n.id: 0 for n in self.nodes}
        adjacency: dict[str, list[str]] = {n.id: [] for n in self.nodes}

        for node in self.nodes:
            for dep in node.depends_on:
                adjacency[dep].append(node.id)
                in_degree[node.id] += 1

        queue = [nid for nid in node_ids if in_degree[nid] == 0]
        visited = 0

        while queue:
            current = queue.pop(0)
            visited += 1
            for neighbor in adjacency[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited != len(node_ids):
            raise CycleDetectedError(
                "DAG contains a cycle — all nodes must form a directed acyclic graph"
            )
        return self

    def topological_sort(self) -> list[DagNode]:
        """Return nodes in topological (execution) order.

        Raises ``CycleDetectedError`` if the graph has a cycle.
        """
        node_map = {n.id: n for n in self.nodes}
        in_degree: dict[str, int] = {n.id: 0 for n in self.nodes}
        adjacency: dict[str, list[str]] = {n.id: [] for n in self.nodes}

        for node in self.nodes:
            for dep in node.depends_on:
                adjacency[dep].append(node.id)
                in_degree[node.id] += 1

        queue = [nid for nid in node_map if in_degree[nid] == 0]
        sorted_ids: list[str] = []

        while queue:
            current = queue.pop(0)
            sorted_ids.append(current)
            for neighbor in adjacency[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_ids) != len(self.nodes):
            raise CycleDetectedError("DAG contains a cycle")

        return [node_map[nid] for nid in sorted_ids]

    def get_effective_retry(self, node_id: str) -> RetryPolicy:
        """Get the effective retry policy for a node.

        Node-level retry takes priority over workflow-level.
        Falls back to max_attempts=1 (no retry).
        """
        node = next((n for n in self.nodes if n.id == node_id), None)
        if node is not None and node.retry is not None:
            return node.retry
        if self.retry is not None:
            return self.retry
        return RetryPolicy(max_attempts=1)

    def get_effective_timeout(self, node_id: str) -> float | None:
        """Get the effective timeout for a node.

        Node-level timeout takes priority over workflow-level.
        Returns None if neither is set.
        """
        node = next((n for n in self.nodes if n.id == node_id), None)
        if node is not None and node.timeout_seconds is not None:
            return node.timeout_seconds
        return self.timeout_seconds


class IfElseResult(BaseModel):
    """Result of an if/else branch node execution.

    Attributes:
        condition: The condition expression that was evaluated.
        condition_result: True if the "then" branch was taken, False for "else".
        then_output: Output from the last node in the "then" branch (or None).
        else_output: Output from the last node in the "else" branch (or None).
        then_status: Overall status of the "then" branch execution.
        else_status: Overall status of the "else" branch execution.
        then_node_ids: Node IDs executed in the "then" branch.
        else_node_ids: Node IDs executed in the "else" branch.
    """

    condition: str = Field(..., description="Condition expression evaluated")
    condition_result: bool = Field(..., description="True = then branch, False = else branch")
    then_output: Any | None = Field(default=None, description="Last then-branch node output")
    else_output: Any | None = Field(default=None, description="Last else-branch node output")
    then_status: str = Field(default="skipped", description="Then branch overall status")
    else_status: str = Field(default="skipped", description="Else branch overall status")
    then_node_ids: list[str] = Field(
        default_factory=list, description="Node IDs in then branch"
    )
    else_node_ids: list[str] = Field(
        default_factory=list, description="Node IDs in else branch"
    )


class SwitchResult(BaseModel):
    """Result of a switch node execution.

    Attributes:
        expression: The switch expression that was evaluated.
        matched_value: The case value that matched (or None for default).
        matched_case_index: Index of the matched case (or -1 for default).
        output: Output from the last node in the matched branch (or None).
        status: Overall status of the matched branch execution.
        executed_node_ids: Node IDs that were executed in the matched branch.
    """

    expression: str = Field(..., description="Switch expression evaluated")
    matched_value: Any | None = Field(default=None, description="Matched case value")
    matched_case_index: int = Field(default=-1, description="Index of matched case (-1 = default)")
    output: Any | None = Field(default=None, description="Last executed node output")
    status: str = Field(default="skipped", description="Matched branch overall status")
    executed_node_ids: list[str] = Field(
        default_factory=list, description="Node IDs executed in matched branch"
    )


# ---------------------------------------------------------------------------
# Deadline state
# ---------------------------------------------------------------------------


class _DeadlineState:
    """Internal helper for tracking workflow-level deadline.

    Stores the absolute deadline time and provides convenience methods
    for remaining time, deadline checks, and effective timeout computation.
    """

    __slots__ = ("deadline_at", "deadline_seconds", "_loop_time")

    def __init__(
        self,
        deadline_seconds: float | None,
        loop_time: float | None = None,
    ) -> None:
        self.deadline_seconds = deadline_seconds
        self._loop_time = loop_time
        if deadline_seconds is not None and deadline_seconds > 0:
            self.deadline_at = self._now() + deadline_seconds
        else:
            self.deadline_at = None

    def _now(self) -> float:
        """Get current monotonic time."""
        if self._loop_time is not None:
            return self._loop_time
        try:
            loop = asyncio.get_running_loop()
            return loop.time()
        except RuntimeError:
            return time.perf_counter()

    def remaining(self) -> float | None:
        """Return remaining seconds until deadline, or None if no deadline."""
        if self.deadline_at is None:
            return None
        return max(0.0, self.deadline_at - self._now())

    def is_exceeded(self) -> bool:
        """Return True if the deadline has passed."""
        if self.deadline_at is None:
            return False
        return self._now() >= self.deadline_at

    def check(self) -> None:
        """Raise WorkflowDeadlineExceededError if deadline is exceeded."""
        if self.deadline_at is None:
            return
        now = self._now()
        if now >= self.deadline_at:
            raise WorkflowDeadlineExceededError(
                deadline_seconds=self.deadline_seconds,
                elapsed_seconds=now - (self.deadline_at - self.deadline_seconds),
            )

    def effective_timeout(self, node_timeout: float | None) -> float | None:
        """Compute effective timeout as min(node_timeout, remaining_deadline).

        Returns None if neither is set.
        """
        remaining = self.remaining()
        if node_timeout is None and remaining is None:
            return None
        if node_timeout is None:
            return remaining
        if remaining is None:
            return node_timeout
        return min(node_timeout, remaining)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class DagExecutor:
    """Executes a DAG workflow with sequential or parallel scheduling.

    Phase 13.1: sequential execution only.
    Phase 13.2: parallel execution with ready-queue + asyncio.gather,
                node-level retry policy with backoff, enhanced trace events.
    Phase 14.0: optional persisted execution state via state_store.

    Args:
        agent_registry: Registry of AgentSpec objects.
        tool_registry: Registry of ToolSpec objects.
        workflow_registry: Registry of Workflow objects.
        app_runner: The parent AppRunner (for agent/tool execution).
        trace_collector: Optional observability trace collector.
        state_store: Optional workflow execution state store for
            crash inspection and recovery planning (Phase 14.0).
        run_id: Optional run identifier for state store association.
    """

    def __init__(
        self,
        agent_registry: Any,
        tool_registry: Any,
        workflow_registry: Any,
        app_runner: Any = None,
        trace_collector: Any = None,
        function_registry: Any = None,
        state_store: Any = None,
        run_id: str | None = None,
        worker: Any = None,
        idempotency_key: str | None = None,
        lease_renewal_config: Any = None,
        snapshot_config: Any = None,
        compensation_config: Any = None,
        # Phase 16.2: Pluggable lease backend
        lease_backend: Any = None,
        lease_policy: Any = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.tool_registry = tool_registry
        self.workflow_registry = workflow_registry
        self.app_runner = app_runner
        self.trace_collector = trace_collector
        self.function_registry = function_registry
        self._state_store = state_store
        self._run_id = run_id
        # Phase 15: Cache the worker identity so acquire and release use the same ID
        self._worker = worker
        # Phase 15.1: Idempotency key for duplicate prevention
        self._idempotency_key = idempotency_key
        # Phase 15.2: Lease renewal config (best-effort background renewal)
        self._lease_renewal_config = lease_renewal_config
        # Phase 16.0: Snapshot config (DAG execution recovery points)
        self._snapshot_config = snapshot_config
        # Phase 16.1: Compensation persistence config
        self._compensation_config = compensation_config
        self._compensation_store: Any = None
        # Phase 16.2: Pluggable lease backend
        self._lease_backend = lease_backend
        self._lease_policy = lease_policy

    async def execute(
        self,
        dag: DagWorkflow,
        input: str,
        context: Any,
        permissions: list[str] | None = None,
        _subworkflow_chain: list[str] | None = None,
        _deadline: _DeadlineState | None = None,
    ) -> tuple[list[NodeExecutionResult], str, Any, WorkflowCompensationResult | None]:
        """Execute all nodes according to the DAG's execution mode.

        Args:
            dag: The DAG workflow to execute.
            input: User input (passed as the initial context).
            context: RunContext for this execution.
            permissions: Granted permissions.
            _subworkflow_chain: Internal: chain of parent subworkflow names
                for cycle detection.
            _deadline: Internal: inherited deadline state from parent workflow.

        Returns:
            Tuple of (node_results, overall_status, final_output, compensation_result).
            compensation_result is None if compensation is not triggered or not enabled.
        """
        from agent_app.observability.events import RunEvent, RunEventType
        from agent_app.runtime.dag_run_state import WorkflowRunState, WorkflowRunStatus

        # Phase 14.0: Create workflow run record
        if self._state_store is not None and self._run_id:
            await self._state_store.create_run(
                WorkflowRunState(
                    run_id=self._run_id,
                    workflow_name=dag.name,
                    status=WorkflowRunStatus.RUNNING.value,
                    input=input,
                    metadata={"execution_mode": dag.execution_mode.value},
                )
            )

        # Phase 15: Acquire lease before execution
        await self._acquire_lease()

        # Phase 15.1: Store input for fingerprinting, then enforce idempotency
        self._current_input = input
        await self._enforce_idempotency(context, "dag.execute")

        # Phase 15.2: Start lease renewal (best-effort background task)
        renewer = self._make_renewer()
        _lease_error: LeaseLostError | None = None
        _snapshot_error: Exception | None = None
        exec_ctx: dict[str, Any] = {}
        try:
            if renewer is not None:
                await renewer.start()

            # Phase 16.1: Initialize compensation state store
            self._init_compensation_store()

            # Phase 16.0: Save initial "running" snapshot
            self._current_dag = dag
            self._workflow_name = dag.name
            exec_ctx = {
                "input": input,
                "permissions": list(permissions or []),
                "_subworkflow_chain": list(_subworkflow_chain or []),
            }
            if self._is_snapshot_enabled():
                initial_snapshot = self._build_snapshot(
                    status="running",
                    execution_context=exec_ctx,
                )
                await self._save_snapshot(initial_snapshot)

            # Create deadline state: use inherited or create from dag config
            deadline = _deadline
            if deadline is None and dag.deadline_seconds is not None:
                deadline = _DeadlineState(dag.deadline_seconds)
            # Execute DAG nodes
            if dag.execution_mode == DagExecutionMode.PARALLEL:
                result = await self._execute_parallel(
                    dag, input, context, permissions, _subworkflow_chain, deadline
                )
            else:
                result = await self._execute_sequential(
                    dag, input, context, permissions, _subworkflow_chain, deadline
                )

            # Phase 16.0: Save completion snapshot
            if self._is_snapshot_enabled() and _snapshot_error is None:
                node_results_map = {nr.node_id: nr for nr in result[0]}
                completed_ids = [nid for nid, nr in node_results_map.items() if nr.status == NodeExecutionStatus.COMPLETED]
                failed_ids = [nid for nid, nr in node_results_map.items() if nr.status == NodeExecutionStatus.FAILED]
                final_snapshot = self._build_snapshot(
                    status=result[1],
                    execution_context=exec_ctx,
                    node_results=node_results_map,
                    completed_node_ids=completed_ids,
                    failed_node_ids=failed_ids,
                )
                await self._save_snapshot(final_snapshot)

            # Check lease_lost only if execution succeeded
            if renewer is not None and renewer.lease_lost:
                from agent_app.runtime.dag_run_state import LeaseLostError as _LLE
                _lease_error = _LLE(
                    run_id=self._run_id or "",
                    worker_id=getattr(
                        self._get_worker_sync(), "worker_id", "unknown"
                    ),
                )
            return result
        except Exception as exc:
            if self._is_snapshot_enabled() and _snapshot_error is None:
                _snapshot_error = exc
            raise
        finally:
            # Phase 16.0: Save failure snapshot if execution failed
            if self._is_snapshot_enabled() and _snapshot_error is not None:
                try:
                    failure_snapshot = self._build_snapshot(
                        status="failed",
                        execution_context=exec_ctx,
                    )
                    await self._save_snapshot(failure_snapshot)
                except Exception:
                    pass  # Best-effort: snapshot failure should not mask the original error
            # Phase 15.2: Stop lease renewal (always)
            if renewer is not None:
                try:
                    await renewer.stop()
                except Exception:
                    pass  # Best-effort: don't let stop failure mask execution result
            # Phase 15: Release lease after execution (always)
            await self._release_lease()
            # Raise lease_lost error AFTER lease is released (only if execution succeeded)
            if _lease_error is not None:
                raise _lease_error

    async def resume(
        self,
        dag: DagWorkflow,
        input: str,
        context: Any,
        permissions: list[str] | None = None,
        _subworkflow_chain: list[str] | None = None,
        _deadline: _DeadlineState | None = None,
        policy: Any = None,
    ) -> tuple[list[NodeExecutionResult], str, Any, WorkflowCompensationResult | None]:
        """Resume a previously persisted DAG workflow run.

        Phase 14.1: Loads persisted node/compensation state from the
        state_store, builds a ResumePlan, and continues execution from
        where the workflow left off.  Completed nodes are skipped and
        their persisted outputs are reused.  Interrupted/failed nodes
        are re-executed according to the resume policy.

        Args:
            dag: The DAG workflow definition.
            input: Original user input.
            context: RunContext for this execution.
            permissions: Granted permissions.
            _subworkflow_chain: Internal: subworkflow chain for cycle detection.
            _deadline: Internal: inherited deadline state.
            policy: Optional ResumePolicy controlling retry/skip behavior.

        Returns:
            Tuple of (node_results, overall_status, final_output, compensation_result).

        Raises:
            DagError: If the run cannot be resumed (compensation started,
                no state_store, run not found, etc.).
        """
        from agent_app.runtime.dag_run_state import (
            NodeExecutionState,
            NodeRunStatus,
            ResumePlan,
            ResumePolicy,
            WorkflowRunState,
            WorkflowRunStatus,
        )

        if policy is None:
            policy = ResumePolicy()

        if self._state_store is None or self._run_id is None:
            raise DagError(
                "Cannot resume workflow run: no state_store configured. "
                "Configure runtime.workflow_state to enable resume."
            )

        # -- Load persisted state --
        try:
            run = await self._state_store.get_run(self._run_id)
        except KeyError:
            raise DagError(
                f"Cannot resume workflow run: run_id '{self._run_id}' not found."
            )

        persisted_nodes = await self._state_store.list_nodes(self._run_id)
        persisted_compensations = await self._state_store.list_compensations(self._run_id)

        # Phase 16.0: Load latest snapshot to accelerate resume
        snapshot_used = False
        latest_snapshot = None
        if self._is_snapshot_enabled():
            try:
                latest_snapshot = await self._state_store.get_latest_run_snapshot(self._run_id)
            except Exception:
                latest_snapshot = None  # Fall through to existing resume logic

            if latest_snapshot is not None:
                # Validate schema version
                if latest_snapshot.schema_version != 1:
                    raise DagError({
                        "type": "snapshot_unsupported_version",
                        "message": (
                            f"Snapshot schema version {latest_snapshot.schema_version} "
                            f"is not supported (run_id='{self._run_id}')."
                        ),
                        "run_id": self._run_id,
                        "version": latest_snapshot.schema_version,
                    })
                # Validate run_id
                if latest_snapshot.run_id != self._run_id:
                    raise DagError({
                        "type": "snapshot_run_id_mismatch",
                        "message": (
                            f"Snapshot run_id '{latest_snapshot.run_id}' does not match "
                            f"current run_id '{self._run_id}'."
                        ),
                    })
                # Check resumability
                from agent_app.runtime.dag_snapshot import snapshot_status_is_resumable
                if not snapshot_status_is_resumable(latest_snapshot.status):
                    if latest_snapshot.status == "completed":
                        # Idempotent return for completed runs
                        return (
                            [],
                            WorkflowRunStatus.COMPLETED.value,
                            None,
                            None,
                        )
                    raise DagError({
                        "type": "snapshot_not_resumable",
                        "message": (
                            f"Snapshot status '{latest_snapshot.status}' is not resumable "
                            f"(run_id='{self._run_id}')."
                        ),
                        "run_id": self._run_id,
                        "status": latest_snapshot.status,
                    })
                # Use snapshot to rebuild execution context
                snapshot_used = True
                # Rebuild execution context from snapshot
                snap_ctx = latest_snapshot.execution_context or {}
                self._workflow_name = latest_snapshot.workflow_name or dag.name
                dag = dag  # Use the provided dag definition

        # Phase 16.1: Check compensation state for resume
        compensation_resumed = False
        if self._is_compensation_persistence_enabled() and self._is_resume_incomplete_compensation():
            self._init_compensation_store()
            existing_comp_state = await self._get_compensation_state()
            if existing_comp_state is not None:
                from agent_app.runtime.compensation_state import (
                    CompensationRunStatus,
                )
                # Resume incomplete compensation (running or partial_failed)
                if existing_comp_state.status in (
                    CompensationRunStatus.RUNNING.value,
                    CompensationRunStatus.PARTIAL_FAILED.value,
                ):
                    # Check if there are any retryable failed actions
                    retryable = existing_comp_state.get_failed_retryable_actions()
                    pending = existing_comp_state.get_pending_actions()
                    if retryable or pending:
                        # Resume compensation execution
                        comp_result = await self._resume_compensation(
                            dag=dag,
                            input=input,
                            context=context,
                            permissions=permissions,
                            execution_context={},
                            existing_state=existing_comp_state,
                            original_failure_type=(
                                existing_comp_state.actions.get(
                                    next(iter(existing_comp_state.actions)),
                                {}
                            ).error or {}
                            ).get("type", "unknown") if existing_comp_state.actions else "unknown",
                        )
                        compensation_resumed = True

        # -- Record workflow.resume_started event --
        if self.trace_collector is not None:
            from agent_app.observability.events import RunEvent, RunEventType
            await self.trace_collector.record(
                RunEvent(
                    event_type=RunEventType.WORKFLOW_STARTED,  # reuse WORKFLOW_STARTED for resume
                    trace_id=getattr(context, "trace_id", "") or "",
                    run_id=self._run_id,
                    user_id=getattr(context, "user_id", ""),
                    tenant_id=getattr(context, "tenant_id", ""),
                    workflow_name=dag.name,
                    workflow_type="dag",
                    status="running",
                    data={"resume": True, "original_status": run.status},
                )
            )

        # Phase 14.1: Persist workflow resume started event
        await self._persist_event(
            context, "workflow.resume_started",
            payload={"original_status": run.status, "policy": policy.model_dump()}
        )

        # -- Build resume plan --
        resume_plan = await self._state_store.build_resume_plan(
            self._run_id, policy
        )

        if not resume_plan.resumable:
            await self._persist_event(
                context, "workflow.resume_failed",
                payload={"reason": resume_plan.reason}
            )
            raise DagError(
                f"Cannot resume workflow run: {resume_plan.reason}"
            )

        # Phase 15.1: Store input for fingerprinting, then enforce idempotency
        self._current_input = input
        await self._enforce_idempotency(context, "dag.resume")

        # Phase 15: Acquire lease before resume execution
        await self._acquire_lease()

        # Phase 15.2: Start lease renewal (best-effort background task)
        renewer = self._make_renewer()
        _lease_error: "LeaseLostError | None" = None
        try:
            if renewer is not None:
                await renewer.start()
            try:
                # -- Prepare execution state --
                node_map = {n.id: n for n in dag.nodes}
                sorted_nodes = dag.topological_sort()
                results: dict[str, NodeExecutionResult] = {}
                execution_context: dict[str, Any] = {
                    "input": input,
                    "permissions": list(permissions or []),
                    "_subworkflow_chain": list(_subworkflow_chain or []),
                }
                overall_status = "completed"
                final_output: Any = None
                retried_nodes: list[str] = []

                # -- Inject persisted outputs for completed/skipped nodes --
                node_output_map: dict[str, Any] = {}
                for pnode in persisted_nodes:
                    if pnode.output is not None:
                        node_output_map[pnode.node_id] = pnode.output

                # -- Record skipped_completed events and build initial results --
                for decision in resume_plan.decisions:
                    if decision.action == "skip":
                        node_id = decision.node_id
                        execution_context[f"node:{node_id}"] = node_output_map.get(node_id)
                        # Record event for skipped completed node
                        if self.trace_collector is not None:
                            from agent_app.observability.events import RunEvent, RunEventType
                            dag_node = node_map.get(node_id)
                            if dag_node is not None:
                                agent_node = dag_node.ref
                                await self.trace_collector.record(
                                    RunEvent(
                                        event_type=RunEventType.NODE_COMPLETED,
                                        trace_id=getattr(context, "trace_id", "") or "",
                                        run_id=self._run_id,
                                        user_id=getattr(context, "user_id", ""),
                                        tenant_id=getattr(context, "tenant_id", ""),
                                        node_id=node_id,
                                        node_type=dag_node.type.value,
                                        agent_name=agent_node if dag_node.type == NodeType.AGENT else None,
                                        tool_name=agent_node if dag_node.type == NodeType.TOOL else None,
                                        status="skipped",
                                        data={"reason": "already_completed"},
                                    )
                                )

                # -- Execute nodes in topological order --
                for node in sorted_nodes:
                    node_id = node.id
                    if node_id in results:
                        continue
                    decision = next(
                        (d for d in resume_plan.decisions if d.node_id == node_id),
                        None,
                    )
                    if decision and decision.action == "skip":
                        # Use persisted status (may be SKIPPED, COMPLETED, etc.)
                        persisted = next(
                            (n for n in persisted_nodes if n.node_id == node_id),
                            None,
                        )
                        if persisted is not None:
                            results[node_id] = NodeExecutionResult(
                                node_id=node_id,
                                status=NodeRunStatus(persisted.status),
                                output=node_output_map.get(node_id) or persisted.output,
                                attempts=[],
                            )
                        else:
                            results[node_id] = NodeExecutionResult(
                                node_id=node_id,
                                status=NodeRunStatus.COMPLETED,
                                output=node_output_map.get(node_id),
                                attempts=[],
                            )
                        continue
                    if decision and decision.action == "blocked":
                        results[node_id] = NodeExecutionResult(
                            node_id=node_id,
                            status=NodeRunStatus.FAILED,
                            error={"type": "ResumeBlocked", "reason": decision.reason},
                            attempts=[],
                        )
                        overall_status = "failed"
                        continue

                    # Execute the node
                    dag_node = node_map.get(node_id)
                    if dag_node is None:
                        continue

                    node_result = await self._execute_node_with_retry(
                        dag_node, dag, input, context, permissions, execution_context
                    )
                    results[node_id] = node_result

                    if node_result.status == NodeRunStatus.FAILED:
                        overall_status = "failed"

                # -- Determine final output --
                if sorted_nodes:
                    last_node = sorted_nodes[-1]
                    last_result = results.get(last_node.id)
                    if last_result is not None:
                        final_output = last_result.output

                # -- Build end state --
                end_state: dict[str, Any] = {"status": overall_status}
                if overall_status == "completed":
                    end_state["output"] = final_output
                    end_state["completed_at"] = datetime.now(timezone.utc)
                else:
                    error_info = None
                    for nr in results.values():
                        if nr.status.value == "failed" and nr.error:
                            error_info = nr.error
                            break
                    if error_info is None and isinstance(final_output, dict):
                        error_info = final_output
                    end_state["error"] = error_info
            except Exception:
                raise  # Re-raise execution errors; outer finally handles cleanup
            await self._state_store.update_run(self._run_id, **end_state)
            resume_event_type = (
                "workflow.resume_completed"
                if overall_status == "completed"
                else "workflow.resume_failed"
            )
            await self._persist_event(
                context, resume_event_type,
                payload={"overall_status": overall_status, "retried_nodes": retried_nodes}
            )

            # Phase 13.9: Execute compensation if workflow failed and compensation is enabled
            compensation_result = None
            if overall_status in ("failed", "interrupted"):
                if self._should_trigger_compensation(dag, overall_status):
                    compensation_result = await self._execute_compensation(
                        dag, results, input, context, permissions, execution_context,
                        final_output.get("type", "unknown") if isinstance(final_output, dict) else "unknown"
                    )

            # Check lease_lost only if execution succeeded
            if renewer is not None and renewer.lease_lost:
                from agent_app.runtime.dag_run_state import LeaseLostError as _LLE
                _lease_error = _LLE(
                    run_id=self._run_id or "",
                    worker_id=getattr(
                        self._get_worker_sync(), "worker_id", "unknown"
                    ),
                )
            return list(results.values()), overall_status, final_output, compensation_result
        finally:
            # Phase 15.2: Stop lease renewal (always)
            if renewer is not None:
                try:
                    await renewer.stop()
                except Exception:
                    pass  # Best-effort: don't let stop failure mask execution result
            # Phase 15: Release lease after resume (always)
            await self._release_lease()
            # Raise lease_lost error AFTER lease is released
            if _lease_error is not None:
                raise _lease_error

    async def _get_worker(self) -> Any:
        """Return the worker identity for this executor.

        Uses the explicitly provided worker, or creates and caches a default one.
        The cached identity ensures acquire and release use the same worker_id.
        """
        if self._worker is not None:
            return self._worker
        if not hasattr(self, "_cached_worker"):
            from agent_app.runtime.dag_run_state import WorkerIdentity
            self._cached_worker = WorkerIdentity()
        return self._cached_worker

    async def _enforce_idempotency(
        self, context: Any, operation: str, extra_payload: dict[str, Any] | None = None,
    ) -> None:
        """Enforce idempotency key if one is configured (Phase 15.1).

        Called before any side-effect-producing operation (execute or resume).
        Uses the state store's ``reserve_idempotency_key`` for atomic
        enforcement.  If no ``idempotency_key`` is set, or no state store
        is configured, this is a no-op.

        Args:
            context: RunContext for this execution.
            operation: Operation identifier (e.g. "dag.execute").
            extra_payload: Additional fields to include in the fingerprint.

        Raises:
            DagError: Wraps IdempotencyError when a conflict is detected.
        """
        if self._idempotency_key is None or self._state_store is None or self._run_id is None:
            return

        from agent_app.runtime.dag_run_state import IdempotencyRecord
        from agent_app.runtime.idempotency import (
            build_execute_payload,
            build_resume_payload,
            compute_request_fingerprint,
            compute_scope,
            IdempotencyKeyMismatchError,
            DuplicateIdempotencyKeyError,
        )

        tenant_id = getattr(context, "tenant_id", "default") or "default"
        user_id = getattr(context, "user_id", "anonymous") or "anonymous"
        session_id = getattr(context, "session_id", None)
        permissions = getattr(context, "permissions", []) or []

        # Use cached workflow name (set by WorkflowExecutor._run_dag)
        workflow_name = getattr(self, "_workflow_name", None)

        if operation.endswith(".resume"):
            payload = build_resume_payload(
                run_id=self._run_id,
                input=getattr(self, "_current_input", "") or "",
                tenant_id=tenant_id,
                user_id=user_id,
                permissions=permissions,
            )
        else:
            payload = build_execute_payload(
                workflow_name=workflow_name,
                input=getattr(self, "_current_input", "") or "",
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                run_id=self._run_id,
                permissions=permissions,
            )

        # Merge extra payload fields
        if extra_payload:
            payload.update(extra_payload)

        scope = compute_scope(tenant_id, operation)
        fingerprint = compute_request_fingerprint(payload)

        record = IdempotencyRecord(
            key=self._idempotency_key,
            run_id=self._run_id,
            operation=operation,
            scope=scope,
            request_fingerprint=fingerprint,
        )

        try:
            await self._state_store.reserve_idempotency_key(record)
        except DuplicateIdempotencyKeyError as exc:
            raise DagError({
                "type": "idempotency_duplicate",
                "message": exc.message,
                "idempotency_key": exc.idempotency_key,
                "existing_run_id": exc.existing_run_id,
            }) from exc
        except IdempotencyKeyMismatchError as exc:
            raise DagError({
                "type": "idempotency_key_reuse_mismatch",
                "message": exc.message,
                "idempotency_key": exc.idempotency_key,
                "existing_run_id": exc.existing_run_id,
            }) from exc

    async def _acquire_lease(self) -> None:
        """Acquire a lease on the current run if a lease backend is configured.

        Phase 16.2: Uses an explicit lease_backend if provided, otherwise
        falls back to the state_store.  Raises DagError if lease
        acquisition fails.
        """
        lease_backend = self._get_lease_backend()
        if lease_backend is None or self._run_id is None:
            return
        from agent_app.runtime.dag_run_state import LeasePolicy, WorkerIdentity

        worker = await self._get_worker()
        policy = self._lease_policy or LeasePolicy()
        result = await lease_backend.acquire_run_lease(
            self._run_id, worker, policy
        )
        if not result.acquired:
            raise DagError(
                f"Cannot execute workflow run: {result.reason}"
            )
        # Record lease event
        await self._persist_event(
            None, "workflow.lease_acquired",
            payload={
                "owner_id": worker.worker_id,
                "lease_version": result.lease.version if result.lease else 1,
            }
        )

    def _get_lease_backend(self) -> Any:
        """Get the effective lease backend.

        Priority:
        1. Explicit ``self._lease_backend`` (Phase 16.2)
        2. ``self._state_store`` (Phase 15 legacy — used as StateStoreLeaseBackend)
        3. None (no lease)
        """
        if self._lease_backend is not None:
            return self._lease_backend
        return self._state_store

    async def _release_lease(self) -> None:
        """Release the lease on the current run if we hold one.

        Phase 16.2: Uses the effective lease backend.
        """
        lease_backend = self._get_lease_backend()
        if lease_backend is None or self._run_id is None:
            return
        from agent_app.runtime.dag_run_state import WorkerIdentity

        worker = await self._get_worker()
        try:
            released = await lease_backend.release_run_lease(
                self._run_id, worker
            )
            await self._persist_event(
                None, "workflow.lease_released",
                payload={"owner_id": worker.worker_id}
            )
            return released
        except KeyError:
            # No lease held or already released — not an error
            pass

    def _make_renewer(self) -> Any:
        """Create a LeaseRenewer for the current execution context.

        Phase 16.2: Uses the effective lease backend if available.
        Returns None if lease renewal is not configured or not applicable.
        """
        lease_backend = self._get_lease_backend()
        if lease_backend is None or self._run_id is None:
            return None
        cfg = self._lease_renewal_config
        if cfg is not None and not getattr(cfg, "renew_enabled", True):
            return None
        # Get worker_id synchronously
        worker_id = getattr(
            getattr(self, "_worker", None), "worker_id", None
        ) or getattr(
            getattr(self, "_cached_worker", None), "worker_id", None
        ) or "unknown"
        # Get TTL from config or use default
        if cfg is not None and getattr(cfg, "ttl_seconds", None):
            ttl = cfg.ttl_seconds
        else:
            ttl = 300  # default, matches LeasePolicy default
        # Get interval from config or derive from ttl
        if cfg is not None and getattr(cfg, "renew_interval_seconds", None):
            interval = cfg.renew_interval_seconds
        else:
            interval = None  # LeaseRenewer will compute ttl / 3
        try:
            from agent_app.runtime.lease_renewer import LeaseRenewer
            # Phase 16.2: Pass lease_backend if it's not a state_store
            # (state_store is handled via legacy param in LeaseRenewer)
            if hasattr(lease_backend, "acquire_run_lease") and not hasattr(lease_backend, "_runs"):
                # It's a standalone lease backend (InMemory/SQLite), not a state store
                return LeaseRenewer(
                    lease_backend=lease_backend,
                    run_id=self._run_id,
                    worker_id=worker_id,
                    ttl_seconds=ttl,
                    interval_seconds=interval,
                )
            else:
                # It's a state store — use legacy param
                return LeaseRenewer(
                    state_store=lease_backend,
                    run_id=self._run_id,
                    worker_id=worker_id,
                    ttl_seconds=ttl,
                    interval_seconds=interval,
                )
        except ImportError:
            return None

    def _get_worker_sync(self) -> Any:
        """Get the worker identity synchronously (for error reporting).

        Uses the explicitly provided worker or the cached default.
        """
        if self._worker is not None:
            return self._worker
        if hasattr(self, "_cached_worker"):
            return self._cached_worker
        # Fallback: create one
        from agent_app.runtime.dag_run_state import WorkerIdentity
        self._cached_worker = WorkerIdentity()
        return self._cached_worker

    # -- Snapshot helpers (Phase 16.0) --

    async def _save_snapshot(self, snapshot: DagRunSnapshot) -> None:
        """Persist a DAG execution snapshot.

        Raises SnapshotWriteError if persistence fails.  Callers should
        treat snapshot write failure as a stable error rather than
        silently continuing.

        Args:
            snapshot: The snapshot to persist.

        Raises:
            SnapshotWriteError: If the snapshot cannot be saved.
        """
        if self._state_store is None or self._run_id is None:
            return
        from agent_app.runtime.dag_snapshot import SnapshotWriteError
        try:
            await self._state_store.save_run_snapshot(snapshot)
        except SnapshotWriteError:
            raise
        except Exception as exc:
            raise SnapshotWriteError(
                run_id=self._run_id,
                message=f"Failed to save snapshot: {exc}",
            ) from exc

    def _build_snapshot(
        self,
        status: str,
        execution_context: dict[str, Any],
        node_results: dict[str, NodeExecutionResult] | None = None,
        completed_node_ids: list[str] | None = None,
        failed_node_ids: list[str] | None = None,
        current_node_ids: list[str] | None = None,
        pending_approvals: list[dict[str, Any]] | None = None,
        compensation_state: dict[str, Any] | None = None,
        schema_version: int = 1,
    ) -> DagRunSnapshot:
        """Build a DagRunSnapshot from current execution state.

        Args:
            status: Overall workflow run status.
            execution_context: Current execution context dict.
            node_results: Current node execution results.
            completed_node_ids: Node IDs that completed successfully.
            failed_node_ids: Node IDs that failed.
            current_node_ids: Node IDs currently executing.
            pending_approvals: Pending approval requests.
            compensation_state: Serialized compensation state.
            schema_version: Snapshot schema version.

        Returns:
            A DagRunSnapshot capturing the current state.
        """
        from agent_app.runtime.dag_snapshot import (
            DagNodeSnapshot,
            DagRunSnapshot,
            DagSnapshotStatus,
            _new_snapshot_id,
            _now,
        )

        now = _now()
        nodes: dict[str, DagNodeSnapshot] = {}
        if node_results:
            for nid, result in node_results.items():
                nodes[nid] = DagNodeSnapshot(
                    node_id=nid,
                    status=result.status.value,
                    attempts=len(result.attempts),
                    output=result.output,
                    error=result.error,
                    started_at=getattr(result, "started_at", None),
                    completed_at=getattr(result, "completed_at", None),
                )

        # Compute pending nodes from dag if available
        pending: list[str] = []
        if hasattr(self, "_current_dag") and node_results:
            all_node_ids = {n.id for n in self._current_dag.nodes}
            pending = sorted(all_node_ids - set(node_results.keys()))

        return DagRunSnapshot(
            snapshot_id=_new_snapshot_id(),
            run_id=self._run_id or "",
            workflow_name=getattr(self, "_workflow_name", None),
            status=status,
            schema_version=schema_version,
            current_node_ids=current_node_ids or [],
            completed_node_ids=completed_node_ids or [],
            failed_node_ids=failed_node_ids or [],
            pending_node_ids=pending,
            nodes=nodes,
            execution_context=execution_context,
            pending_approvals=pending_approvals or [],
            compensation_state=compensation_state,
            created_at=now,
            updated_at=now,
        )

    def _is_snapshot_enabled(self) -> bool:
        """Check if snapshot persistence is enabled.

        Returns True if a state store is configured and snapshots are
        not explicitly disabled in the config.

        Returns:
            True if snapshots should be saved.
        """
        if self._state_store is None or self._run_id is None:
            return False
        cfg = self._snapshot_config
        if cfg is None:
            return True  # Default: enabled when state_store is available
        return bool(getattr(cfg, "enabled", True))

    async def _maybe_save_snapshot(
        self,
        status: str,
        results: dict[str, NodeExecutionResult],
        current_node_id: str | None = None,
    ) -> None:
        """Save a snapshot if snapshot persistence is enabled.

        This is a best-effort operation — failures are logged but do not
        propagate to the caller (the snapshot is a recovery aid, not
        critical for correctness).

        Args:
            status: Overall workflow status.
            results: Current node execution results.
            current_node_id: Node currently executing (added to current_node_ids).
        """
        if not self._is_snapshot_enabled():
            return
        try:
            completed_ids = [nid for nid, nr in results.items() if nr.status == NodeExecutionStatus.COMPLETED]
            failed_ids = [nid for nid, nr in results.items() if nr.status == NodeExecutionStatus.FAILED]
            current_ids = [current_node_id] if current_node_id else []
            exec_ctx = getattr(self, "_execution_context", {})
            snapshot = self._build_snapshot(
                status=status,
                execution_context=exec_ctx,
                node_results=results,
                completed_node_ids=completed_ids,
                failed_node_ids=failed_ids,
                current_node_ids=current_ids,
            )
            await self._save_snapshot(snapshot)
        except Exception as exc:
            # Best-effort: log but don't propagate
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                "Failed to save snapshot for run '%s': %s",
                self._run_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Phase 16.1: Compensation state persistence
    # ------------------------------------------------------------------

    def _init_compensation_store(self) -> None:
        """Lazy-initialize the compensation state store from config."""
        if self._compensation_store is not None:
            return
        cfg = self._compensation_config
        if cfg is None:
            # Default: enabled with memory store
            from agent_app.runtime.compensation_store import (
                InMemoryCompensationStateStore,
            )
            self._compensation_store = InMemoryCompensationStateStore()
            return
        if not getattr(cfg, "enabled", True):
            self._compensation_store = None
            return
        from agent_app.runtime.compensation_store import (
            create_compensation_state_store,
        )
        store_type = getattr(cfg, "store", "memory")
        path = getattr(cfg, "path", None)
        self._compensation_store = create_compensation_state_store(
            store_type=store_type,
            db_path=path,
        )

    def _is_compensation_persistence_enabled(self) -> bool:
        """Check if compensation state persistence is enabled."""
        # Ensure store is initialized so the check is accurate
        self._init_compensation_store()
        cfg = self._compensation_config
        if cfg is not None and not bool(getattr(cfg, "enabled", True)):
            return False
        return self._compensation_store is not None

    def _get_max_compensation_attempts(self) -> int:
        """Get the max attempts for compensation actions from config."""
        cfg = self._compensation_config
        if cfg is not None:
            return getattr(cfg, "max_attempts", 1)
        return 1

    def _is_resume_incomplete_compensation(self) -> bool:
        """Check if resume should continue incomplete compensation."""
        cfg = self._compensation_config
        if cfg is not None:
            return bool(getattr(cfg, "resume_incomplete", True))
        return True

    def _create_compensation_state(
        self,
        dag: DagWorkflow,
        candidates: list[str],
        original_failure_type: str,
    ) -> Any:
        """Create a new CompensationExecutionState for a compensation run.

        Args:
            dag: The workflow definition.
            candidates: Node IDs being compensated (in order).
            original_failure_type: Type of failure that triggered compensation.

        Returns:
            A new CompensationExecutionState with one action per candidate.
        """
        from agent_app.runtime.compensation_state import (
            CompensationActionState,
            CompensationActionStatus,
            CompensationExecutionState,
            CompensationRunStatus,
        )

        now = datetime.now(timezone.utc)
        actions: dict[str, CompensationActionState] = {}
        action_order: list[str] = []

        for node_id in candidates:
            node = next((n for n in dag.nodes if n.id == node_id), None)
            if node is None or node.compensate is None:
                continue
            action = CompensationActionState(
                run_id=self._run_id or "",
                workflow_name=getattr(self, "_workflow_name", None) or dag.name,
                node_id=node_id,
                compensating_for_node_id=node_id,
                status=CompensationActionStatus.PENDING.value,
                max_attempts=self._get_max_compensation_attempts(),
                input=None,
                output=None,
                error=None,
                idempotency_key=None,
            )
            actions[action.action_id] = action
            action_order.append(action.action_id)

        state = CompensationExecutionState(
            run_id=self._run_id or "",
            workflow_name=getattr(self, "_workflow_name", None) or dag.name,
            status=CompensationRunStatus.PENDING.value,
            actions=actions,
            action_order=action_order,
            created_at=now,
            updated_at=now,
        )
        return state

    async def _save_compensation_state(
        self, state: Any
    ) -> None:
        """Persist compensation state to the compensation store.

        Compensation state write failure is a stable error — it is NOT
        silently swallowed.

        Args:
            state: CompensationExecutionState to persist.

        Raises:
            SnapshotWriteError: If persistence fails (wraps underlying error).
        """
        if not self._is_compensation_persistence_enabled():
            return
        try:
            await self._compensation_store.save_compensation_state(state)
        except Exception as exc:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(
                "Failed to save compensation state for run '%s': %s",
                self._run_id,
                exc,
            )
            from agent_app.runtime.dag_snapshot import SnapshotWriteError
            raise SnapshotWriteError(
                run_id=self._run_id or "",
                message=f"Failed to save compensation state: {exc}",
            )

    async def _update_compensation_action(
        self,
        action_id: str,
        status: str,
        output: Any = None,
        error: dict[str, Any] | Exception | None = None,
    ) -> Any:
        """Update a single compensation action in the store.

        Args:
            action_id: The action to update.
            status: New status.
            output: Handler output (for completed actions).
            error: Error details (for failed actions).

        Returns:
            Updated CompensationExecutionState.

        Raises:
            KeyError: If the run or action is not found.
        """
        if not self._is_compensation_persistence_enabled():
            return None
        state = await self._compensation_store.get_compensation_state(
            self._run_id or ""
        )
        if state is None:
            return None
        action = state.get_action(action_id)
        if action is None:
            return state
        action.status = status
        action.output = output
        if isinstance(error, Exception):
            action.error = {"type": type(error).__name__, "message": str(error)}
        elif error is not None:
            action.error = error
        if status == "completed":
            action.mark_completed(output)
        elif status == "failed":
            action.mark_failed(error)
        elif status == "running":
            action.mark_running()
        return await self._compensation_store.update_compensation_action(
            self._run_id or "", action
        )

    async def _get_compensation_state(self) -> Any | None:
        """Get the compensation state for the current run from the store."""
        if not self._is_compensation_persistence_enabled():
            return None
        if self._compensation_store is None:
            return None
        return await self._compensation_store.get_compensation_state(
            self._run_id or ""
        )

    async def _persist_node_state(
        self,
        context: Any,
        dag: DagWorkflow,
        node: DagNode,
        status: str,
        output: Any = None,
        error: dict[str, Any] | None = None,
        attempts: int = 0,
    ) -> None:
        """Persist node execution state to the state store.

        Phase 14.0: Records node lifecycle transitions. No-op if no
        state_store is configured.

        Args:
            context: RunContext for this execution.
            dag: The DAG workflow being executed.
            node: The DAG node whose state to persist.
            status: Current node status string.
            output: Node output (if completed).
            error: Error details (if failed/interrupted).
            attempts: Number of execution attempts.
        """
        if self._state_store is None or self._run_id is None:
            return
        from agent_app.runtime.dag_run_state import NodeExecutionState, NodeRunStatus

        now = _now()
        node_state = NodeExecutionState(
            run_id=self._run_id,
            node_id=node.id,
            node_type=node.type.value,
            status=status,
            input=node.input,
            output=output,
            error=error,
            started_at=now,
            completed_at=now if status in (
                NodeRunStatus.COMPLETED.value,
                NodeRunStatus.FAILED.value,
                NodeRunStatus.SKIPPED.value,
                NodeRunStatus.CANCELLED.value,
            ) else None,
            attempts=attempts,
            metadata={"dag_name": dag.name},
        )
        await self._state_store.upsert_node(node_state)

    async def _persist_event(
        self,
        context: Any,
        event_type: str,
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Persist a workflow event to the state store.

        Args:
            context: RunContext for this execution.
            event_type: Event category string.
            node_id: Related node ID (None for workflow-level events).
            payload: Structured event data.
        """
        if self._state_store is None or self._run_id is None:
            return
        from agent_app.runtime.dag_run_state import WorkflowEventState

        await self._state_store.append_event(
            WorkflowEventState(
                event_id=_new_id(),
                run_id=self._run_id,
                node_id=node_id,
                event_type=event_type,
                payload=payload or {},
            )
        )

    async def _execute_sequential(
        self,
        dag: DagWorkflow,
        input: str,
        context: Any,
        permissions: list[str] | None,
        _subworkflow_chain: list[str] | None = None,
        deadline: _DeadlineState | None = None,
    ) -> tuple[list[NodeExecutionResult], str, Any, WorkflowCompensationResult | None]:
        """Phase 13.1 behavior: topological sort, then one-by-one."""
        sorted_nodes = dag.topological_sort()
        results: dict[str, NodeExecutionResult] = {}
        execution_context: dict[str, Any] = {
            "input": input,
            "permissions": list(permissions or []),
            "_subworkflow_chain": list(_subworkflow_chain or []),
        }
        overall_status = "completed"
        final_output: Any = None

        # -- Record dag.started --
        if self.trace_collector is not None:
            await self.trace_collector.record(
                RunEvent(
                    event_type=RunEventType.WORKFLOW_STARTED,
                    trace_id=context.trace_id or "",
                    run_id=context.run_id,
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    workflow_name=dag.name,
                    workflow_type="dag",
                    data={"execution_mode": "sequential"},
                )
            )

        # Phase 14.0: Record workflow started event
        await self._persist_event(
            context, "workflow.started", payload={"execution_mode": "sequential"}
        )

        for node in sorted_nodes:
            # Phase 13.3: Check upstream status and condition before executing
            skip_result = self._should_skip_node(node, results)
            if skip_result is not None:
                results[node.id] = skip_result
                execution_context[f"node:{node.id}"] = None
                overall_status, final_output = self._propagate_status(
                    skip_result.status.value, overall_status, None
                )
                if overall_status in ("failed", "interrupted"):
                    self._mark_downstream_skipped(
                        node.id, sorted_nodes, results, dag, execution_context
                    )
                    break
                continue

            # Phase 13.3: Check condition with event recording
            cond_result = await self._evaluate_condition(
                node, results, context, dag
            )
            if cond_result is not None:
                results[node.id] = cond_result
                execution_context[f"node:{node.id}"] = None
                overall_status, final_output = self._propagate_status(
                    cond_result.status.value, overall_status, None
                )
                if overall_status in ("failed", "interrupted"):
                    self._mark_downstream_skipped(
                        node.id, sorted_nodes, results, dag, execution_context
                    )
                    break
                continue

            # Phase 13.8: Check workflow deadline before executing node
            if deadline is not None and deadline.is_exceeded():
                # Record deadline exceeded event
                if self.trace_collector is not None:
                    completed_ids = [nid for nid, r in results.items() if r.status == NodeExecutionStatus.COMPLETED]
                    pending_ids = [n.id for n in sorted_nodes if n.id not in results]
                    await self._record_deadline_exceeded_event(
                        context, dag, deadline, completed_ids, [], pending_ids,
                    )
                # Mark remaining nodes as skipped
                for remaining_node in sorted_nodes:
                    if remaining_node.id not in results:
                        results[remaining_node.id] = NodeExecutionResult(
                            node_id=remaining_node.id,
                            status=NodeExecutionStatus.SKIPPED,
                            error={
                                "type": "workflow_deadline_exceeded",
                                "message": "workflow_deadline_exceeded",
                                "deadline_seconds": deadline.deadline_seconds,
                            },
                        )
                        execution_context[f"node:{remaining_node.id}"] = None
                overall_status = "failed"
                final_output = {
                    "type": "workflow_deadline_exceeded",
                    "deadline_seconds": deadline.deadline_seconds,
                    "elapsed_seconds": deadline.deadline_seconds,
                }
                break

            node_result = await self._execute_node_with_retry(
                node, dag, input, context, permissions, execution_context, deadline=deadline
            )
            results[node.id] = node_result
            execution_context[f"node:{node.id}"] = node_result.output
            # Store full NodeExecutionResult for condition evaluators (IF_ELSE/SWITCH)
            execution_context[f"_result:{node.id}"] = node_result

            # Phase 14.0: Persist node state
            await self._persist_node_state(
                context, dag, node,
                status=node_result.status.value,
                output=node_result.output,
                error=node_result.error,
                attempts=len(node_result.attempts),
            )

            # Record node lifecycle event for sequential mode
            # (skip timeout events — already recorded in _execute_node_with_retry)
            status_str = node_result.status.value
            is_timeout = (
                node_result.status == NodeExecutionStatus.FAILED
                and node_result.attempts
                and node_result.attempts[-1].error
                and node_result.attempts[-1].error.get("type") == "timeout"
            )
            error_info = node_result.error if node_result.status in (
                NodeExecutionStatus.FAILED,
                NodeExecutionStatus.INTERRUPTED,
            ) else None
            if not is_timeout:
                await self._record_node_event(
                    context, dag, node, status_str, error=error_info,
                )

            overall_status, final_output = self._propagate_status(
                node_result.status.value, overall_status, node_result.output
            )
            if overall_status in ("failed", "interrupted"):
                # Mark remaining unstarted nodes as skipped
                self._mark_downstream_skipped(
                    node.id, sorted_nodes, results, dag, execution_context
                )
                # Phase 16.0: Save snapshot after failure/interruption
                await self._maybe_save_snapshot(
                    overall_status, results, current_node_id=node.id,
                )
                break

            # Phase 16.0: Save snapshot after successful node completion
            await self._maybe_save_snapshot(
                overall_status, results, current_node_id=None,
            )

        # -- Record dag.completed / failed / interrupted --
        if self.trace_collector is not None:
            wf_event_type = self._dag_wf_event_type(overall_status)
            await self.trace_collector.record(
                RunEvent(
                    event_type=wf_event_type,
                    trace_id=context.trace_id or "",
                    run_id=context.run_id,
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    workflow_name=dag.name,
                    workflow_type="dag",
                    status=overall_status,
                )
            )

        # Phase 14.0: Persist final workflow status and record event
        if self._state_store is not None and self._run_id:
            from agent_app.runtime.dag_run_state import WorkflowRunStatus
            end_status = _map_workflow_status(overall_status)
            end_state: dict[str, Any] = {
                "status": end_status.value,
                "output": final_output,
            }
            if overall_status == "failed":
                error_info = None
                for nr in results.values():
                    if nr.status.value == "failed" and nr.error:
                        error_info = nr.error
                        break
                if error_info is None and isinstance(final_output, dict):
                    error_info = final_output
                end_state["error"] = error_info
            await self._state_store.update_run(self._run_id, **end_state)
            await self._persist_event(
                context, f"workflow.{end_status.value}",
                payload={"overall_status": overall_status}
            )

        # Phase 13.9: Execute compensation if workflow failed and compensation is enabled
        compensation_result = None
        if overall_status in ("failed", "interrupted"):
            if self._should_trigger_compensation(dag, overall_status):
                compensation_result = await self._execute_compensation(
                    dag, results, input, context, permissions, execution_context,
                    final_output.get("type", "unknown") if isinstance(final_output, dict) else "unknown"
                )

        return list(results.values()), overall_status, final_output, compensation_result

    async def _execute_parallel(
        self,
        dag: DagWorkflow,
        input: str,
        context: Any,
        permissions: list[str] | None,
        _subworkflow_chain: list[str] | None = None,
        deadline: _DeadlineState | None = None,
    ) -> tuple[list[NodeExecutionResult], str, Any]:
        """Phase 13.2: parallel execution with ready-queue scheduling."""
        # -- Record dag.started --
        if self.trace_collector is not None:
            await self.trace_collector.record(
                RunEvent(
                    event_type=RunEventType.WORKFLOW_STARTED,
                    trace_id=context.trace_id or "",
                    run_id=context.run_id,
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    workflow_name=dag.name,
                    workflow_type="dag",
                    data={
                        "execution_mode": "parallel",
                        "max_concurrency": dag.max_concurrency,
                    },
                )
            )

        # Phase 14.0: Record workflow started event
        await self._persist_event(
            context, "workflow.started",
            payload={"execution_mode": "parallel", "max_concurrency": dag.max_concurrency}
        )

        # Build dependency maps
        node_map = {n.id: n for n in dag.nodes}
        downstream: dict[str, list[str]] = {n.id: [] for n in dag.nodes}
        dep_count: dict[str, int] = {n.id: len(n.depends_on) for n in dag.nodes}

        for node in dag.nodes:
            for dep in node.depends_on:
                downstream[dep].append(node.id)

        # State tracking
        results: dict[str, NodeExecutionResult] = {}
        execution_context: dict[str, Any] = {
            "input": input,
            "permissions": list(permissions or []),
            "_subworkflow_chain": list(_subworkflow_chain or []),
        }
        completed_status: dict[str, str] = {}  # node_id -> "completed" | "failed" | "interrupted"
        overall_status = "completed"
        final_output: Any = None
        interruption_occurred = False

        # Concurrency control
        semaphore: asyncio.Semaphore | None = None
        if dag.max_concurrency is not None and dag.max_concurrency > 0:
            semaphore = asyncio.Semaphore(dag.max_concurrency)

        # Lock for shared state mutations
        lock = asyncio.Lock()

        async def _run_node(node: DagNode) -> NodeExecutionResult:
            """Execute a single node (with retry), thread-safe."""
            async with semaphore if semaphore else _null_ctx():
                return await self._execute_node_with_retry(
                    node, dag, input, context, permissions, execution_context, lock, deadline=deadline
                )

        # Ready set: nodes with all dependencies satisfied
        ready: set[str] = {
            nid for nid, count in dep_count.items() if count == 0
        }
        running: set[str] = set()
        done: set[str] = set()

        deadline_exceeded = False

        while ready or running:
            # Phase 13.8: Check deadline before scheduling
            if deadline is not None and deadline.is_exceeded() and not deadline_exceeded:
                deadline_exceeded = True
                # Record deadline exceeded event
                if self.trace_collector is not None:
                    completed_ids = [nid for nid in done if nid in results and results[nid].status == NodeExecutionStatus.COMPLETED]
                    running_ids = list(running)
                    pending_ids = [nid for nid in ready if nid not in done and nid not in running]
                    await self._record_deadline_exceeded_event(
                        context, dag, deadline, completed_ids, running_ids, pending_ids,
                    )

            # If deadline exceeded or interruption occurred, don't schedule new nodes
            if deadline_exceeded or interruption_occurred:
                # Let running nodes finish, but mark rest as skipped
                async with lock:
                    for nid in list(ready):
                        if nid not in done and nid not in running:
                            node = node_map[nid]
                            skip_reason = "workflow_deadline_exceeded" if deadline_exceeded else "Upstream node interrupted"
                            skip_error_type = "workflow_deadline_exceeded" if deadline_exceeded else "skipped"
                            results[nid] = NodeExecutionResult(
                                node_id=nid,
                                status=NodeExecutionStatus.SKIPPED,
                                error={"type": skip_error_type, "message": skip_reason},
                            )
                            done.add(nid)
                            ready.discard(nid)
                            if self.trace_collector is not None:
                                await self.trace_collector.record(
                                    RunEvent(
                                        event_type=RunEventType.NODE_CANCELLED_BY_DEADLINE if deadline_exceeded else RunEventType.TOOL_FAILED,
                                        trace_id=context.trace_id or "",
                                        run_id=context.run_id,
                                        user_id=context.user_id,
                                        tenant_id=context.tenant_id,
                                        workflow_name=dag.name,
                                        tool_name=node.ref if node.type == NodeType.TOOL else None,
                                        agent_name=node.ref if node.type == NodeType.AGENT else None,
                                        status="skipped",
                                        data={"node_id": nid, "reason": skip_reason},
                                    )
                                )
                if not running:
                    break
                await asyncio.sleep(0.01)
                continue

            if not ready:
                # Nothing ready but still running — wait
                if running:
                    await asyncio.sleep(0.01)
                continue

            # Launch all ready nodes concurrently
            current_batch = list(ready)
            ready.clear()
            running.update(current_batch)

            # Record node.started events for batch
            if self.trace_collector is not None:
                for nid in current_batch:
                    node = node_map[nid]
                    if node.type == NodeType.AGENT:
                        start_event_type = RunEventType.AGENT_STARTED
                    elif node.type == NodeType.TOOL:
                        start_event_type = RunEventType.TOOL_STARTED
                    else:  # FUNCTION
                        start_event_type = RunEventType.NODE_STARTED
                    await self.trace_collector.record(
                        RunEvent(
                            event_type=start_event_type,
                            trace_id=context.trace_id or "",
                            run_id=context.run_id,
                            user_id=context.user_id,
                            tenant_id=context.tenant_id,
                            workflow_name=dag.name,
                            agent_name=node.ref if node.type == NodeType.AGENT else None,
                            tool_name=node.ref if node.type == NodeType.TOOL else None,
                            data={"node_id": nid},
                        )
                    )

            tasks = {nid: asyncio.create_task(_run_node(node_map[nid])) for nid in current_batch}

            # Phase 13.8: Wait with deadline awareness
            remaining = deadline.remaining() if deadline is not None else None
            deadline_hit = False

            if remaining is not None and remaining <= 0:
                # Deadline already exceeded before launching
                deadline_hit = True
            else:
                done_tasks, pending_tasks = await asyncio.wait(
                    tasks.values(),
                    timeout=remaining,
                    return_when=asyncio.ALL_COMPLETED,
                )
                if pending_tasks:
                    deadline_hit = True
                    # Cancel pending tasks (best-effort)
                    for t in pending_tasks:
                        t.cancel()
                    # Wait for cancellations to settle
                    if pending_tasks:
                        await asyncio.gather(*pending_tasks, return_exceptions=True)

            # Process results
            for nid in current_batch:
                task = tasks[nid]
                if task.done() and not task.cancelled():
                    try:
                        node_result = task.result()
                    except Exception:
                        node_result = NodeExecutionResult(
                            node_id=nid,
                            status=NodeExecutionStatus.FAILED,
                            error={"type": "task_error", "message": "Task raised an exception"},
                        )
                elif deadline_hit:
                    # Node was cancelled by deadline or never completed
                    node_result = NodeExecutionResult(
                        node_id=nid,
                        status=NodeExecutionStatus.FAILED,
                        error={
                            "type": "workflow_deadline_exceeded",
                            "message": "workflow_deadline_exceeded",
                            "deadline_seconds": deadline.deadline_seconds if deadline else None,
                        },
                    )
                    # Record node cancelled by deadline event
                    if self.trace_collector is not None:
                        node = node_map[nid]
                        await self._record_node_event(
                            context, dag, node, "cancelled_by_deadline",
                            error={"type": "workflow_deadline_exceeded", "message": "workflow_deadline_exceeded"},
                        )
                else:
                    node_result = NodeExecutionResult(
                        node_id=nid,
                        status=NodeExecutionStatus.FAILED,
                        error={"type": "task_error", "message": "Task cancelled unexpectedly"},
                    )
                running.discard(nid)
                done.add(nid)
                results[nid] = node_result
                execution_context[f"node:{nid}"] = node_result.output
                # Store full NodeExecutionResult for condition evaluators (IF_ELSE/SWITCH)
                execution_context[f"_result:{nid}"] = node_result
                status_str = node_result.status.value
                completed_status[nid] = status_str

                # Phase 14.0: Persist node state (parallel mode)
                node_for_persist = node_map.get(nid)
                if node_for_persist is not None:
                    await self._persist_node_state(
                        context, dag, node_for_persist,
                        status=node_result.status.value,
                        output=node_result.output,
                        error=node_result.error,
                        attempts=len(node_result.attempts),
                    )

                # Record node completion event
                if self.trace_collector is not None:
                    node = node_map[nid]
                    if node.type == NodeType.AGENT:
                        event_map = {
                            "completed": RunEventType.AGENT_COMPLETED,
                            "failed": RunEventType.AGENT_FAILED,
                            "interrupted": RunEventType.AGENT_COMPLETED,
                            "skipped": RunEventType.AGENT_FAILED,
                        }
                    elif node.type == NodeType.TOOL:
                        event_map = {
                            "completed": RunEventType.TOOL_COMPLETED,
                            "failed": RunEventType.TOOL_FAILED,
                            "interrupted": RunEventType.TOOL_APPROVAL_REQUIRED,
                            "skipped": RunEventType.TOOL_FAILED,
                        }
                    else:  # FUNCTION
                        event_map = {
                            "completed": RunEventType.NODE_COMPLETED,
                            "failed": RunEventType.NODE_FAILED,
                            "interrupted": RunEventType.NODE_FAILED,
                            "skipped": RunEventType.NODE_SKIPPED,
                        }
                    await self.trace_collector.record(
                        RunEvent(
                            event_type=event_map.get(status_str, RunEventType.NODE_COMPLETED),
                            trace_id=context.trace_id or "",
                            run_id=context.run_id,
                            user_id=context.user_id,
                            tenant_id=context.tenant_id,
                            workflow_name=dag.name,
                            agent_name=node.ref if node.type == NodeType.AGENT else None,
                            tool_name=node.ref if node.type == NodeType.TOOL else None,
                            status=status_str,
                            error=node_result.error,
                            data={"node_id": nid},
                        )
                    )

                if node_result.status == NodeExecutionStatus.FAILED:
                    if deadline_hit:
                        overall_status = "failed"
                        final_output = {
                            "type": "workflow_deadline_exceeded",
                            "deadline_seconds": deadline.deadline_seconds if deadline else None,
                            "elapsed_seconds": deadline.deadline_seconds if deadline else None,
                        }
                    else:
                        overall_status = "failed"
                        final_output = node_result.output
                elif node_result.status == NodeExecutionStatus.INTERRUPTED:
                    overall_status = "interrupted"
                    final_output = node_result.output
                    interruption_occurred = True
                elif node_result.status == NodeExecutionStatus.COMPLETED:
                    # Only promote to completed if no worse status seen yet
                    if overall_status not in ("failed", "interrupted"):
                        overall_status = "completed"
                        final_output = node_result.output

                # Phase 16.0: Save snapshot after each node completes in parallel mode
                await self._maybe_save_snapshot(
                    overall_status, results, current_node_id=nid,
                )

                # Update downstream dependency counts
                for downstream_id in downstream[nid]:
                    dep_count[downstream_id] -= 1
                    if dep_count[downstream_id] == 0:
                        ds_node = node_map[downstream_id]
                        # Phase 13.3: Check upstream status
                        skip_result = self._should_skip_node(ds_node, results)
                        if skip_result is not None:
                            results[downstream_id] = skip_result
                            done.add(downstream_id)
                            completed_status[downstream_id] = skip_result.status.value
                            execution_context[f"node:{downstream_id}"] = None
                            if self.trace_collector is not None:
                                await self._record_node_event(
                                    context, dag, ds_node,
                                    skip_result.status.value,
                                    error=skip_result.error,
                                )
                            # Propagate failed/condition_error status
                            if skip_result.status == NodeExecutionStatus.FAILED:
                                overall_status = "failed"
                                final_output = None
                        else:
                            # Phase 13.3: Check condition with event recording
                            cond_result = await self._evaluate_condition(
                                ds_node, results, context, dag
                            )
                            if cond_result is not None:
                                results[downstream_id] = cond_result
                                done.add(downstream_id)
                                completed_status[downstream_id] = cond_result.status.value
                                execution_context[f"node:{downstream_id}"] = None
                                if self.trace_collector is not None:
                                    await self._record_node_event(
                                        context, dag, ds_node,
                                        cond_result.status.value,
                                        error=cond_result.error,
                                    )
                                if cond_result.status == NodeExecutionStatus.FAILED:
                                    overall_status = "failed"
                                    final_output = None
                            else:
                                ready.add(downstream_id)

        # -- Record dag.completed / failed / interrupted --
        if self.trace_collector is not None:
            wf_event_type = self._dag_wf_event_type(overall_status)
            await self.trace_collector.record(
                RunEvent(
                    event_type=wf_event_type,
                    trace_id=context.trace_id or "",
                    run_id=context.run_id,
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    workflow_name=dag.name,
                    workflow_type="dag",
                    status=overall_status,
                )
            )

        # Phase 14.0: Persist final workflow status and record event
        if self._state_store is not None and self._run_id:
            from agent_app.runtime.dag_run_state import WorkflowRunStatus
            end_status = _map_workflow_status(overall_status)
            end_state: dict[str, Any] = {
                "status": end_status.value,
                "output": final_output,
            }
            if overall_status == "failed":
                error_info = None
                for nr in results.values():
                    if nr.status.value == "failed" and nr.error:
                        error_info = nr.error
                        break
                if error_info is None and isinstance(final_output, dict):
                    error_info = final_output
                end_state["error"] = error_info
            await self._state_store.update_run(self._run_id, **end_state)
            await self._persist_event(
                context, f"workflow.{end_status.value}",
                payload={"overall_status": overall_status}
            )

        # Phase 13.9: Execute compensation if workflow failed and compensation is enabled
        compensation_result = None
        if overall_status in ("failed", "interrupted"):
            if self._should_trigger_compensation(dag, overall_status):
                compensation_result = await self._execute_compensation(
                    dag, results, input, context, permissions, execution_context,
                    final_output.get("type", "unknown") if isinstance(final_output, dict) else "unknown"
                )

        return list(results.values()), overall_status, final_output, compensation_result

    async def _execute_node_with_retry(
        self,
        node: DagNode,
        dag: DagWorkflow,
        input: str,
        context: Any,
        permissions: list[str] | None,
        execution_context: dict[str, Any],
        lock: asyncio.Lock | None = None,
        deadline: _DeadlineState | None = None,
    ) -> NodeExecutionResult:
        """Execute a node with retry policy.

        Returns the final NodeExecutionResult after all attempts.
        """
        retry_policy = dag.get_effective_retry(node.id)
        max_attempts = retry_policy.max_attempts

        attempts: list[NodeExecutionAttempt] = []
        final_result: NodeExecutionResult | None = None

        for attempt_num in range(1, max_attempts + 1):
            # Phase 13.8: Check deadline before retry
            if deadline is not None and deadline.is_exceeded():
                # Deadline exceeded — don't start new attempt
                if final_result is not None:
                    return final_result
                return NodeExecutionResult(
                    node_id=node.id,
                    status=NodeExecutionStatus.FAILED,
                    error={
                        "type": "workflow_deadline_exceeded",
                        "message": "workflow_deadline_exceeded",
                        "deadline_seconds": deadline.deadline_seconds,
                    },
                )

            # Backoff before retry (not on first attempt)
            if attempt_num > 1 and retry_policy.backoff_seconds > 0:
                backoff = (
                    retry_policy.backoff_seconds
                    * (retry_policy.backoff_multiplier ** (attempt_num - 2))
                )
                # Phase 13.8: Cap backoff to remaining deadline
                if deadline is not None:
                    remaining = deadline.remaining()
                    if remaining is not None and backoff > remaining:
                        backoff = remaining
                # Record retry_scheduled event
                if self.trace_collector is not None:
                    from agent_app.observability.events import RunEvent, RunEventType
                    await self.trace_collector.record(
                        RunEvent(
                            event_type=RunEventType.TOOL_FAILED,  # reuse existing type
                            trace_id=context.trace_id or "",
                            run_id=context.run_id,
                            user_id=context.user_id,
                            tenant_id=context.tenant_id,
                            workflow_name=dag.name,
                            agent_name=node.ref if node.type == NodeType.AGENT else None,
                            tool_name=node.ref if node.type == NodeType.TOOL else None,
                            data={
                                "node_id": node.id,
                                "node_type": node.type.value,
                                "retry_scheduled": True,
                                "attempt": attempt_num,
                                "backoff_seconds": backoff,
                            },
                        )
                    )
                await asyncio.sleep(backoff)

            # Record retry_started event
            if self.trace_collector is not None and attempt_num > 1:
                from agent_app.observability.events import RunEventType
                await self.trace_collector.record(
                    RunEvent(
                        event_type=RunEventType.TOOL_FAILED,  # reuse existing type
                        trace_id=context.trace_id or "",
                        run_id=context.run_id,
                        user_id=context.user_id,
                        tenant_id=context.tenant_id,
                        workflow_name=dag.name,
                        agent_name=node.ref if node.type == NodeType.AGENT else None,
                        tool_name=node.ref if node.type == NodeType.TOOL else None,
                        data={
                            "node_id": node.id,
                            "node_type": node.type.value,
                            "retry_started": True,
                            "attempt": attempt_num,
                        },
                    )
                )

            started_at = time.perf_counter()
            # Phase 13.8: Use deadline-aware effective timeout
            node_timeout = dag.get_effective_timeout(node.id)
            if deadline is not None:
                effective_timeout = deadline.effective_timeout(node_timeout)
            else:
                effective_timeout = node_timeout
            timeout_occurred = False
            try:
                if effective_timeout is not None:
                    output, status, node_exc = await asyncio.wait_for(
                        self._execute_node(node, input, context, permissions, execution_context, dag, deadline=deadline),
                        timeout=effective_timeout,
                    )
                else:
                    output, status, node_exc = await self._execute_node(
                        node, input, context, permissions, execution_context, dag, deadline=deadline
                    )
            except asyncio.TimeoutError:
                output = None
                status = "failed"
                node_exc = DagError(
                    f"Node '{node.id}' timed out after {effective_timeout} seconds"
                )
                timeout_occurred = True
            completed_at = time.perf_counter()

            error_info: dict[str, Any] | None = None
            if status in ("failed", "interrupted"):
                if isinstance(node_exc, Exception):
                    error_info = {
                        "type": type(node_exc).__name__,
                        "message": str(node_exc),
                        "node_id": node.id,
                    }
                    # Phase 13.3: tag timeout errors
                    if timeout_occurred:
                        error_info["type"] = "timeout"
                    # Phase 13.5: tag permission denied errors
                    elif (
                        isinstance(node_exc, DagError)
                        and isinstance(node_exc.args[0] if node_exc.args else None, dict)
                        and node_exc.args[0].get("type") == "permission_denied"
                    ):
                        error_info["type"] = "permission_denied"
                        error_info["missing_permissions"] = node_exc.args[0].get(
                            "missing_permissions", []
                        )
                    # Phase 13.6: tag subworkflow_failed errors
                    elif (
                        isinstance(node_exc, DagError)
                        and isinstance(node_exc.args[0] if node_exc.args else None, dict)
                        and node_exc.args[0].get("type") == "subworkflow_failed"
                    ):
                        error_info["type"] = "subworkflow_failed"
                        error_info["workflow"] = node_exc.args[0].get("workflow")
                        failed_node = node_exc.args[0].get("failed_node")
                        if failed_node:
                            error_info["failed_node"] = failed_node
                elif final_result is not None and final_result.error:
                    error_info = final_result.error
                else:
                    error_info = {"type": status, "message": status, "node_id": node.id}
                    if timeout_occurred:
                        error_info["type"] = "timeout"

            attempt_record = NodeExecutionAttempt(
                attempt=attempt_num,
                status=NodeExecutionStatus(status),
                error=error_info,
                started_at=started_at,
                completed_at=completed_at,
            )
            attempts.append(attempt_record)

            # Phase 13.3: Record timeout event
            if timeout_occurred and self.trace_collector is not None:
                await self._record_node_event(
                    context, dag, node, "timeout",
                    error=error_info,
                    extra_data={"timeout_seconds": effective_timeout, "attempt": attempt_num},
                )

            node_status = NodeExecutionStatus(status)
            final_result = NodeExecutionResult(
                node_id=node.id,
                status=node_status,
                output=output,
                error=error_info,
                started_at=started_at,
                completed_at=completed_at,
                attempts=list(attempts),
            )

            # Check if we should stop retrying
            if node_status == NodeExecutionStatus.COMPLETED:
                break
            elif node_status == NodeExecutionStatus.INTERRUPTED:
                # Never retry interrupted nodes
                break
            elif node_status == NodeExecutionStatus.FAILED:
                # Check if retry policy includes this status
                if node_status not in retry_policy.retry_on_statuses:
                    break
                if attempt_num >= max_attempts:
                    # Record retry_exhausted event
                    if self.trace_collector is not None:
                        from agent_app.observability.events import RunEvent, RunEventType
                        await self.trace_collector.record(
                            RunEvent(
                                event_type=RunEventType.TOOL_FAILED,
                                trace_id=context.trace_id or "",
                                run_id=context.run_id,
                                user_id=context.user_id,
                                tenant_id=context.tenant_id,
                                workflow_name=dag.name,
                                agent_name=node.ref if node.type == NodeType.AGENT else None,
                                tool_name=node.ref if node.type == NodeType.TOOL else None,
                                data={
                                    "node_id": node.id,
                                    "node_type": node.type.value,
                                    "retry_exhausted": True,
                                    "attempts": attempt_num,
                                },
                            )
                        )
                    break

        assert final_result is not None
        return final_result

    def _propagate_status(
        self,
        status_str: str,
        overall_status: str,
        output: Any,
    ) -> tuple[str, Any]:
        """Update overall status based on a node's result."""
        if status_str == "failed" and overall_status != "interrupted":
            return "failed", output
        elif status_str == "interrupted":
            return "interrupted", output
        else:
            return overall_status, output

    def _mark_downstream_skipped(
        self,
        failed_node_id: str,
        sorted_nodes: list[DagNode],
        results: dict[str, NodeExecutionResult],
        dag: DagWorkflow,
        execution_context: dict[str, Any],
    ) -> None:
        """Mark downstream nodes as skipped after a failure/interruption."""
        failed_and_skipped: set[str] = {failed_node_id}
        changed = True
        while changed:
            changed = False
            for node in sorted_nodes:
                if node.id in failed_and_skipped:
                    continue
                if any(dep in failed_and_skipped for dep in node.depends_on):
                    if node.id not in results:
                        cause = "interrupted" if any(
                            results.get(d, NodeExecutionResult(node_id=d, status=NodeExecutionStatus.INTERRUPTED)).status == NodeExecutionStatus.INTERRUPTED
                            for d in node.depends_on
                            if d in results
                        ) else "failed"
                        results[node.id] = NodeExecutionResult(
                            node_id=node.id,
                            status=NodeExecutionStatus.SKIPPED,
                            error={"type": "skipped", "message": f"Upstream node {cause}"},
                        )
                        execution_context[f"node:{node.id}"] = None
                        failed_and_skipped.add(node.id)
                        changed = True

    def _should_skip_node(
        self,
        node: DagNode,
        results: dict[str, NodeExecutionResult],
    ) -> NodeExecutionResult | None:
        """Check if a node should be skipped due to upstream status.

        Checks if any dependency is interrupted, failed, or skipped.
        Does NOT check the node's condition — use _evaluate_condition for that.

        Returns:
            A SKIPPED NodeExecutionResult if an upstream issue is found,
            or None if all upstream nodes are healthy.
        """
        for dep_id in node.depends_on:
            dep_result = results.get(dep_id)
            if dep_result is None:
                continue
            if dep_result.status == NodeExecutionStatus.INTERRUPTED:
                return NodeExecutionResult(
                    node_id=node.id,
                    status=NodeExecutionStatus.SKIPPED,
                    error={"type": "skipped", "message": "Upstream node interrupted"},
                )
            elif dep_result.status == NodeExecutionStatus.FAILED:
                return NodeExecutionResult(
                    node_id=node.id,
                    status=NodeExecutionStatus.SKIPPED,
                    error={"type": "skipped", "message": "Upstream node failed"},
                )
            elif dep_result.status == NodeExecutionStatus.SKIPPED:
                return NodeExecutionResult(
                    node_id=node.id,
                    status=NodeExecutionStatus.SKIPPED,
                    error={"type": "skipped", "message": "Upstream skipped"},
                )
        return None

    async def _evaluate_condition(
        self,
        node: DagNode,
        results: dict[str, NodeExecutionResult],
        context: Any,
        dag: DagWorkflow,
    ) -> NodeExecutionResult | None:
        """Evaluate a node's condition expression, recording events.

        Returns:
            SKIPPED if condition is false,
            FAILED if condition has a parse/evaluation error,
            None if condition is true or not set.
        """
        if node.condition is None:
            return None

        from agent_app.workflows.condition import (
            ConditionEvaluationError,
            evaluate_condition,
        )

        try:
            cond_value = evaluate_condition(node.condition, results)
        except ConditionEvaluationError as exc:
            if self.trace_collector is not None:
                await self._record_node_event(
                    context, dag, node, "condition_evaluated",
                    error={"type": "condition_error", "message": str(exc)},
                    extra_data={"expr": node.condition.expr, "result": False},
                )
            return NodeExecutionResult(
                node_id=node.id,
                status=NodeExecutionStatus.FAILED,
                error={"type": "condition_error", "message": str(exc)},
            )

        # Record condition evaluation event
        if self.trace_collector is not None:
            await self._record_node_event(
                context, dag, node, "condition_evaluated",
                extra_data={"expr": node.condition.expr, "result": cond_value},
            )

        if not cond_value:
            return NodeExecutionResult(
                node_id=node.id,
                status=NodeExecutionStatus.SKIPPED,
                error={"type": "skipped", "message": "Condition evaluated to false"},
            )
        return None

    async def _record_node_event(
        self,
        context: Any,
        dag: DagWorkflow,
        node: DagNode,
        status: str,
        error: dict[str, Any] | None = None,
        extra_data: dict[str, Any] | None = None,
    ) -> None:
        """Record a node lifecycle event to the trace collector."""
        if self.trace_collector is None:
            return
        from agent_app.observability.events import RunEvent, RunEventType

        event_type_map = {
            "completed": RunEventType.NODE_COMPLETED,
            "failed": RunEventType.NODE_FAILED,
            "skipped": RunEventType.NODE_SKIPPED,
            "interrupted": RunEventType.NODE_SKIPPED,
            "condition_evaluated": RunEventType.NODE_CONDITION_EVAL,
            "timeout": RunEventType.NODE_TIMEOUT,
        }
        data: dict[str, Any] = {"node_id": node.id}
        if node.type == NodeType.FUNCTION:
            data["function"] = node.ref
        if extra_data:
            data.update(extra_data)

        await self.trace_collector.record(
            RunEvent(
                event_type=event_type_map.get(status, RunEventType.NODE_COMPLETED),
                trace_id=context.trace_id or "",
                run_id=context.run_id,
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                workflow_name=dag.name,
                agent_name=node.ref if node.type == NodeType.AGENT else None,
                tool_name=node.ref if node.type == NodeType.TOOL else None,
                status=status,
                error=error,
                data=data,
            )
        )

    def _build_node_input(
        self, node: DagNode, context: dict[str, Any]
    ) -> str:
        """Build the input string for a node.

        Merges static node input with upstream node outputs.
        Upstream outputs are available as ``node:<node_id>`` in context.
        """
        parts: list[str] = []

        # Start with the original user input
        original_input = context.get("input", "")
        if original_input:
            parts.append(str(original_input))

        # Add upstream outputs for context
        upstream_outputs = []
        for key, value in context.items():
            if key.startswith("node:") and value is not None:
                upstream_outputs.append(f"[{key[5:]}]: {value}")

        if upstream_outputs:
            parts.append("\n\nUpstream results:\n" + "\n".join(upstream_outputs))

        # Override with static node input if provided
        if node.input:
            static_parts = []
            for k, v in node.input.items():
                static_parts.append(f"{k}={v}")
            if static_parts:
                parts.append("\nNode overrides: " + ", ".join(static_parts))

        return "\n".join(parts) if parts else ""

    async def _execute_node(
        self,
        node: DagNode,
        node_input: str,
        context: Any,
        permissions: list[str] | None,
        execution_context: dict[str, Any] | None = None,
        dag: DagWorkflow | None = None,
        deadline: _DeadlineState | None = None,
    ) -> tuple[Any, str, Exception | None]:
        """Execute a single node.

        Returns (output, status, exception) where status is one of:
        "completed", "failed", "interrupted".
        """
        if node.type == NodeType.AGENT:
            return await self._execute_agent_node(node, node_input, context, permissions)
        elif node.type == NodeType.TOOL:
            return await self._execute_tool_node(node, node_input, context, permissions)
        elif node.type == NodeType.FUNCTION:
            return await self._execute_function_node(node, node_input, context, permissions, execution_context, dag)
        elif node.type == NodeType.SUBWORKFLOW:
            return await self._execute_subworkflow_node(
                node, node_input, context, permissions, execution_context, dag, deadline=deadline
            )
        elif node.type == NodeType.IF_ELSE:
            return await self._execute_if_else_node(
                node, node_input, context, permissions, execution_context, dag, deadline=deadline
            )
        elif node.type == NodeType.SWITCH:
            return await self._execute_switch_node(
                node, node_input, context, permissions, execution_context, dag, deadline=deadline
            )
        else:
            raise DagError(f"Unknown node type: {node.type}")

    async def _execute_agent_node(
        self,
        node: DagNode,
        node_input: str,
        context: Any,
        permissions: list[str] | None,
    ) -> tuple[Any, str, Exception | None]:
        """Execute an agent node via AppRunner."""
        if self.app_runner is None:
            exc = DagError(
                f"No AppRunner available to execute agent node '{node.id}'"
            )
            return None, "failed", exc

        try:
            result = await self.app_runner.run(
                agent=node.ref,
                workflow=None,
                input=node_input,
                user_id=getattr(context, "user_id", "anonymous"),
                tenant_id=getattr(context, "tenant_id", "default"),
                session_id=getattr(context, "session_id", None),
                permissions=permissions or getattr(context, "permissions", []),
            )
        except Exception as exc:
            return None, "failed", exc

        if result.status == "interrupted":
            return result.final_output, "interrupted", None
        elif result.status == "failed":
            return result.final_output, "failed", None
        else:
            return result.final_output, "completed", None

    async def _execute_tool_node(
        self,
        node: DagNode,
        node_input: str,
        context: Any,
        permissions: list[str] | None,
    ) -> tuple[Any, str, Exception | None]:
        """Execute a tool node via ToolExecutor."""
        from agent_app.runtime.tool_executor import ToolExecutor

        try:
            tool_spec = self.tool_registry.get(node.ref)
        except KeyError:
            return None, "failed", DagError(f"Tool '{node.ref}' not found in registry")

        # Build a minimal ToolExecutor if we don't have one cached
        executor = getattr(self, "_tool_executor", None)
        if executor is None:
            from agent_app.governance.audit import InMemoryAuditLogger
            from agent_app.governance.permission import DefaultPermissionChecker

            executor = ToolExecutor(
                tool_registry=self.tool_registry,
                approval_store=_NoOpApprovalStore(),
                permission_checker=DefaultPermissionChecker(),
                audit_logger=InMemoryAuditLogger(),
                trace_collector=self.trace_collector,
            )
            self._tool_executor = executor

        # Merge DAG-level permissions into context so permission checks pass
        effective_perms = list(permissions or getattr(context, "permissions", []) or [])
        tool_ctx = context.model_copy(update={"permissions": effective_perms})

        try:
            result = await executor.execute(
                tool_name=node.ref,
                arguments={"input": node_input},
                context=tool_ctx,
            )
        except Exception as exc:
            return None, "failed", exc

        if result.status == "interrupted":
            return result.output, "interrupted", None
        elif result.status == "failed":
            return result.output, "failed", None
        else:
            return result.output, "completed", None

    async def _execute_function_node(
        self,
        node: DagNode,
        node_input: str,
        context: Any,
        permissions: list[str] | None,
        execution_context: dict[str, Any] | None = None,
        dag: DagWorkflow | None = None,
    ) -> tuple[Any, str, Exception | None]:
        """Execute a function node by resolving from the function registry.

        Supports sync and async functions. Inputs are resolved from the
        node's ``input`` mapping (see ``_resolve_function_inputs``).
        Output is normalized via ``_normalize_output``.

        Permission enforcement: before execution, checks that the current
        execution context grants all permissions required by the function
        (from registry metadata) and the node configuration.

        Returns:
            Tuple of (output, status, exception) where status is one of:
            "completed", "failed", "interrupted".
        """
        from agent_app.workflows.function_registry import (
            FunctionNotFoundError,
            _call_function,
            _normalize_output,
        )

        # Resolve function from registry
        registry = self.function_registry
        if registry is None:
            from agent_app.workflows.function_registry import get_default_function_registry
            registry = get_default_function_registry()

        try:
            wf_func = registry.get(node.ref)
        except FunctionNotFoundError:
            exc = DagError(
                f"Function '{node.ref}' not found in function registry. "
                f"Register it with @workflow_function(name='{node.ref}')."
            )
            return None, "failed", exc

        # Phase 13.5: Permission enforcement
        # Merge function-level permissions (from registry) with node-level permissions (from YAML)
        required_perms: list[str] = list(wf_func.permissions)
        node_perms = getattr(node, "permissions", None)
        if node_perms:
            for p in node_perms:
                if p not in required_perms:
                    required_perms.append(p)

        if required_perms:
            # Collect available permissions from multiple sources
            available_perms: set[str] = set()
            # From execution_context (may be set by upstream)
            if execution_context:
                ctx_perms = execution_context.get("permissions", [])
                if isinstance(ctx_perms, list):
                    available_perms.update(ctx_perms)
            # From permissions parameter
            if permissions:
                available_perms.update(permissions)
            # From context object (RunContext)
            if hasattr(context, "permissions"):
                ctx_obj_perms = getattr(context, "permissions", [])
                if isinstance(ctx_obj_perms, list):
                    available_perms.update(ctx_obj_perms)

            missing = [p for p in required_perms if p not in available_perms]
            if missing:
                # Record permission denied event
                if self.trace_collector is not None and dag is not None:
                    await self._record_permission_denied_event(
                        context, dag, node, wf_func, missing,
                    )
                exc = DagError({
                    "type": "permission_denied",
                    "function": node.ref,
                    "missing_permissions": missing,
                })
                return None, "failed", exc

        # Build execution context for input resolution
        # Use the parent's execution_context (which has upstream node outputs)
        # or create a minimal one if not provided
        fn_context = dict(execution_context) if execution_context else {"input": node_input}
        if "input" not in fn_context:
            fn_context["input"] = node_input
        if "context" not in fn_context and hasattr(context, "user_id"):
            fn_context["context"] = context

        # Resolve input mapping
        try:
            kwargs = self._resolve_function_inputs(node, fn_context)
        except DagError as exc:
            return None, "failed", exc

        # Execute function
        try:
            result = await _call_function(wf_func.func, kwargs)
        except Exception as exc:
            return None, "failed", exc

        output = _normalize_output(result)
        return output, "completed", None

    async def _execute_subworkflow_node(
        self,
        node: DagNode,
        node_input: str,
        context: Any,
        permissions: list[str] | None,
        execution_context: dict[str, Any],
        dag: DagWorkflow,
        deadline: _DeadlineState | None = None,
    ) -> tuple[Any, str, Exception | None]:
        """Execute a subworkflow node by running another DAG workflow.

        Resolves the subworkflow from the workflow registry, maps inputs
        from the parent execution context, and executes the sub-DAG.
        The subworkflow inherits the parent's permissions and context.

        Cycle detection prevents A→A and A→B→A references.

        Args:
            node: The SUBWORKFLOW node to execute.
            node_input: Original user input string.
            context: Parent RunContext.
            permissions: Granted permissions.
            execution_context: Parent execution state.
            dag: Parent DAG workflow.

        Returns:
            Tuple of (output, status, exception).
        """
        subworkflow_name = node.subworkflow_name or node.ref

        # -- Resolve subworkflow from registry --
        if self.workflow_registry is None:
            exc = DagError({
                "type": "subworkflow_failed",
                "workflow": subworkflow_name,
                "message": "No workflow registry available",
            })
            return None, "failed", exc

        try:
            subworkflow_wf = self.workflow_registry.get(subworkflow_name)
        except KeyError:
            exc = DagError(
                f"Subworkflow node '{node.id}' references unknown workflow "
                f"'{subworkflow_name}'"
            )
            return None, "failed", exc

        from agent_app.core.workflow import WorkflowType
        if subworkflow_wf.type != WorkflowType.DAG:
            exc = DagError(
                f"Subworkflow '{subworkflow_name}' referenced by node '{node.id}' "
                f"is not a DAG workflow (type: {subworkflow_wf.type.value})"
            )
            return None, "failed", exc

        # -- Cycle detection --
        chain = execution_context.get("_subworkflow_chain", [])
        if subworkflow_name in chain:
            exc = DagError(
                f"Recursive subworkflow reference detected: workflow "
                f"'{subworkflow_name}' cannot call itself (chain: "
                f"{' → '.join(chain + [subworkflow_name])})"
            )
            return None, "failed", exc
        new_chain = chain + [subworkflow_name]

        # -- Record SUBWORKFLOW_STARTED event --
        if self.trace_collector is not None:
            await self._record_subworkflow_event(
                context, dag, node, subworkflow_name,
                RunEventType.SUBWORKFLOW_STARTED, "started",
            )

        # -- Resolve inputs --
        # Build subworkflow execution context with inherited permissions
        sub_ctx = dict(execution_context)
        sub_ctx["_subworkflow_chain"] = new_chain
        sub_ctx["permissions"] = list(permissions or execution_context.get("permissions", []))

        try:
            mapped_inputs = self._resolve_function_inputs(node, sub_ctx)
        except DagError as exc:
            if self.trace_collector is not None:
                await self._record_subworkflow_event(
                    context, dag, node, subworkflow_name,
                    RunEventType.SUBWORKFLOW_FAILED, "failed",
                    error={"type": "input_mapping_failed", "message": str(exc)},
                )
            return None, "failed", exc

        # Convert mapped inputs dict to a string for the sub-DAG's input
        # The sub-DAG's FUNCTION nodes can access fields via input.* using _resolve_path
        sub_input = mapped_inputs.get("_raw_input", mapped_inputs)

        # -- Execute the sub-DAG --
        from agent_app.workflows.dag import DagWorkflow
        sub_dag_data = subworkflow_wf.config.get("dag", {})
        sub_dag = DagWorkflow(**sub_dag_data)

        sub_executor = DagExecutor(
            agent_registry=self.agent_registry,
            tool_registry=self.tool_registry,
            workflow_registry=self.workflow_registry,
            app_runner=self.app_runner,
            trace_collector=self.trace_collector,
            function_registry=self.function_registry,
        )

        # Phase 13.8: Compute effective child deadline
        # min(parent remaining deadline, child configured deadline)
        child_deadline: _DeadlineState | None = None
        if deadline is not None:
            parent_remaining = deadline.remaining()
            child_configured = sub_dag.deadline_seconds
            if parent_remaining is not None and child_configured is not None:
                effective = min(parent_remaining, child_configured)
                child_deadline = _DeadlineState(effective)
            elif parent_remaining is not None:
                child_deadline = _DeadlineState(parent_remaining)
            elif child_configured is not None:
                child_deadline = _DeadlineState(child_configured)
        elif sub_dag.deadline_seconds is not None:
            child_deadline = _DeadlineState(sub_dag.deadline_seconds)

        try:
            sub_results, sub_status, sub_output, _ = await sub_executor.execute(
                dag=sub_dag,
                input=sub_input if isinstance(sub_input, str) else str(sub_input),
                context=context,
                permissions=sub_ctx.get("permissions"),
                _subworkflow_chain=new_chain,
                _deadline=child_deadline,
            )
        except Exception as exc:
            if self.trace_collector is not None:
                await self._record_subworkflow_event(
                    context, dag, node, subworkflow_name,
                    RunEventType.SUBWORKFLOW_FAILED, "failed",
                    error={"type": "subworkflow_error", "message": str(exc)},
                )
            sw_exc = DagError({
                "type": "subworkflow_failed",
                "workflow": subworkflow_name,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            })
            return None, "failed", sw_exc

        # -- Handle subworkflow status --
        if sub_status in ("failed", "interrupted"):
            # Find the first failed/INTERRUPTED node for error context
            failed_node_info = None
            for r in sub_results:
                if r.status in (NodeExecutionStatus.FAILED, NodeExecutionStatus.INTERRUPTED):
                    failed_node_info = {
                        "node_id": r.node_id,
                        "error": r.error,
                    }
                    break

            if self.trace_collector is not None:
                await self._record_subworkflow_event(
                    context, dag, node, subworkflow_name,
                    RunEventType.SUBWORKFLOW_FAILED, "failed",
                    error={
                        "type": "subworkflow_failed",
                        "workflow": subworkflow_name,
                        "failed_node": failed_node_info,
                    },
                )

            sw_exc = DagError({
                "type": "subworkflow_failed",
                "workflow": subworkflow_name,
                "failed_node": failed_node_info,
                "subworkflow_status": sub_status,
            })
            return None, "failed", sw_exc

        # -- Record SUBWORKFLOW_COMPLETED --
        if self.trace_collector is not None:
            await self._record_subworkflow_event(
                context, dag, node, subworkflow_name,
                RunEventType.SUBWORKFLOW_COMPLETED, "completed",
            )

        # Return the subworkflow's final output wrapped with metadata
        return {
            "workflow": subworkflow_name,
            "status": "completed",
            "output": sub_output,
            "node_outputs": {r.node_id: r.output for r in sub_results},
        }, "completed", None

    async def _record_subworkflow_event(
        self,
        context: Any,
        parent_dag: DagWorkflow,
        node: DagNode,
        subworkflow_name: str,
        event_type: Any,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Record a subworkflow lifecycle event."""
        if self.trace_collector is None:
            return
        from agent_app.observability.events import RunEvent

        await self.trace_collector.record(
            RunEvent(
                event_type=event_type,
                trace_id=getattr(context, "trace_id", "") or "",
                run_id=getattr(context, "run_id", ""),
                user_id=getattr(context, "user_id", ""),
                tenant_id=getattr(context, "tenant_id", ""),
                workflow_name=parent_dag.name,
                workflow_type="dag",
                status=status,
                error=error,
                data={
                    "node_id": node.id,
                    "node_type": "subworkflow",
                    "subworkflow": subworkflow_name,
                },
            )
        )

    async def _record_permission_denied_event(
        self,
        context: Any,
        dag: DagWorkflow,
        node: DagNode,
        wf_func: Any,
        missing: list[str],
    ) -> None:
        """Record a FUNCTION_PERMISSION_DENIED event to the trace collector."""
        from agent_app.observability.events import RunEvent, RunEventType

        await self.trace_collector.record(
            RunEvent(
                event_type=RunEventType.FUNCTION_PERMISSION_DENIED,
                trace_id=getattr(context, "trace_id", "") or "",
                run_id=getattr(context, "run_id", ""),
                user_id=getattr(context, "user_id", ""),
                tenant_id=getattr(context, "tenant_id", ""),
                workflow_name=dag.name,
                workflow_type="dag",
                status="failed",
                error={
                    "type": "permission_denied",
                    "function": node.ref,
                    "missing_permissions": missing,
                    "required_permissions": wf_func.permissions,
                },
                data={
                    "node_id": node.id,
                    "node_type": "function",
                    "function": node.ref,
                    "missing_permissions": missing,
                },
            )
        )

    async def _execute_if_else_node(
        self,
        node: DagNode,
        node_input: str,
        context: Any,
        permissions: list[str] | None,
        execution_context: dict[str, Any],
        dag: DagWorkflow,
        deadline: _DeadlineState | None = None,
    ) -> tuple[Any, str, Exception | None]:
        """Execute an if/else branch node.

        Evaluates the node's condition expression. If true, executes all
        nodes in ``then``; if false, executes all nodes in ``else_``.

        Returns:
            Tuple of (IfElseResult, status, exception).
        """
        from agent_app.workflows.condition import (
            ConditionEvaluationError,
            evaluate_condition,
        )

        condition_expr = node.input.get("condition", "")
        if not condition_expr:
            exc = DagError(
                f"if_else node '{node.id}' requires a 'condition' in input"
            )
            return None, "failed", exc

        # Build a DagCondition for evaluation
        condition = DagCondition(expr=condition_expr)

        # Evaluate condition against current node_results
        # Use _result: keys which store full NodeExecutionResult objects
        node_results = {}
        for key, val in execution_context.items():
            if key.startswith("_result:") and isinstance(val, NodeExecutionResult):
                node_results[key[8:]] = val

        try:
            cond_value = evaluate_condition(condition, node_results)
        except ConditionEvaluationError as exc:
            return None, "failed", DagError(
                f"Condition evaluation failed for if_else node '{node.id}': {exc}"
            )

        then_ids = node.then or []
        else_ids = node.else_branch or []

        then_output: Any = None
        else_output: Any = None
        then_status = "skipped"
        else_status = "skipped"
        then_executed: list[str] = []
        else_executed: list[str] = []
        overall_status = "completed"

        if cond_value:
            # Execute then branch
            then_status = "completed"
            for dep_id in then_ids:
                dep_node = next((n for n in dag.nodes if n.id == dep_id), None)
                if dep_node is None:
                    then_status = "failed"
                    overall_status = "failed"
                    break

                dep_input = self._build_node_input(dep_node, execution_context)
                skip_result = self._should_skip_node(dep_node, node_results)
                if skip_result is not None:
                    node_results[dep_id] = skip_result
                    execution_context[f"node:{dep_id}"] = None
                    then_status = self._propagate_status(
                        skip_result.status.value, then_status, None
                    )[0]
                    if then_status in ("failed", "interrupted"):
                        overall_status = then_status
                        break
                    continue

                dep_result = await self._execute_node_with_retry(
                    dep_node, dag, dep_input, context, permissions, execution_context, deadline=deadline
                )
                node_results[dep_id] = dep_result
                execution_context[f"node:{dep_id}"] = dep_result.output
                execution_context[f"branch:then:{dep_id}"] = dep_result.output
                then_output = dep_result.output
                then_executed.append(dep_id)
                then_status = self._propagate_status(
                    dep_result.status.value, then_status, dep_result.output
                )[0]
                if then_status in ("failed", "interrupted"):
                    overall_status = then_status
                    break
        else:
            # Execute else branch
            else_status = "completed"
            for dep_id in else_ids:
                dep_node = next((n for n in dag.nodes if n.id == dep_id), None)
                if dep_node is None:
                    else_status = "failed"
                    overall_status = "failed"
                    break

                dep_input = self._build_node_input(dep_node, execution_context)
                skip_result = self._should_skip_node(dep_node, node_results)
                if skip_result is not None:
                    node_results[dep_id] = skip_result
                    execution_context[f"node:{dep_id}"] = None
                    else_status = self._propagate_status(
                        skip_result.status.value, else_status, None
                    )[0]
                    if else_status in ("failed", "interrupted"):
                        overall_status = else_status
                        break
                    continue

                dep_result = await self._execute_node_with_retry(
                    dep_node, dag, dep_input, context, permissions, execution_context, deadline=deadline
                )
                node_results[dep_id] = dep_result
                execution_context[f"node:{dep_id}"] = dep_result.output
                execution_context[f"branch:else:{dep_id}"] = dep_result.output
                else_output = dep_result.output
                else_executed.append(dep_id)
                else_status = self._propagate_status(
                    dep_result.status.value, else_status, dep_result.output
                )[0]
                if else_status in ("failed", "interrupted"):
                    overall_status = else_status
                    break

        result = IfElseResult(
            condition=condition_expr,
            condition_result=cond_value,
            then_output=then_output,
            else_output=else_output,
            then_status=then_status,
            else_status=else_status,
            then_node_ids=then_executed,
            else_node_ids=else_executed,
        )
        return result, overall_status, None

    async def _execute_switch_node(
        self,
        node: DagNode,
        node_input: str,
        context: Any,
        permissions: list[str] | None,
        execution_context: dict[str, Any],
        dag: DagWorkflow,
        deadline: _DeadlineState | None = None,
    ) -> tuple[Any, str, Exception | None]:
        """Execute a switch (multi-way branch) node.

        Evaluates ``switch_expr`` against each case value. Executes the
        first matching case's node_ids, or the ``default`` case if none
        match.

        Returns:
            Tuple of (SwitchResult, status, exception).
        """
        from agent_app.workflows.condition import (
            ConditionEvaluationError,
            resolve_expression_value,
        )

        switch_expr = node.switch_expr or ""
        if not switch_expr:
            exc = DagError(
                f"switch node '{node.id}' requires a 'switch_expr'"
            )
            return None, "failed", exc

        # Evaluate expression against current node_results to get the raw value
        # Use _result: keys which store full NodeExecutionResult objects
        node_results = {}
        for key, val in execution_context.items():
            if key.startswith("_result:") and isinstance(val, NodeExecutionResult):
                node_results[key[8:]] = val

        try:
            expr_value = resolve_expression_value(switch_expr, node_results)
        except ConditionEvaluationError as exc:
            return None, "failed", DagError(
                f"Switch expression evaluation failed for node '{node.id}': {exc}"
            )

        cases = node.cases or []
        default_ids: list[str] = node.input.get("default", [])

        # Find matching case
        matched_value: Any = None
        matched_ids: list[str] = []
        matched_index = -1

        for i, case in enumerate(cases):
            case_value = case.get("value")
            case_ids = case.get("node_ids", [])
            if case_value is not None and expr_value == case_value:
                matched_value = case_value
                matched_ids = case_ids
                matched_index = i
                break

        if not matched_ids and default_ids:
            matched_ids = default_ids
            matched_index = -1

        # Execute matched branch
        output: Any = None
        status = "skipped"
        executed_ids: list[str] = []
        overall_status = "completed"

        for dep_id in matched_ids:
            dep_node = next((n for n in dag.nodes if n.id == dep_id), None)
            if dep_node is None:
                status = "failed"
                overall_status = "failed"
                break

            dep_input = self._build_node_input(dep_node, execution_context)
            skip_result = self._should_skip_node(dep_node, node_results)
            if skip_result is not None:
                node_results[dep_id] = skip_result
                execution_context[f"node:{dep_id}"] = None
                status = self._propagate_status(
                    skip_result.status.value, status, None
                )[0]
                if status in ("failed", "interrupted"):
                    overall_status = status
                    break
                continue

            dep_result = await self._execute_node_with_retry(
                dep_node, dag, dep_input, context, permissions, execution_context, deadline=deadline
            )
            node_results[dep_id] = dep_result
            execution_context[f"node:{dep_id}"] = dep_result.output
            output = dep_result.output
            executed_ids.append(dep_id)
            status = self._propagate_status(
                dep_result.status.value, status, dep_result.output
            )[0]
            if status in ("failed", "interrupted"):
                overall_status = status
                break

        result = SwitchResult(
            expression=switch_expr,
            matched_value=matched_value,
            matched_case_index=matched_index,
            output=output,
            status=status,
            executed_node_ids=executed_ids,
        )
        return result, overall_status, None

    def _resolve_function_inputs(
        self,
        node: DagNode,
        execution_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve a FUNCTION node's input mapping to concrete kwargs.

        Supports the following reference patterns:
        - ``input.<field>`` — value from the original workflow input
        - ``nodes.<node_id>.output.<field>`` — output from an upstream node
        - ``nodes.<node_id>.status`` — status string of an upstream node
        - ``context.user_id`` — from the RunContext
        - ``context.tenant_id`` — from the RunContext
        - Literal values (strings, numbers, booleans) — passed through

        Nested paths are supported via dot-separated segments, e.g.:
        - ``nodes.a.output.data.amount``
        - ``input.customer.profile.email``
        - ``context.user.role``

        Args:
            node: The FUNCTION node whose inputs to resolve.
            execution_context: Current execution state including ``"input"``
                (original user input string) and ``"node:<id>"`` entries
                for completed upstream nodes.

        Returns:
            Dictionary of parameter name → resolved value.

        Raises:
            DagError: If a referenced node/field doesn't exist.
        """
        resolved: dict[str, Any] = {}
        input_mapping: dict[str, Any] = node.input or {}

        def _get_upstream_output(node_key: str) -> Any:
            """Extract the raw output from an execution_context entry.

            The context stores either a raw value or a NodeExecutionResult.
            """
            val = execution_context.get(node_key)
            if val is None:
                return None
            if isinstance(val, NodeExecutionResult):
                return val.output
            return val

        def _get_upstream_status(node_key: str) -> str | None:
            """Extract the status string from an execution_context entry."""
            val = execution_context.get(node_key)
            if val is None:
                return None
            if isinstance(val, NodeExecutionResult):
                return val.status.value
            return str(val)

        def _resolve_path(source: Any, path: str) -> Any:
            """Resolve a dot-separated path through a nested structure.

            Supports dict key access, Pydantic model attribute access,
            and list index access (e.g. ``items.0.name``).

            Args:
                source: The root object to resolve from.
                path: Dot-separated path string.

            Returns:
                The resolved value.

            Raises:
                DagError: If a path segment cannot be resolved.
            """
            current = source
            segments = path.split(".")
            for i, segment in enumerate(segments):
                segment_desc = f"path segment '{segment}'" if i > 0 else f"root value"
                if current is None:
                    raise DagError(
                        f"Failed to resolve input mapping '{full_mapping}': "
                        f"{segment_desc} is None"
                    )
                # Try list index access first
                if isinstance(current, list):
                    try:
                        idx = int(segment)
                        current = current[idx]
                    except (ValueError, IndexError):
                        raise DagError(
                            f"Failed to resolve input mapping '{full_mapping}': "
                            f"index '{segment}' out of range for list"
                        )
                    continue
                # Try dict key access
                if isinstance(current, dict):
                    if segment not in current:
                        raise DagError(
                            f"Failed to resolve input mapping '{full_mapping}': "
                            f"path segment '{segment}' not found"
                        )
                    current = current[segment]
                    continue
                # Try Pydantic model attribute access
                if hasattr(current, "model_dump"):
                    # Pydantic model — use model_dump() then continue
                    try:
                        current = current.model_dump()[segment]
                    except (KeyError, AttributeError):
                        raise DagError(
                            f"Failed to resolve input mapping '{full_mapping}': "
                            f"path segment '{segment}' not found in model"
                        )
                    continue
                # Try generic attribute access
                if hasattr(current, segment):
                    current = getattr(current, segment)
                    continue
                raise DagError(
                    f"Failed to resolve input mapping '{full_mapping}': "
                    f"cannot access '{segment}' on {type(current).__name__}"
                )
            return current

        for param_name, raw_value in input_mapping.items():
            if isinstance(raw_value, str):
                if raw_value.startswith("input."):
                    field = raw_value[len("input."):]
                    source = execution_context.get("input", "")
                    full_mapping = raw_value
                    if isinstance(source, str):
                        # Plain string input — return as-is (no nested path)
                        resolved[param_name] = source
                    elif field:
                        try:
                            resolved[param_name] = _resolve_path(source, field)
                        except DagError:
                            raise
                    else:
                        resolved[param_name] = source
                elif raw_value.startswith("nodes."):
                    parts = raw_value.split(".")
                    if len(parts) >= 4 and parts[2] == "output":
                        upstream_id = parts[1]
                        field = ".".join(parts[3:])
                        node_key = f"node:{upstream_id}"
                        upstream_output = _get_upstream_output(node_key)
                        full_mapping = raw_value
                        if upstream_output is None:
                            raise DagError(
                                f"Failed to resolve input mapping '{full_mapping}': "
                                f"upstream node '{upstream_id}' has not produced "
                                f"output yet"
                            )
                        try:
                            resolved[param_name] = _resolve_path(upstream_output, field)
                        except DagError:
                            raise
                    elif len(parts) >= 3 and parts[2] == "status":
                        upstream_id = parts[1]
                        node_key = f"node:{upstream_id}"
                        status_val = _get_upstream_status(node_key)
                        if status_val is None:
                            raise DagError(
                                f"Failed to resolve input mapping '{raw_value}': "
                                f"upstream node '{upstream_id}' has not executed "
                                f"yet"
                            )
                        resolved[param_name] = status_val
                    else:
                        raise DagError(
                            f"Invalid nodes reference '{raw_value}' in input "
                            f"mapping of node '{node.id}' — expected "
                            f"'nodes.<id>.output.<field>' or 'nodes.<id>.status'"
                        )
                elif raw_value.startswith("context."):
                    ctx_field = raw_value[len("context."):]
                    ctx_obj = execution_context.get("context")
                    full_mapping = raw_value
                    if ctx_obj is None:
                        raise DagError(
                            f"Failed to resolve input mapping '{full_mapping}': "
                            f"no context object available"
                        )
                    try:
                        resolved[param_name] = _resolve_path(ctx_obj, ctx_field)
                    except DagError:
                        raise
                else:
                    # Treat as literal string if it doesn't match any prefix
                    resolved[param_name] = raw_value
            else:
                # Literal value (int, float, bool, list, dict, None)
                resolved[param_name] = raw_value

        # Validate: all required params resolved
        return resolved

    async def _record_deadline_exceeded_event(
        self,
        context: Any,
        dag: DagWorkflow,
        deadline: _DeadlineState,
        completed_ids: list[str],
        running_ids: list[str],
        pending_ids: list[str],
    ) -> None:
        """Record a WORKFLOW_DEADLINE_EXCEEDED event."""
        if self.trace_collector is None:
            return
        from agent_app.observability.events import RunEvent, RunEventType

        elapsed = deadline.deadline_seconds - deadline.remaining() if deadline.remaining() is not None else deadline.deadline_seconds
        await self.trace_collector.record(
            RunEvent(
                event_type=RunEventType.WORKFLOW_DEADLINE_EXCEEDED,
                trace_id=getattr(context, "trace_id", "") or "",
                run_id=getattr(context, "run_id", ""),
                user_id=getattr(context, "user_id", ""),
                tenant_id=getattr(context, "tenant_id", ""),
                workflow_name=dag.name,
                workflow_type="dag",
                status="failed",
                error={
                    "type": "workflow_deadline_exceeded",
                    "deadline_seconds": deadline.deadline_seconds,
                    "elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
                },
                data={
                    "deadline_seconds": deadline.deadline_seconds,
                    "elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
                    "completed_node_ids": completed_ids,
                    "running_node_ids": running_ids,
                    "pending_node_ids": pending_ids,
                },
            )
        )

    def _should_trigger_compensation(self, dag: DagWorkflow, overall_status: str) -> bool:
        """Determine if compensation should be triggered based on workflow status."""
        if dag.compensation is None:
            return False

        enabled = dag.compensation.get("enabled", False)
        if not enabled:
            return False

        trigger_on = dag.compensation.get("trigger_on", ["workflow_failed"])

        if overall_status == "failed" and "workflow_failed" in trigger_on:
            return True
        if overall_status == "interrupted" and "workflow_interrupted" in trigger_on:
            return True

        return False
    def _should_trigger_compensation(self, dag: DagWorkflow, overall_status: str) -> bool:
        """Determine if compensation should be triggered based on workflow status."""
        if dag.compensation is None:
            return False

        enabled = dag.compensation.get("enabled", False)
        if not enabled:
            return False

        trigger_on = dag.compensation.get("trigger_on", ["workflow_failed"])

        if overall_status == "failed" and "workflow_failed" in trigger_on:
            return True
        if overall_status == "interrupted" and "workflow_interrupted" in trigger_on:
            return True

        return False

    def _get_compensation_candidates(
        self, results: dict[str, NodeExecutionResult], dag: DagWorkflow
    ) -> list[str]:
        """Identify nodes eligible for compensation.

        Returns node IDs in reverse completion order for deterministic execution.
        """
        # Nodes that can be compensated: completed, with a compensation handler
        completed_nodes = []
        for node in dag.nodes:
            if node.id not in results:
                continue
            result = results[node.id]
            if result.status != NodeExecutionStatus.COMPLETED:
                continue
            if node.compensate is None:
                continue
            # Don't compensate compensation handlers themselves
            if node.type == NodeType.FUNCTION and "compensate" in node.id.lower():
                continue
            completed_nodes.append(node.id)

        # Return in reverse topological order (reverse completion order)
        # For deterministic ordering, we'll sort by the completion timestamp if available
        def completion_order(node_id: str) -> tuple[int, str]:
            result = results.get(node_id)
            if result and result.completed_at is not None:
                # Use negative timestamp for reverse order
                return (0, str(result.completed_at))
            # Fallback to node index for stable ordering
            idx = next((i for i, n in enumerate(dag.nodes) if n.id == node_id), 0)
            return (1, str(idx))

        completed_nodes.sort(key=completion_order, reverse=True)
        return completed_nodes

    async def _execute_compensation(
        self,
        dag: DagWorkflow,
        results: dict[str, NodeExecutionResult],
        input: str,
        context: Any,
        permissions: list[str] | None,
        execution_context: dict[str, Any],
        original_failure_type: str,
    ) -> WorkflowCompensationResult:
        """Execute compensation handlers for completed nodes in reverse order."""
        if not self._should_trigger_compensation(dag, "failed"):
            return WorkflowCompensationResult(
                status="skipped",
                skipped_nodes=[],
            )

        continue_on_failure = dag.compensation.get("continue_on_failure", True)
        comp_timeout = dag.compensation.get("timeout_seconds", None)

        # Record workflow compensation started event
        if self.trace_collector is not None:
            await self._record_workflow_compensation_started(
                context, dag, original_failure_type
            )

        # Phase 14.0: Persist workflow compensation started state
        if self._state_store is not None and self._run_id:
            await self._state_store.update_run(
                self._run_id,
                status="compensating",
            )
            await self._persist_event(
                context, "workflow.compensation_started",
                payload={"failure_type": original_failure_type}
            )

        candidates = self._get_compensation_candidates(results, dag)
        comp_result = WorkflowCompensationResult(status="completed")
        comp_result.compensated_nodes = []
        comp_result.failed_nodes = []
        comp_result.skipped_nodes = []

        # Phase 16.1: Create and persist compensation state
        comp_state = None
        if self._is_compensation_persistence_enabled() and candidates:
            self._init_compensation_store()
            comp_state = self._create_compensation_state(
                dag, candidates, original_failure_type
            )
            comp_state.mark_running()
            try:
                await self._save_compensation_state(comp_state)
            except Exception:
                # Compensation state write failure is a stable error
                # but we continue execution (best-effort persistence)
                comp_state = None

        for node_id in candidates:
            node = next((n for n in dag.nodes if n.id == node_id), None)
            if node is None or node.compensate is None:
                comp_result.skipped_nodes.append(node_id)
                continue

            # Record node compensation started event
            if self.trace_collector is not None:
                await self._record_node_compensation_started(
                    context, dag, node, original_failure_type
                )

            # Phase 16.1: Update compensation action to RUNNING
            action_id_for_node = None
            if comp_state is not None:
                # Find the action for this node
                for aid, action in comp_state.actions.items():
                    if action.compensating_for_node_id == node_id:
                        action_id_for_node = aid
                        action.mark_running()
                        try:
                            await self._compensation_store.update_compensation_action(
                                self._run_id or "", action
                            )
                        except Exception:
                            pass
                        break

            started_at = time.perf_counter()
            comp_start_time = _now()
            try:
                # Execute compensation handler
                output, status, exc = await self._execute_compensation_handler(
                    node, dag, input, context, permissions, execution_context, comp_timeout
                )
                completed_at = time.perf_counter()
                comp_end_time = _now()

                node_comp_result = NodeCompensationResult(
                    node_id=node_id,
                    status="completed",
                    started_at=started_at,
                    completed_at=completed_at,
                    attempts=1,
                    output=output,
                )
                comp_result.results[node_id] = node_comp_result
                comp_result.compensated_nodes.append(node_id)

                # Record node compensation completed event
                if self.trace_collector is not None:
                    await self._record_node_compensation_completed(
                        context, dag, node, output
                    )

                # Phase 16.1: Update compensation action to COMPLETED
                if action_id_for_node is not None and comp_state is not None:
                    action = comp_state.get_action(action_id_for_node)
                    if action is not None:
                        action.mark_completed(output)
                        try:
                            await self._compensation_store.update_compensation_action(
                                self._run_id or "", action
                            )
                        except Exception:
                            pass

            except Exception as exc:
                completed_at = time.perf_counter()
                comp_fail_time = _now()
                node_comp_result = NodeCompensationResult(
                    node_id=node_id,
                    status="failed",
                    started_at=started_at,
                    completed_at=completed_at,
                    attempts=1,
                    error={
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                comp_result.results[node_id] = node_comp_result
                comp_result.failed_nodes.append(node_id)

                # Record node compensation failed event
                if self.trace_collector is not None:
                    await self._record_node_compensation_failed(
                        context, dag, node, exc
                    )

                # Phase 16.1: Update compensation action to FAILED
                if action_id_for_node is not None and comp_state is not None:
                    action = comp_state.get_action(action_id_for_node)
                    if action is not None:
                        action.mark_failed(exc)
                        try:
                            await self._compensation_store.update_compensation_action(
                                self._run_id or "", action
                            )
                        except Exception:
                            pass

                if not continue_on_failure:
                    comp_result.status = "failed"
                    break

        # Determine overall compensation status
        if comp_result.failed_nodes and comp_result.compensated_nodes:
            comp_result.status = "partial"
        elif comp_result.failed_nodes and not comp_result.compensated_nodes:
            comp_result.status = "failed"
        elif comp_result.skipped_nodes and not comp_result.compensated_nodes:
            comp_result.status = "skipped"

        # Phase 16.1: Finalize compensation state status
        if comp_state is not None:
            try:
                if comp_result.status == "completed":
                    comp_state.mark_completed()
                else:
                    comp_state.mark_partial_failed()
                await self._save_compensation_state(comp_state)
            except Exception:
                pass  # Best-effort: don't let compensation state failure block execution

        return comp_result

    async def _resume_compensation(
        self,
        dag: DagWorkflow,
        input: str,
        context: Any,
        permissions: list[str] | None,
        execution_context: dict[str, Any],
        existing_state: Any,
        original_failure_type: str,
    ) -> Any:
        """Resume an incomplete compensation from persisted state.

        Skips completed actions, retries failed actions within max_attempts,
        and executes pending actions.

        Args:
            dag: The workflow definition.
            input: Original input.
            context: Run context.
            permissions: Granted permissions.
            execution_context: Current execution context.
            existing_state: The persisted CompensationExecutionState.
            original_failure_type: Type of the original failure.

        Returns:
            WorkflowCompensationResult from resumed execution.
        """
        from agent_app.runtime.compensation_state import (
            CompensationActionStatus,
            CompensationRunStatus,
        )
        from agent_app.workflows.dag import (
            NodeCompensationResult,
            WorkflowCompensationResult,
        )

        comp_result = WorkflowCompensationResult(status="completed")
        comp_result.compensated_nodes = []
        comp_result.failed_nodes = []
        comp_result.skipped_nodes = []

        continue_on_failure = dag.compensation.get("continue_on_failure", True) if dag.compensation else True
        comp_timeout = dag.compensation.get("timeout_seconds", None) if dag.compensation else None

        # Mark state as running
        existing_state.mark_running()
        await self._save_compensation_state(existing_state)

        for action_id in existing_state.action_order:
            action = existing_state.get_action(action_id)
            if action is None:
                continue

            # Skip completed actions
            if action.status == CompensationActionStatus.COMPLETED.value:
                comp_result.compensated_nodes.append(action.compensating_for_node_id or action.node_id)
                # Rebuild NodeCompensationResult from persisted data
                comp_result.results[action.node_id] = NodeCompensationResult(
                    node_id=action.node_id,
                    status="completed",
                    started_at=action.started_at or datetime.now(timezone.utc),
                    completed_at=action.completed_at or datetime.now(timezone.utc),
                    attempts=action.attempts,
                    output=action.output,
                )
                continue

            # Skip failed actions beyond max_attempts
            if action.status == CompensationActionStatus.FAILED.value:
                if not action.can_retry():
                    comp_result.failed_nodes.append(action.compensating_for_node_id or action.node_id)
                    comp_result.results[action.node_id] = NodeCompensationResult(
                        node_id=action.node_id,
                        status="failed",
                        attempts=action.attempts,
                        error=action.error,
                    )
                    continue

            # Execute pending or retryable failed actions
            node_id = action.compensating_for_node_id or action.node_id
            node = next((n for n in dag.nodes if n.id == node_id), None)
            if node is None or node.compensate is None:
                comp_result.skipped_nodes.append(node_id)
                action.mark_skipped(f"Node {node_id} not found or no compensate config")
                try:
                    await self._compensation_store.update_compensation_action(
                        self._run_id or "", action
                    )
                except Exception:
                    pass
                continue

            # Mark action running
            action.mark_running()
            try:
                await self._compensation_store.update_compensation_action(
                    self._run_id or "", action
                )
            except Exception:
                pass

            started_at = time.perf_counter()
            comp_start_time = _now()
            try:
                output, status, exc = await self._execute_compensation_handler(
                    node, dag, input, context, permissions, execution_context, comp_timeout
                )
                completed_at = time.perf_counter()
                comp_end_time = _now()

                action.mark_completed(output)
                try:
                    await self._compensation_store.update_compensation_action(
                        self._run_id or "", action
                    )
                except Exception:
                    pass

                comp_result.results[node_id] = NodeCompensationResult(
                    node_id=node_id,
                    status="completed",
                    started_at=started_at,
                    completed_at=completed_at,
                    attempts=action.attempts,
                    output=output,
                )
                comp_result.compensated_nodes.append(node_id)

            except Exception as exc:
                completed_at = time.perf_counter()
                action.mark_failed(exc)
                try:
                    await self._compensation_store.update_compensation_action(
                        self._run_id or "", action
                    )
                except Exception:
                    pass

                comp_result.results[node_id] = NodeCompensationResult(
                    node_id=node_id,
                    status="failed",
                    started_at=started_at,
                    completed_at=completed_at,
                    attempts=action.attempts,
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
                comp_result.failed_nodes.append(node_id)

                if not continue_on_failure:
                    existing_state.mark_failed()
                    await self._save_compensation_state(existing_state)
                    comp_result.status = "failed"
                    return comp_result

        # Finalize state
        if comp_result.failed_nodes and comp_result.compensated_nodes:
            comp_result.status = "partial"
            existing_state.mark_partial_failed()
        elif comp_result.failed_nodes and not comp_result.compensated_nodes:
            comp_result.status = "failed"
            existing_state.mark_failed()
        else:
            existing_state.mark_completed()
        await self._save_compensation_state(existing_state)

        return comp_result

    async def _execute_compensation_handler(
        self,
        node: DagNode,
        dag: DagWorkflow,
        input: str,
        context: Any,
        permissions: list[str] | None,
        execution_context: dict[str, Any],
        comp_timeout: float | None,
    ) -> tuple[Any, str, Exception | None]:
        """Execute a single compensation handler for a node."""
        # Parse compensation config
        comp_config = node.compensate
        if comp_config is None:
            raise ValueError(f"No compensation config for node {node.id}")

        # Get the compensation function/ref
        comp_func_name = comp_config.get("function") or comp_config.get("ref")
        if not comp_func_name:
            raise ValueError(f"No function/ref in compensation config for node {node.id}")

        # Get inputs
        comp_inputs = comp_config.get("inputs", {})
        if isinstance(comp_inputs, dict):
            resolved_inputs = self._resolve_compensation_inputs(
                comp_inputs, node.id, execution_context
            )
        else:
            resolved_inputs = {}

        # Execute the compensation
        # Execute the compensation
        try:
            wf_func_entry = self.function_registry.get(comp_func_name)
            comp_func = wf_func_entry.func
        except Exception:
            raise ValueError(f"Compensation function '{comp_func_name}' not found in registry")


        try:
            timeout = comp_config.get("timeout_seconds", comp_timeout)
            if timeout is not None:
                output = await asyncio.wait_for(
                    comp_func(**resolved_inputs), timeout=timeout
                )
            else:
                output = await comp_func(**resolved_inputs)
            return output, "completed", None
        except asyncio.TimeoutError:
            raise CompensationError(
                f"Compensation for node \'{node.id}\' timed out after {timeout}s"
            )
        except Exception as e:
            raise CompensationError(
                f"Compensation for node \'{node.id}\' failed: {e}"
            ) from e

    def _resolve_compensation_inputs(
        self,
        comp_inputs: dict[str, Any],
        node_id: str,
        execution_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve compensation input mappings."""
        resolved = {}
        for key, value in comp_inputs.items():
            if isinstance(value, str) and value.startswith("nodes."):
                # Reference to original node's output
                parts = value[6:].split(".", 1)  # Remove "nodes." prefix
                source_node_id = parts[0]
                source_result = execution_context.get(f"_result:{source_node_id}")
                if source_result and hasattr(source_result, "output"):
                    if len(parts) > 1:
                        resolved[key] = self._resolve_path(
                            source_result.output, parts[1]
                        )
                    else:
                        resolved[key] = source_result.output
                else:
                    resolved[key] = None
            elif isinstance(value, str) and value.startswith("input."):
                resolved[key] = execution_context.get("input", {}).get(value[6:])
            else:
                resolved[key] = value
        return resolved

    def _resolve_path(self, obj: Any, path: str) -> Any:
        """Resolve a dotted path into a nested object."""
        parts = path.split(".")
        current = obj
        for part in parts:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            else:
                try:
                    current = getattr(current, part)
                except AttributeError:
                    return None
        return current

    async def _record_workflow_compensation_started(
        self, context: Any, dag: DagWorkflow, original_failure_type: str
    ) -> None:
        """Record workflow compensation started event."""
        await self.trace_collector.record(
            RunEvent(
                event_type=RunEventType.WORKFLOW_COMPENSATION_STARTED,
                trace_id=context.trace_id or "",
                run_id=context.run_id,
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                workflow_name=dag.name,
                workflow_type="dag",
                data={
                    "original_failure_type": original_failure_type,
                },
            )
        )

    async def _record_workflow_compensation_completed(
        self,
        context: Any,
        dag: DagWorkflow,
        comp_result: WorkflowCompensationResult,
        original_failure_type: str,
    ) -> None:
        """Record workflow compensation completed event."""
        await self.trace_collector.record(
            RunEvent(
                event_type=RunEventType.WORKFLOW_COMPENSATION_COMPLETED,
                trace_id=context.trace_id or "",
                run_id=context.run_id,
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                workflow_name=dag.name,
                workflow_type="dag",
                data={
                    "original_failure_type": original_failure_type,
                    "compensation_status": comp_result.status,
                    "compensated_node_ids": comp_result.compensated_nodes,
                    "failed_compensation_node_ids": comp_result.failed_nodes,
                    "skipped_compensation_node_ids": comp_result.skipped_nodes,
                },
            )
        )

    async def _record_node_compensation_started(
        self, context: Any, dag: DagWorkflow, node: DagNode, original_failure_type: str
    ) -> None:
        """Record node compensation started event."""
        await self.trace_collector.record(
            RunEvent(
                event_type=RunEventType.NODE_COMPENSATION_STARTED,
                trace_id=context.trace_id or "",
                run_id=context.run_id,
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                workflow_name=dag.name,
                workflow_type="dag",
                tool_name=node.ref if node.type == NodeType.TOOL else None,
                agent_name=node.ref if node.type == NodeType.AGENT else None,
                data={
                    "node_id": node.id,
                    "original_failure_type": original_failure_type,
                },
            )
        )

    async def _record_node_compensation_completed(
        self, context: Any, dag: DagWorkflow, node: DagNode, output: Any
    ) -> None:
        """Record node compensation completed event."""
        await self.trace_collector.record(
            RunEvent(
                event_type=RunEventType.NODE_COMPENSATION_COMPLETED,
                trace_id=context.trace_id or "",
                run_id=context.run_id,
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                workflow_name=dag.name,
                workflow_type="dag",
                tool_name=node.ref if node.type == NodeType.TOOL else None,
                agent_name=node.ref if node.type == NodeType.AGENT else None,
                data={
                    "node_id": node.id,
                },
            )
        )

    async def _record_node_compensation_failed(
        self, context: Any, dag: DagWorkflow, node: DagNode, error: Exception
    ) -> None:
        """Record node compensation failed event."""
        await self.trace_collector.record(
            RunEvent(
                event_type=RunEventType.NODE_COMPENSATION_FAILED,
                trace_id=context.trace_id or "",
                run_id=context.run_id,
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                workflow_name=dag.name,
                workflow_type="dag",
                tool_name=node.ref if node.type == NodeType.TOOL else None,
                agent_name=node.ref if node.type == NodeType.AGENT else None,
                data={
                    "node_id": node.id,
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                },
            )
        )


    def _dag_wf_event_type(self, overall_status: str) -> Any:
        """Map overall DAG status to a RunEventType."""
        from agent_app.observability.events import RunEventType
        mapping = {
            "completed": RunEventType.WORKFLOW_COMPLETED,
            "failed": RunEventType.WORKFLOW_FAILED,
            "interrupted": RunEventType.WORKFLOW_FAILED,
        }
        return mapping.get(overall_status, RunEventType.WORKFLOW_FAILED)


# ---------------------------------------------------------------------------
# No-op fallback for tool approval store
# ---------------------------------------------------------------------------


class _NoOpApprovalStore:
    """Fallback when no approval store is configured."""

    async def create(self, request: Any) -> Any:
        return request

    async def get(self, approval_id: str) -> Any:
        raise KeyError(approval_id)

    async def approve(self, approval_id: str, approved_by: str, reason: str | None = None) -> Any:
        raise RuntimeError("No approval store configured.")

    async def reject(self, approval_id: str, rejected_by: str, reason: str | None = None) -> Any:
        raise RuntimeError("No approval store configured.")

    async def list_pending(self, tenant_id: str | None = None) -> list:
        return []


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


class _null_ctx:
    """Async no-op context manager for when semaphore is None."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# Phase 14.0: Workflow status mapping helper
# ---------------------------------------------------------------------------


def _map_workflow_status(overall_status: str) -> Any:
    """Map executor internal status string to WorkflowRunStatus enum.

    Args:
        overall_status: Internal status string ("completed", "failed",
            "interrupted", etc.).

    Returns:
        Corresponding WorkflowRunStatus value.
    """
    from agent_app.runtime.dag_run_state import WorkflowRunStatus

    status_map = {
        "completed": WorkflowRunStatus.COMPLETED,
        "failed": WorkflowRunStatus.FAILED,
        "interrupted": WorkflowRunStatus.FAILED,
        "compensating": WorkflowRunStatus.COMPENSATING,
        "compensated": WorkflowRunStatus.COMPENSATED,
        "partial": WorkflowRunStatus.PARTIAL,
    }
    return status_map.get(overall_status, WorkflowRunStatus.FAILED)
