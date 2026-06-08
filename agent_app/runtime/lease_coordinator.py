"""Lease coordinator — unified entry point for lease backend operations.

Phase 16.2: Provides a thin coordination layer over ``WorkflowLeaseBackend``
that applies a default ``LeasePolicy`` when none is provided by the caller.
This gives DagExecutor and LeaseRenewer a stable, policy-aware interface
without duplicating default-policy logic at each call site.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_app.runtime.dag_run_state import LeasePolicy

if TYPE_CHECKING:
    from agent_app.runtime.lease_backend import WorkflowLeaseBackend
    from agent_app.runtime.dag_run_state import (
        LeaseAcquireResult,
        WorkerIdentity,
        WorkflowRunLease,
    )

logger = logging.getLogger(__name__)


class LeaseCoordinator:
    """Unified coordinator for lease backend operations.

    Wraps a ``WorkflowLeaseBackend`` and applies a default ``LeasePolicy``
    to every operation that accepts a policy argument.  Callers can
    override the default on a per-call basis.

    Args:
        backend: The lease backend to coordinate.
        default_policy: Default policy applied when callers don't supply one.

    Usage::

        coordinator = LeaseCoordinator(backend, default_policy=LeasePolicy())
        result = await coordinator.acquire(run_id, worker)
        lease = await coordinator.renew(run_id, worker)
        released = await coordinator.release(run_id, worker)
    """

    def __init__(
        self,
        backend: WorkflowLeaseBackend,
        default_policy: LeasePolicy | None = None,
    ) -> None:
        self._backend = backend
        self._default_policy = default_policy or LeasePolicy()

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
