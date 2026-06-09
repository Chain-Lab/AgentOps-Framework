"""Recovery scanner models — Phase 16.5.

Provides Pydantic models for recovery scanning, candidate classification,
and manual recovery results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from agent_app.runtime.dag_run_state import RecoveryPlan


class RecoveryCandidateReason(StrEnum):
    """Why a workflow run was flagged as a recovery candidate."""

    RUNNING_TOO_LONG = "running_too_long"
    RUN_STALE = "run_stale"
    NODE_INTERRUPTED = "node_interrupted"
    NODE_FAILED = "node_failed"
    LEASE_EXPIRED = "lease_expired"
    LEASE_MISSING = "lease_missing"
    COMPENSATION_INCOMPLETE = "compensation_incomplete"
    SNAPSHOT_AVAILABLE = "snapshot_available"
    RESUME_PLAN_AVAILABLE = "resume_plan_available"
    NOT_RESUMABLE = "not_resumable"


class RecoveryRecommendation(StrEnum):
    """Suggested action for a recovery candidate."""

    INSPECT_ONLY = "inspect_only"
    RESUME = "resume"
    WAIT_FOR_ACTIVE_LEASE = "wait_for_active_lease"
    MANUAL_REVIEW = "manual_review"
    DO_NOT_RESUME = "do_not_resume"


class RecoveryScanConfig(BaseModel):
    """Configuration for a recovery scan.

    Attributes:
        stale_after_seconds: A running run is stale if unchanged for this many seconds.
        running_after_seconds: A run is considered long-running after this many seconds.
        include_completed: Include completed runs in the scan.
        include_failed: Include failed runs in the scan.
        include_running: Include running/active runs in the scan.
        include_compensating: Include runs with active compensation in the scan.
        limit: Maximum number of runs to scan.
        tenant_id: Filter to a specific tenant.
        workflow_name: Filter to a specific workflow name.
    """

    stale_after_seconds: int = Field(
        default=300, description="Seconds before a running run is considered stale"
    )
    running_after_seconds: int = Field(
        default=300, description="Seconds before a run is considered long-running"
    )
    include_completed: bool = Field(
        default=False, description="Include completed runs"
    )
    include_failed: bool = Field(default=True, description="Include failed runs")
    include_running: bool = Field(default=True, description="Include running runs")
    include_compensating: bool = Field(
        default=True, description="Include compensating runs"
    )
    limit: int = Field(default=100, description="Maximum runs to scan", ge=1, le=1000)
    tenant_id: str | None = Field(default=None, description="Filter by tenant ID")
    workflow_name: str | None = Field(
        default=None, description="Filter by workflow name"
    )


class RecoveryCandidate(BaseModel):
    """A workflow run flagged as a potential recovery target.

    Attributes:
        run_id: The workflow run identifier.
        workflow_name: The workflow name, if known.
        status: Current run status.
        updated_at: When the run was last updated.
        age_seconds: How long since the last update.
        reasons: Why this run is a candidate.
        recommendation: Suggested action.
        lease_present: Whether a lease exists for this run.
        lease_owner: Who holds the lease, if any.
        lease_expires_at: When the lease expires.
        lease_expired: Whether the lease has expired.
        resumable: Whether the run can be resumed.
        resume_plan_summary: Summary of the resume plan.
        recovery_plan_summary: Summary of the recovery plan.
        error: Error information, if any.
    """

    run_id: str
    workflow_name: str | None = None
    status: str
    updated_at: datetime | None = None
    age_seconds: float | None = None

    reasons: list[RecoveryCandidateReason] = Field(default_factory=list)
    recommendation: RecoveryRecommendation = RecoveryRecommendation.INSPECT_ONLY

    lease_present: bool = False
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    lease_expired: bool | None = None

    resumable: bool | None = None
    resume_plan_summary: dict[str, Any] = Field(default_factory=dict)
    recovery_plan_summary: dict[str, Any] = Field(default_factory=dict)

    error: dict[str, Any] | None = None


class RecoveryScanResult(BaseModel):
    """Result of a recovery scan.

    Attributes:
        scanned_at: When the scan was performed.
        total_scanned: Total number of runs examined.
        candidate_count: Number of candidates found.
        candidates: List of recovery candidates.
        errors: Non-fatal errors encountered during scanning.
    """

    scanned_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    total_scanned: int = 0
    candidate_count: int = 0
    candidates: list[RecoveryCandidate] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class ManualRecoveryResult(BaseModel):
    """Result of a manual recovery attempt.

    Attributes:
        run_id: The workflow run that was recovered.
        attempted: Whether recovery was attempted.
        recovered: Whether recovery succeeded.
        status: Final status after recovery attempt.
        lease_acquired: Whether the lease was acquired.
        lease_released: Whether the lease was released.
        result: The AppRunResult from resume, if available.
        error: Error information, if recovery failed.
    """

    run_id: str
    attempted: bool = False
    recovered: bool = False
    status: str = ""
    lease_acquired: bool = False
    lease_released: bool = False
    result: Any = None
    error: dict[str, Any] | None = None
