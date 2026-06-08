"""Structured run events for observability."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RunEventType(str, Enum):
    """Categories of runtime events emitted during agent execution."""

    # Run lifecycle
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_INTERRUPTED = "run.interrupted"

    # Workflow lifecycle
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"

    # Routing / handoff
    ROUTING_DECISION = "routing.decision"
    HANDOFF_OCCURRED = "handoff.occurred"

    # Agent lifecycle
    AGENT_STARTED = "agent.started"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"

    # Tool lifecycle
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_WAITING = "tool.waiting"
    TOOL_PERMISSION_DENIED = "tool.permission_denied"
    TOOL_APPROVAL_REQUIRED = "tool.approval_required"

    # Approval lifecycle
    APPROVAL_CREATED = "approval.created"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"

    # Run state lifecycle
    RUN_STATE_SAVED = "run_state.saved"
    RUN_STATE_RESUMED = "run_state.resumed"

    # DAG node lifecycle (Phase 13+)
    NODE_READY = "node.ready"
    NODE_STARTED = "node.started"
    NODE_COMPLETED = "node.completed"
    NODE_FAILED = "node.failed"
    NODE_SKIPPED = "node.skipped"
    NODE_CONDITION_EVAL = "node.condition_evaluated"
    NODE_TIMEOUT = "node.timeout"
    FUNCTION_PERMISSION_DENIED = "function.permission_denied"
    RETRY_SCHEDULED = "retry.scheduled"
    RETRY_STARTED = "retry.started"
    RETRY_EXHAUSTED = "retry.exhausted"

    # Subworkflow lifecycle (Phase 13.6)
    SUBWORKFLOW_STARTED = "subworkflow.started"
    SUBWORKFLOW_COMPLETED = "subworkflow.completed"
    SUBWORKFLOW_FAILED = "subworkflow.failed"

    # Workflow deadline (Phase 13.8)
    WORKFLOW_DEADLINE_EXCEEDED = "workflow.deadline_exceeded"
    NODE_CANCELLED_BY_DEADLINE = "node.cancelled_by_deadline"

    # Compensation / rollback (Phase 13.9)
    WORKFLOW_COMPENSATION_STARTED = "workflow.compensation_started"
    WORKFLOW_COMPENSATION_COMPLETED = "workflow.compensation_completed"
    WORKFLOW_COMPENSATION_FAILED = "workflow.compensation_failed"
    NODE_COMPENSATION_STARTED = "node.compensation_started"
    NODE_COMPENSATION_COMPLETED = "node.compensation_completed"
    NODE_COMPENSATION_FAILED = "node.compensation_failed"
    NODE_COMPENSATION_SKIPPED = "node.compensation_skipped"


def _uid() -> str:
    """Generate a short unique identifier for events."""
    return uuid.uuid4().hex[:12]


class RunEvent(BaseModel):
    """A single structured event emitted during an agent run.

    Attributes:
        event_id: Unique event identifier.
        trace_id: Groups all events for a single run.
        run_id: Logical run identifier (matches RunContext.run_id).
        event_type: Category of event.
        timestamp: When the event occurred (timezone-aware UTC).
        user_id: End-user identifier.
        tenant_id: Multi-tenant identifier.
        workflow_name: Name of the workflow being executed.
        workflow_type: Type of workflow (single, handoff, orchestrator).
        agent_name: Agent associated with this event.
        tool_name: Tool associated with this event.
        approval_id: Approval request identifier.
        status: Short status string (e.g. "completed", "failed").
        duration_ms: Wall-clock duration in milliseconds.
        error: Structured error details when status indicates failure.
        data: Arbitrary JSON-serializable extra data.
    """

    event_id: str = Field(default_factory=_uid, description="Unique event ID")
    trace_id: str = Field(..., description="Groups events for a single run")
    event_type: RunEventType | str = Field(..., description="Event category")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Event timestamp (UTC)",
    )

    run_id: str | None = Field(default=None, description="Logical run ID")
    user_id: str | None = Field(default=None)
    tenant_id: str | None = Field(default=None)

    workflow_name: str | None = Field(default=None)
    workflow_type: str | None = Field(default=None)
    agent_name: str | None = Field(default=None)
    tool_name: str | None = Field(default=None)
    approval_id: str | None = Field(default=None)

    status: str | None = Field(default=None)
    duration_ms: int | None = Field(default=None)
    error: dict[str, Any] | None = Field(default=None)
    data: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat(),
        },
    }
