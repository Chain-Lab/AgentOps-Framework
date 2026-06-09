"""Lease coordinator — unified entry point for lease backend operations.

Phase 16.2: Provides a thin coordination layer over ``WorkflowLeaseBackend``
that applies a default ``LeasePolicy`` when none is provided by the caller.
This gives DagExecutor and LeaseRenewer a stable, policy-aware interface
without duplicating default-policy logic at each call site.

Phase 16.3: Adds opt-in metrics, health checks, and diagnostics support.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from agent_app.runtime.dag_run_state import LeasePolicy

if TYPE_CHECKING:
    from agent_app.runtime.lease_backend import WorkflowLeaseBackend
    from agent_app.runtime.lease_health import (
        LeaseBackendHealthChecker,
        LeaseHealthCheckResult,
    )
    from agent_app.runtime.lease_metrics import LeaseMetrics, LeaseMetricsSnapshot
    from agent_app.runtime.dag_run_state import (
        LeaseAcquireResult,
        WorkerIdentity,
        WorkflowRunLease,
    )

logger = logging.getLogger(__name__)


class LeaseDiagnostics(BaseModel):
    """Lease backend diagnostic snapshot for operator visibility.

    Attributes:
        backend_type: Human-readable backend type.
        health: Latest health check result (if available).
        metrics: Metrics snapshot (if metrics enabled).
        expired_leases_count: Number of expired leases (if available).
        sample_expired_leases: Sample of expired lease details (limited).
        details: Additional backend-specific diagnostic details (Phase 16.4).
    """

    backend_type: str
    health: Any = None
    metrics: dict[str, Any] | None = None
    expired_leases_count: int | None = None
    sample_expired_leases: list[dict[str, Any]] = []
    details: dict[str, Any] | None = None


class LeaseCoordinator:
    """Unified coordinator for lease backend operations.

    Wraps a ``WorkflowLeaseBackend`` and applies a default ``LeasePolicy``
    to every operation that accepts a policy argument.  Callers can
    override the default on a per-call basis.

    Phase 16.3: Supports opt-in metrics, health checks, and diagnostics.

    Args:
        backend: The lease backend to coordinate.
        default_policy: Default policy applied when callers don't supply one.
        metrics: Optional ``LeaseMetrics`` instance for operation recording.

    Usage::

        coordinator = LeaseCoordinator(backend, default_policy=LeasePolicy())
        result = await coordinator.acquire(run_id, worker)
        lease = await coordinator.renew(run_id, worker)
        released = await coordinator.release(run_id, worker)
        health = await coordinator.health_check()
        diag = await coordinator.diagnostics()
    """

    def __init__(
        self,
        backend: Any,  # WorkflowLeaseBackend
        default_policy: LeasePolicy | None = None,
        metrics: Any = None,  # LeaseMetrics | None
    ) -> None:
        self._backend = backend
        self._default_policy = default_policy or LeasePolicy()
        self._metrics = metrics
        # Phase 16.3: Wrap backend with metrics if provided
        if metrics is not None:
            from agent_app.runtime.lease_backend import MetricsWorkflowLeaseBackend
            self._backend = MetricsWorkflowLeaseBackend(backend, metrics)

    def _policy(self, policy: LeasePolicy | None) -> LeasePolicy:
        """Return the effective policy (caller-supplied or default)."""
        return policy if policy is not None else self._default_policy

    async def acquire(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> LeaseAcquireResult:
        """Acquire a lease, applying the default policy if none provided."""
        effective = self._policy(policy)
        logger.debug(
            "LeaseCoordinator.acquire: run_id=%s, worker=%s, ttl=%s",
            run_id, worker.worker_id, effective.ttl_seconds,
        )
        return await self._backend.acquire_run_lease(run_id, worker, effective)

    async def renew(
        self,
        run_id: str,
        worker: WorkerIdentity,
        policy: LeasePolicy | None = None,
    ) -> WorkflowRunLease:
        """Renew a lease, applying the default policy if none provided."""
        effective = self._policy(policy)
        logger.debug(
            "LeaseCoordinator.renew: run_id=%s, worker=%s, ttl=%s",
            run_id, worker.worker_id, effective.ttl_seconds,
        )
        return await self._backend.renew_run_lease(run_id, worker, effective)

    async def release(
        self,
        run_id: str,
        worker: WorkerIdentity,
    ) -> WorkflowRunLease:
        """Release a lease."""
        logger.debug(
            "LeaseCoordinator.release: run_id=%s, worker=%s",
            run_id, worker.worker_id,
        )
        return await self._backend.release_run_lease(run_id, worker)

    async def get(
        self,
        run_id: str,
    ) -> WorkflowRunLease | None:
        """Get the current lease for a run."""
        return await self._backend.get_run_lease(run_id)

    async def list_expired(
        self,
        before: datetime | None = None,
    ) -> list[WorkflowRunLease]:
        """List expired leases."""
        cutoff = before or datetime.now(timezone.utc)
        logger.debug(
            "LeaseCoordinator.list_expired: before=%s",
            cutoff.isoformat(),
        )
        return await self._backend.list_expired_leases(cutoff)

    # -- Phase 16.3: Observability --

    def metrics_snapshot(self) -> Any | None:  # LeaseMetricsSnapshot | None
        """Return a snapshot of recorded metrics, or None if not enabled."""
        if self._metrics is None:
            return None
        return self._metrics.snapshot()

    async def health_check(self) -> Any:  # LeaseHealthCheckResult
        """Perform a lightweight health check on the lease backend.

        Returns a ``LeaseHealthCheckResult`` with status and details.
        Never raises — exceptions are captured in the result.
        """
        from agent_app.runtime.lease_health import LeaseBackendHealthChecker

        # Unwrap metrics wrapper if present
        backend = self._backend
        if hasattr(backend, "_backend"):
            backend = backend._backend  # type: ignore[attr-defined]

        checker = LeaseBackendHealthChecker(backend)
        return await checker.check()

    async def diagnostics(
        self,
        include_expired_sample: bool = False,
        expired_sample_limit: int = 10,
    ) -> LeaseDiagnostics:
        """Collect diagnostic information about the lease backend.

        Args:
            include_expired_sample: Whether to include a sample of expired
                lease details in the diagnostics.
            expired_sample_limit: Maximum number of expired lease samples
                to include.  Ignored if ``include_expired_sample`` is False.

        Returns:
            ``LeaseDiagnostics`` with health, metrics, and lease info.
        """
        # Unwrap metrics wrapper if present
        backend = self._backend
        if hasattr(backend, "_backend"):
            backend = backend._backend  # type: ignore[attr-defined]

        health = await self.health_check()
        metrics_snap = self.metrics_snapshot()
        metrics_dict = None
        if metrics_snap is not None:
            metrics_dict = {
                "acquire": {
                    "attempts": metrics_snap.acquire.attempts,
                    "successes": metrics_snap.acquire.successes,
                    "denied": metrics_snap.acquire.denied,
                    "failures": metrics_snap.acquire.failures,
                    "exceptions": metrics_snap.acquire.exceptions,
                },
                "renew": {
                    "attempts": metrics_snap.renew.attempts,
                    "successes": metrics_snap.renew.successes,
                    "failures": metrics_snap.renew.failures,
                    "exceptions": metrics_snap.renew.exceptions,
                },
                "release": {
                    "attempts": metrics_snap.release.attempts,
                    "successes": metrics_snap.release.successes,
                    "failures": metrics_snap.release.failures,
                    "exceptions": metrics_snap.release.exceptions,
                },
            }

        expired_count = None
        sample = []
        try:
            expired = await backend.list_expired_leases()
            expired_count = len(expired)
            if include_expired_sample and expired:
                limit = min(expired_sample_limit, len(expired))
                for lease in expired[:limit]:
                    sample.append({
                        "run_id": lease.run_id,
                        "owner_id": lease.owner_id,
                        "expires_at": lease.expires_at.isoformat() if lease.expires_at else None,
                        "version": lease.version,
                    })
        except Exception:
            pass  # Best-effort diagnostics

        return LeaseDiagnostics(
            backend_type=health.backend_type,
            health=health,
            metrics=metrics_dict,
            expired_leases_count=expired_count,
            sample_expired_leases=sample,
        )


# ---------------------------------------------------------------------------
# Module-level diagnostics helper
# ---------------------------------------------------------------------------


async def get_lease_diagnostics(
    backend: Any,  # WorkflowLeaseBackend
    metrics: Any = None,  # LeaseMetrics | None
    include_expired_sample: bool = False,
    expired_sample_limit: int = 10,
) -> Any:  # LeaseDiagnostics
    """Collect diagnostic information about a lease backend.

    This is a convenience function that creates a ``LeaseCoordinator``
    with the given backend and metrics, then collects diagnostics.

    Args:
        backend: The lease backend to diagnose.
        metrics: Optional ``LeaseMetrics`` instance.
        include_expired_sample: Whether to include a sample of expired
            lease details.
        expired_sample_limit: Maximum number of expired lease samples
            to include.

    Returns:
        ``LeaseDiagnostics`` with health, metrics, and lease info.
    """
    from agent_app.runtime.dag_run_state import LeasePolicy

    coordinator = LeaseCoordinator(
        backend=backend,
        default_policy=LeasePolicy(),
        metrics=metrics,
    )
    return await coordinator.diagnostics(
        include_expired_sample=include_expired_sample,
        expired_sample_limit=expired_sample_limit,
    )
