"""Recovery service — Phase 16.5.

Provides manual, lease-protected recovery of persisted DAG workflow runs.

Recovery is always operator-triggered.  The service acquires a lease before
resuming and releases it afterwards.  Audit events are recorded best-effort.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent_app.runtime.dag_run_state import WorkerIdentity
from agent_app.runtime.lease_backend import LeasePolicy, WorkflowLeaseBackend
from agent_app.runtime.recovery_models import (
    ManualRecoveryResult,
    RecoveryCandidate,
    RecoveryScanConfig,
    RecoveryScanResult,
    RecoveryRecommendation,
)
from agent_app.runtime.recovery_scanner import RecoveryScanner

if TYPE_CHECKING:
    from agent_app.core.app import AgentApp
    from agent_app.governance.audit import AuditLogger
    from agent_app.runtime.dag_state_store import WorkflowStateStore

from agent_app.governance.audit import AuditEvent

logger = logging.getLogger(__name__)


class RecoveryService:
    """Manual recovery service with lease protection.

    Wraps :class:`RecoveryScanner` and adds the ability to explicitly
    trigger recovery of a single workflow run.  Recovery requires:

    1. The run must pass scanner inspection (resumable or inspectable).
    2. A lease must be acquired before calling ``resume_workflow_run()``.
    3. The lease is released after recovery succeeds or fails.

    Args:
        app: The AgentApp instance (used to call ``resume_workflow_run``).
        state_store: Workflow state store for inspection.
        lease_backend: Lease backend for acquire/release during recovery.
        audit_logger: Optional audit logger for recovery events.
    """

    def __init__(
        self,
        app: AgentApp,
        state_store: WorkflowStateStore,
        lease_backend: WorkflowLeaseBackend,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._app = app
        self._state_store = state_store
        self._lease_backend = lease_backend
        self._audit_logger = audit_logger
        self._scanner = RecoveryScanner(state_store, lease_backend)

    async def scan(
        self,
        config: RecoveryScanConfig | None = None,
    ) -> RecoveryScanResult:
        """Scan for recovery candidates (read-only).

        Args:
            config: Scan configuration.

        Returns:
            RecoveryScanResult.
        """
        return await self._scanner.scan(config)

    async def inspect_run(self, run_id: str) -> RecoveryCandidate:
        """Inspect a single run.

        Args:
            run_id: The run to inspect.

        Returns:
            RecoveryCandidate.
        """
        return await self._scanner.inspect_run(run_id)

    async def recover_run(
        self,
        workflow: str,
        run_id: str,
        recovered_by: str,
        resume_policy: Any = None,
    ) -> ManualRecoveryResult:
        """Attempt to manually recover a workflow run.

        The recovery flow:

        1. Inspect the run to determine resumability and lease state.
        2. Reject if recommendation is WAIT_FOR_ACTIVE_LEASE or DO_NOT_RESUME.
        3. Acquire a lease as ``recovered_by``.
        4. Call ``app.resume_workflow_run()``.
        5. Release the lease (best-effort, even on failure).

        Args:
            workflow: Name of the workflow to resume.
            run_id: The run ID to recover.
            recovered_by: Identity of the operator performing recovery.
            resume_policy: Optional resume policy.

        Returns:
            ManualRecoveryResult with outcome details.
        """
        result = ManualRecoveryResult(run_id=run_id, attempted=False)

        if self._state_store is None:
            result.error = {
                "type": "no_state_store",
                "message": "Recovery requires a workflow state store.",
            }
            result.status = "no_state_store"
            return result

        if self._lease_backend is None:
            result.error = {
                "type": "no_lease_backend",
                "message": "Recovery requires a lease backend.",
            }
            result.status = "no_lease_backend"
            return result

        # -- Step 1: Inspect --
        try:
            candidate = await self._scanner.inspect_run(run_id)
        except KeyError:
            result.error = {
                "type": "not_found",
                "message": f"Workflow run '{run_id}' not found.",
            }
            result.status = "not_found"
            await self._audit("recovery.failed", run_id, recovered_by, result.error)
            return result
        except Exception as exc:
            result.error = {"type": "inspect_failed", "message": str(exc)}
            result.status = "inspect_failed"
            await self._audit("recovery.failed", run_id, recovered_by, result.error)
            return result

        # -- Step 2: Check recommendation --
        if candidate.recommendation == RecoveryRecommendation.WAIT_FOR_ACTIVE_LEASE:
            result.error = {
                "type": "active_lease",
                "message": (
                    f"Run '{run_id}' has an active lease held by "
                    f"'{candidate.lease_owner}'. "
                    f"Wait for the lease to expire or be released."
                ),
                "lease_owner": candidate.lease_owner,
                "lease_expires_at": (
                    candidate.lease_expires_at.isoformat()
                    if candidate.lease_expires_at
                    else None
                ),
            }
            result.status = "blocked_active_lease"
            await self._audit(
                "recovery.skipped_active_lease", run_id, recovered_by, result.error
            )
            return result

        if candidate.recommendation == RecoveryRecommendation.DO_NOT_RESUME:
            result.error = {
                "type": "not_resumable",
                "message": candidate.error.get("reason", "Run is not resumable."),
                "reasons": [r.value for r in candidate.reasons],
            }
            result.status = "not_resumable"
            await self._audit(
                "recovery.skipped_not_resumable", run_id, recovered_by, result.error
            )
            return result

        # -- Step 3: Acquire lease --
        worker = WorkerIdentity(worker_id=recovered_by)
        lease_acquired = False
        try:
            from agent_app.runtime.dag_run_state import LeasePolicy
            policy = LeasePolicy()
            acquire_result = await self._lease_backend.acquire_run_lease(
                run_id=run_id,
                worker=worker,
                policy=policy,
            )
            lease_acquired = acquire_result.acquired
            if not lease_acquired:
                result.error = {
                    "type": "lease_denied",
                    "message": acquire_result.reason or "Lease acquire denied.",
                    "current_owner": acquire_result.current_owner_id,
                    "expires_at": (
                        acquire_result.expires_at.isoformat()
                        if acquire_result.expires_at
                        else None
                    ),
                }
                result.status = "lease_denied"
                await self._audit(
                    "recovery.failed", run_id, recovered_by, result.error
                )
                return result
        except Exception as exc:
            result.error = {
                "type": "lease_error",
                "message": f"Lease acquire failed: {exc}",
            }
            result.status = "lease_error"
            await self._audit(
                "recovery.failed", run_id, recovered_by, result.error
            )
            return result

        result.lease_acquired = True
        result.attempted = True

        # -- Step 4: Audit recovery.started --
        await self._audit(
            "recovery.started",
            run_id,
            recovered_by,
            {
                "workflow": workflow,
                "recommendation": candidate.recommendation.value,
                "reasons": [r.value for r in candidate.reasons],
            },
        )

        # -- Step 5: Resume --
        try:
            resume_result = await self._app.resume_workflow_run(
                workflow=workflow,
                run_id=run_id,
                worker=worker,
            )
            result.recovered = getattr(resume_result, "status", "") == "completed"
            result.status = getattr(resume_result, "status", "unknown")
            result.result = resume_result
        except Exception as exc:
            result.recovered = False
            result.status = "resume_failed"
            result.error = {
                "type": "resume_error",
                "message": str(exc),
            }
            await self._audit(
                "recovery.failed", run_id, recovered_by, result.error
            )
        else:
            await self._audit(
                "recovery.completed",
                run_id,
                recovered_by,
                {
                    "workflow": workflow,
                    "recovered": result.recovered,
                    "status": result.status,
                },
            )

        # -- Step 6: Release lease (best-effort) --
        try:
            await self._lease_backend.release_run_lease(
                run_id=run_id,
                worker=worker,
            )
            result.lease_released = True
        except Exception as exc:
            logger.warning(
                "Failed to release lease for %s after recovery: %s", run_id, exc
            )
            result.lease_released = False
            if result.error is None:
                result.error = {}
            result.error["lease_release_error"] = str(exc)

        return result

    async def _audit(
        self,
        event_type: str,
        run_id: str,
        user_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record an audit event (best-effort, never raises)."""
        if self._audit_logger is None:
            return
        try:
            event = AuditEvent(
                event_id=_make_event_id(event_type, run_id),
                run_id=run_id,
                event_type=event_type,
                user_id=user_id,
                data=data or {},
            )
            await self._audit_logger.log(event)
        except Exception as exc:
            logger.debug("Audit log failed for %s: %s", event_type, exc)


def _make_event_id(event_type: str, run_id: str) -> str:
    """Generate a simple audit event ID."""
    import time
    return f"{event_type}:{run_id}:{int(time.time() * 1000)}"
