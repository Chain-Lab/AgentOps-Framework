"""Recovery scanner — Phase 16.5.

Read-only scanner that inspects persisted DAG workflow runs and identifies
recovery candidates based on status, lease state, and resumability.

Does NOT perform automatic recovery.  Recovery must be triggered explicitly
via RecoveryService.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agent_app.runtime.dag_run_state import (
    CompensationRunStatus,
    NodeRunStatus,
    RecoveryPlan,
)
from agent_app.runtime.lease_backend import WorkflowLeaseBackend
from agent_app.runtime.recovery_models import (
    RecoveryCandidate,
    RecoveryCandidateReason,
    RecoveryRecommendation,
    RecoveryScanConfig,
    RecoveryScanResult,
)

if TYPE_CHECKING:
    from agent_app.runtime.dag_state_store import WorkflowStateStore

logger = logging.getLogger(__name__)

# Status values treated as "running"
_RUNNING_STATUSES = {"running", "pending", "started"}
# Status values treated as "compensating"
_COMPENSATING_STATUSES = {"compensating", "compensation_started"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RecoveryScanner:
    """Scan persisted workflow runs for recovery candidates.

    The scanner is **read-only** — it never modifies run state, never
    acquires or releases leases, and never calls resume.

    Args:
        state_store: The workflow state store to read from.
        lease_backend: Optional lease backend for lease-aware recommendations.
    """

    def __init__(
        self,
        state_store: WorkflowStateStore,
        lease_backend: WorkflowLeaseBackend | None = None,
    ) -> None:
        self._state_store = state_store
        self._lease_backend = lease_backend

    async def scan(
        self,
        config: RecoveryScanConfig | None = None,
    ) -> RecoveryScanResult:
        """Scan for recovery candidates.

        Args:
            config: Scan configuration. Uses defaults if not provided.

        Returns:
            RecoveryScanResult with all candidates found.
        """
        config = config or RecoveryScanConfig()
        started = _now()

        # Build status filter
        statuses: list[str] | None = None
        if config.include_failed and config.include_running and config.include_compensating:
            # Scan all — no status filter needed
            statuses = None
        else:
            statuses = []
            if config.include_failed:
                statuses.append("failed")
            if config.include_running:
                statuses.extend(_RUNNING_STATUSES)
            if config.include_compensating:
                statuses.extend(_COMPENSATING_STATUSES)
            if config.include_completed:
                statuses.append("completed")
            if not statuses:
                statuses = None

        updated_before = _now()

        try:
            # Fetch more than the user limit so total_scanned is accurate.
            # The user-facing limit is applied to candidates after evaluation.
            internal_limit = max(config.limit * 10, 1000)
            runs = await self._state_store.list_runs(
                statuses=statuses,
                updated_before=updated_before,
                workflow_name=config.workflow_name,
                limit=internal_limit,
            )
        except Exception as exc:
            logger.error("Failed to list runs for recovery scan: %s", exc)
            return RecoveryScanResult(
                scanned_at=started,
                total_scanned=0,
                candidate_count=0,
                errors=[{"error": str(exc)}],
            )

        candidates: list[RecoveryCandidate] = []
        errors: list[dict[str, Any]] = []

        for run in runs:
            try:
                candidate = await self._evaluate_run(run, config)
                if candidate is not None:
                    candidates.append(candidate)
            except Exception as exc:
                logger.warning(
                    "Error evaluating run %s for recovery: %s", run.run_id, exc
                )
                errors.append({"run_id": run.run_id, "error": str(exc)})

        # Apply user limit to candidates (we fetched more from the store)
        candidates = candidates[:config.limit]

        return RecoveryScanResult(
            scanned_at=started,
            total_scanned=len(runs),
            candidate_count=len(candidates),
            candidates=candidates,
            errors=errors,
        )

    async def inspect_run(self, run_id: str) -> RecoveryCandidate:
        """Inspect a single run and produce a recovery candidate.

        Args:
            run_id: The workflow run to inspect.

        Returns:
            RecoveryCandidate for the specified run.

        Raises:
            KeyError: If the run_id is not found in the state store.
        """
        run = await self._state_store.get_run(run_id)
        return await self._evaluate_run(run, RecoveryScanConfig())

    # -- Private helpers --

    async def _evaluate_run(
        self,
        run: Any,
        config: RecoveryScanConfig,
    ) -> RecoveryCandidate | None:
        """Evaluate a single run and return a RecoveryCandidate, or None if it
        should not be a candidate (e.g. completed and not included)."""
        now = _now()
        updated_at = run.updated_at
        age_seconds: float | None = None
        if updated_at:
            age_seconds = (now - updated_at).total_seconds()

        # -- Determine candidate eligibility --
        is_running = run.status in _RUNNING_STATUSES
        is_failed = run.status == "failed"
        is_completed = run.status == "completed"
        is_compensating = run.status in _COMPENSATING_STATUSES

        is_stale = (
            is_running
            and age_seconds is not None
            and age_seconds > config.stale_after_seconds
        )
        is_long_running = (
            is_running
            and age_seconds is not None
            and age_seconds > config.running_after_seconds
        )

        # A run is a candidate if:
        # - failed (and include_failed)
        # - running AND (stale or long-running)
        # - compensating (and include_compensating)
        # - completed (and include_completed)
        is_candidate = False
        if is_failed and config.include_failed:
            is_candidate = True
        elif is_running and (is_stale or is_long_running) and config.include_running:
            is_candidate = True
        elif is_compensating and config.include_compensating:
            is_candidate = True
        elif is_completed and config.include_completed:
            is_candidate = True

        if not is_candidate:
            return None

        # -- Build candidate --
        reasons: list[RecoveryCandidateReason] = []
        recommendation = RecoveryRecommendation.INSPECT_ONLY

        # -- Check compensation --
        compensation_active = False
        try:
            compensations = await self._state_store.list_compensations(run.run_id)
            for comp in compensations:
                if comp.status in (
                    CompensationRunStatus.PENDING.value,
                    CompensationRunStatus.RUNNING.value,
                ):
                    compensation_active = True
                    break
        except Exception:
            pass

        if compensation_active:
            reasons.append(RecoveryCandidateReason.COMPENSATION_INCOMPLETE)
            recommendation = RecoveryRecommendation.MANUAL_REVIEW

        # -- Check nodes --
        failed_nodes: list[str] = []
        interrupted_nodes: list[str] = []
        resumable = True
        resumable_reason: str | None = None

        try:
            nodes = await self._state_store.list_nodes(run.run_id)
            for node in nodes:
                if node.status == NodeRunStatus.FAILED.value:
                    failed_nodes.append(node.node_id)
                elif node.status == NodeRunStatus.RUNNING.value:
                    if not node.completed_at:
                        interrupted_nodes.append(node.node_id)

            # Build recovery plan only when there are node-level details to check
            if failed_nodes or interrupted_nodes:
                recovery_plan = await self._state_store.build_recovery_plan(run.run_id)
                resumable = recovery_plan.resumable
                resumable_reason = recovery_plan.reason
            elif is_running and compensation_active:
                resumable = False
                resumable_reason = "Compensation has started."
            # else: no nodes, not compensating → resumable (stale run can retry)

            if not resumable:
                reasons.append(RecoveryCandidateReason.NOT_RESUMABLE)
        except Exception as exc:
            logger.debug("Could not load nodes for %s: %s", run.run_id, exc)

        if failed_nodes:
            reasons.append(RecoveryCandidateReason.NODE_FAILED)
        if interrupted_nodes:
            reasons.append(RecoveryCandidateReason.NODE_INTERRUPTED)

        # -- Stale / long-running detection --
        if is_stale:
            reasons.append(RecoveryCandidateReason.RUN_STALE)
        if is_long_running:
            reasons.append(RecoveryCandidateReason.RUNNING_TOO_LONG)

        # -- Check lease --
        lease_present = False
        lease_owner: str | None = None
        lease_expires_at: datetime | None = None
        lease_expired: bool | None = None
        lease_active = False

        if self._lease_backend is not None:
            try:
                lease = await self._lease_backend.get_run_lease(run.run_id)
                if lease is not None:
                    lease_present = True
                    lease_owner = lease.owner_id
                    lease_expires_at = lease.expires_at
                    lease_expired = now >= lease.expires_at
                    lease_active = not lease_expired
                else:
                    lease_expired = None
            except Exception as exc:
                logger.debug("Lease lookup failed for %s: %s", run.run_id, exc)
                lease_present = False

        if not lease_present and is_running:
            reasons.append(RecoveryCandidateReason.LEASE_MISSING)
        elif lease_present and lease_expired:
            reasons.append(RecoveryCandidateReason.LEASE_EXPIRED)

        # -- Stale / long-running (already computed above) --
        if is_stale:
            reasons.append(RecoveryCandidateReason.RUN_STALE)
        if is_long_running:
            reasons.append(RecoveryCandidateReason.RUNNING_TOO_LONG)

        # -- Resume plan --
        resume_plan_summary: dict[str, Any] = {}
        try:
            from agent_app.runtime.dag_run_state import ResumePolicy
            resume_plan = await self._state_store.build_resume_plan(
                run.run_id, ResumePolicy()
            )
            resume_plan_summary = {
                "total_nodes": len(resume_plan.nodes),
                "resumable_nodes": sum(
                    1 for n in resume_plan.nodes if n.decision.value == "resume"
                ),
                "skipped_nodes": sum(
                    1 for n in resume_plan.nodes if n.decision.value == "skip"
                ),
            }
            reasons.append(RecoveryCandidateReason.RESUME_PLAN_AVAILABLE)
        except Exception as exc:
            logger.debug("Could not build resume plan for %s: %s", run.run_id, exc)

        # -- Recovery plan summary --
        recovery_plan_summary: dict[str, Any] = {}
        try:
            recovery_plan = await self._state_store.build_recovery_plan(run.run_id)
            recovery_plan_summary = {
                "resumable": recovery_plan.resumable,
                "completed_nodes": len(recovery_plan.completed_nodes),
                "interrupted_nodes": len(recovery_plan.interrupted_nodes),
                "failed_nodes": len(recovery_plan.failed_nodes),
                "compensation_started": recovery_plan.compensation_started,
            }
        except Exception as exc:
            logger.debug(
                "Could not build recovery plan for %s: %s", run.run_id, exc
            )

        # -- Determine recommendation --
        if not reasons:
            # No reasons — default to inspect only
            recommendation = RecoveryRecommendation.INSPECT_ONLY
        elif recommendation == RecoveryRecommendation.MANUAL_REVIEW:
            # Compensation is active — already set above
            pass
        elif not resumable:
            recommendation = RecoveryRecommendation.DO_NOT_RESUME
        elif lease_active:
            recommendation = RecoveryRecommendation.WAIT_FOR_ACTIVE_LEASE
        elif run.status in _RUNNING_STATUSES and (is_stale or is_long_running):
            recommendation = RecoveryRecommendation.RESUME
        elif run.status == "failed" and resumable:
            recommendation = RecoveryRecommendation.RESUME
        elif run.status in _COMPENSATING_STATUSES:
            recommendation = RecoveryRecommendation.MANUAL_REVIEW
        else:
            recommendation = RecoveryRecommendation.INSPECT_ONLY

        # Build error if not resumable
        error: dict[str, Any] | None = None
        if not resumable and resumable_reason:
            error = {"type": "not_resumable", "reason": resumable_reason}

        return RecoveryCandidate(
            run_id=run.run_id,
            workflow_name=getattr(run, "workflow_name", None),
            status=run.status,
            updated_at=updated_at,
            age_seconds=age_seconds,
            reasons=reasons,
            recommendation=recommendation,
            lease_present=lease_present,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            lease_expired=lease_expired,
            resumable=resumable,
            resume_plan_summary=resume_plan_summary,
            recovery_plan_summary=recovery_plan_summary,
            error=error,
        )
