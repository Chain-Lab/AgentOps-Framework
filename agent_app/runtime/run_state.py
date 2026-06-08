"""Run state persistence — models, protocol, and stores.

Phase 9: Framework-level run state abstraction for interrupted runs.
This is NOT OpenAI native RunState resume — it's a framework-level
persistence layer that records which runs were interrupted, why, and
what approvals are pending.

Architecture:
  - RunStateStatus: enum for run lifecycle states
  - InterruptedRun: data model capturing full interrupted run context
  - RunStateStore: protocol for persisting/querying run states
  - InMemoryRunStateStore: in-memory implementation
  - SQLiteRunStateStore: SQLite-backed implementation

OpenAI backend integration:
  - OpenAIAgentsBackend can store SDK-specific state in backend_state dict
  - Real OpenAI RunState resume is deferred to a future phase
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult


class RunStateStatus(str, Enum):
    """Lifecycle states for a persisted run."""

    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    RESUMED = "resumed"


class InterruptedRun(BaseModel):
    """Captures the full state of an interrupted run.

    This model is the framework-level representation of a run that was
    interrupted (e.g., pending approval). It is independent of any
    specific backend SDK.

    Attributes:
        run_id: Unique run identifier.
        status: Current lifecycle state.
        agent_name: Agent that was executing (if single-agent).
        workflow_name: Workflow that was executing (if multi-agent).
        workflow_type: Type of workflow (handoff, orchestrator, single).
        input: Original user input that triggered the run.
        context: Full RunContext at the time of interruption.
        interruptions: List of interruption dicts from AppRunResult.
        approval_ids: Extracted approval IDs from interruptions.
        backend_name: Name of the execution backend ("dry_run" or "openai").
        backend_state: Backend-specific payload for future resume.
                      For OpenAI backend, this will hold RunState data
                      when real RunState resume is implemented.
        result_snapshot: Snapshot of AppRunResult at interruption time.
        created_at: When the run was first saved.
        updated_at: Last time the run state was modified.
        resumed_at: When the run was resumed (if applicable).
        error: Error details if status is FAILED.
    """

    run_id: str = Field(..., description="Unique run identifier")
    status: str = Field(
        default=RunStateStatus.INTERRUPTED.value,
        description="Run lifecycle state",
    )
    agent_name: str | None = Field(default=None, description="Executing agent")
    workflow_name: str | None = Field(default=None, description="Executing workflow")
    workflow_type: str | None = Field(default=None, description="Workflow type")
    input: str = Field(..., description="Original user input")
    context: RunContext = Field(..., description="Run context at interruption")
    interruptions: list[dict[str, Any]] = Field(
        default_factory=list, description="Interruption details"
    )
    approval_ids: list[str] = Field(
        default_factory=list, description="Pending approval IDs"
    )
    backend_name: str = Field(default="dry_run", description="Execution backend")
    backend_state: dict[str, Any] = Field(
        default_factory=dict, description="Backend-specific resume payload"
    )
    result_snapshot: dict[str, Any] | None = Field(
        default=None, description="AppRunResult snapshot"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last update timestamp",
    )
    resumed_at: datetime | None = Field(default=None, description="Resume timestamp")
    error: dict[str, Any] | None = Field(default=None, description="Error details")

    def extract_approval_ids(self) -> list[str]:
        """Extract approval IDs from interruptions.

        Scans the interruptions list for approval_required entries
        and collects their approval_id values.

        Returns:
            List of approval ID strings.
        """
        ids: list[str] = []
        for interruption in self.interruptions:
            if interruption.get("type") == "approval_required":
                apv_id = interruption.get("approval_id")
                if apv_id:
                    ids.append(apv_id)
        return ids

    def is_resumable(self) -> bool:
        """Check if this run can be resumed.

        A run is resumable if it's in INTERRUPTED status and has
        at least one pending approval ID.

        Returns:
            True if the run can be resumed.
        """
        return (
            self.status == RunStateStatus.INTERRUPTED.value
            and len(self.approval_ids) > 0
        )


class RunStateStore(Protocol):
    """Protocol for persisting and querying run states.

    Implementations store InterruptedRun instances and provide
    lifecycle methods (save, get, mark_resumed, etc.).
    """

    async def save_interrupted(self, run: InterruptedRun) -> InterruptedRun:
        """Save an interrupted run.

        If a run with the same run_id already exists, update it.

        Args:
            run: The InterruptedRun to save.

        Returns:
            The saved InterruptedRun.
        """
        ...

    async def get(self, run_id: str) -> InterruptedRun:
        """Retrieve a run by ID.

        Args:
            run_id: The run identifier.

        Returns:
            The InterruptedRun.

        Raises:
            KeyError: If the run_id is not found.
        """
        ...

    async def mark_resumed(self, run_id: str) -> InterruptedRun:
        """Mark a run as resumed.

        Updates status to RESUMED and sets resumed_at timestamp.

        Args:
            run_id: The run identifier.

        Returns:
            The updated InterruptedRun.

        Raises:
            KeyError: If the run_id is not found.
        """
        ...

    async def mark_completed(self, run_id: str) -> InterruptedRun:
        """Mark a run as completed.

        Args:
            run_id: The run identifier.

        Returns:
            The updated InterruptedRun.

        Raises:
            KeyError: If the run_id is not found.
        """
        ...

    async def mark_failed(self, run_id: str, error: dict[str, Any]) -> InterruptedRun:
        """Mark a run as failed.

        Args:
            run_id: The run identifier.
            error: Error details dict.

        Returns:
            The updated InterruptedRun.

        Raises:
            KeyError: If the run_id is not found.
        """
        ...

    async def list_interrupted(self, tenant_id: str | None = None) -> list[InterruptedRun]:
        """List all interrupted runs, optionally filtered by tenant.

        Only returns runs with status=INTERRUPTED. Completed, failed,
        and resumed runs are excluded.

        Args:
            tenant_id: Optional tenant filter.

        Returns:
            List of InterruptedRun instances with status=INTERRUPTED.
        """
        ...


def _serialize_run(run: InterruptedRun) -> dict[str, Any]:
    """Serialize an InterruptedRun to a JSON-compatible dict.

    Handles RunContext and AppRunResult serialization.
    """
    return {
        "run_id": run.run_id,
        "status": run.status,
        "agent_name": run.agent_name,
        "workflow_name": run.workflow_name,
        "workflow_type": run.workflow_type,
        "input": run.input,
        "context": run.context.model_dump(mode="json"),
        "interruptions": run.interruptions,
        "approval_ids": run.approval_ids,
        "backend_name": run.backend_name,
        "backend_state": run.backend_state,
        "result_snapshot": run.result_snapshot,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "resumed_at": run.resumed_at.isoformat() if run.resumed_at else None,
        "error": run.error,
    }


def _deserialize_run(data: dict[str, Any]) -> InterruptedRun:
    """Deserialize a dict back to an InterruptedRun."""
    data["context"] = RunContext(**data["context"])
    if data.get("created_at"):
        data["created_at"] = datetime.fromisoformat(data["created_at"])
    if data.get("updated_at"):
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
    if data.get("resumed_at"):
        data["resumed_at"] = datetime.fromisoformat(data["resumed_at"])
    return InterruptedRun(**data)
