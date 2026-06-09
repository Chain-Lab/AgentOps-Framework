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


class RecoveryDaemonTickResult(BaseModel):
    """Result of a single RecoveryDaemon tick (scan cycle).

    Attributes:
        scanned_count: Number of runs examined by the scanner.
        selected_count: Number of candidates selected for recovery.
        recovered_count: Number of runs successfully recovered.
        skipped_count: Number of candidates that were skipped.
        failed_count: Number of recovery attempts that failed.
        dry_run: Whether this was a dry-run (no actual recovery attempted).
        selected_run_ids: Run IDs selected for recovery in this tick.
        recovered_run_ids: Run IDs that were successfully recovered.
        skipped: List of skip reasons, each with run_id and reason.
        failures: List of failure details, each with run_id and error.
    """

    scanned_count: int = 0
    selected_count: int = 0
    recovered_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    dry_run: bool = True
    selected_run_ids: list[str] = Field(default_factory=list)
    recovered_run_ids: list[str] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)


class AutoRecoveryPolicy(BaseModel):
    """Policy for automatic recovery by RecoveryDaemon.

    All defaults are conservative: disabled, dry-run, single-threaded,
    and no automatic recovery of completed runs.

    Attributes:
        enabled: Whether automatic recovery is enabled. Defaults to False.
        interval_seconds: Seconds between daemon scan cycles. Defaults to 30.
        stale_after_seconds: Seconds before a running run is considered stale.
        statuses: Run statuses to scan for candidates.
        workflow_name: Optional filter for a specific workflow name.
        tenant_id: Optional filter for a specific tenant.
        include_completed: Whether to include completed runs. Defaults to False.
        max_candidates_per_scan: Maximum candidates to evaluate per scan.
        max_recoveries_per_scan: Maximum recoveries per scan cycle.
        max_concurrent_recoveries: Maximum concurrent recovery operations.
        dry_run: If True, log what would be recovered but do not act.
        recover_failed: Auto-recover failed runs that are resumable.
        recover_stale_running: Auto-recover stale running runs with expired/missing lease.
        recover_compensating: Auto-recover compensating runs that are resumable.
    """

    enabled: bool = Field(
        default=False,
        description="Enable automatic recovery daemon",
    )
    interval_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Seconds between scan cycles",
    )
    stale_after_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Seconds before a running run is considered stale",
    )
    statuses: list[str] = Field(
        default_factory=lambda: ["running", "failed", "compensating"],
        description="Run statuses to scan",
    )
    workflow_name: str | None = Field(
        default=None,
        description="Filter by workflow name",
    )
    tenant_id: str | None = Field(
        default=None,
        description="Filter by tenant ID",
    )
    include_completed: bool = Field(
        default=False,
        description="Include completed runs in scan",
    )
    max_candidates_per_scan: int = Field(
        default=50,
        gt=0,
        description="Max candidates to evaluate per scan",
    )
    max_recoveries_per_scan: int = Field(
        default=5,
        gt=0,
        description="Max recoveries per scan cycle",
    )
    max_concurrent_recoveries: int = Field(
        default=1,
        gt=0,
        description="Max concurrent recovery operations",
    )
    dry_run: bool = Field(
        default=True,
        description="If True, only log what would be recovered",
    )
    recover_failed: bool = Field(
        default=True,
        description="Auto-recover failed runs",
    )
    recover_stale_running: bool = Field(
        default=True,
        description="Auto-recover stale running runs with expired/missing lease",
    )
    recover_compensating: bool = Field(
        default=True,
        description="Auto-recover compensating runs",
    )


# ---------------------------------------------------------------------------
# Phase 18: Observability models
# ---------------------------------------------------------------------------


class RecoverySystemStatus(BaseModel):
    """Snapshot of the recovery subsystem's current configuration and health.

    Attributes:
        enabled: Whether automatic recovery is enabled.
        dry_run: Whether the daemon is in dry-run mode.
        daemon_configured: Whether a recovery daemon can be created.
        scanner_available: Whether the scanner dependencies are available.
        recovery_service_available: Whether the recovery service deps are available.
        last_tick_at: When the most recent daemon tick completed.
        last_tick_result: Result of the most recent daemon tick, if any.
        policy: The current auto-recovery policy in use.
    """

    enabled: bool = False
    dry_run: bool = True
    daemon_configured: bool = False
    scanner_available: bool = False
    recovery_service_available: bool = False
    last_tick_at: datetime | None = None
    last_tick_result: RecoveryDaemonTickResult | None = None
    policy: AutoRecoveryPolicy | None = None
