"""Background lease renewal for DAG workflow runs.

Phase 15.2: Provides best-effort in-process lease renewal during workflow
execution.  A :class:`LeaseRenewer` periodically calls
``renew_run_lease`` on the state store while a workflow is running,
extending the lease so it doesn't expire mid-execution.

This is NOT a distributed worker backend, NOT Celery, NOT Temporal, and
does NOT provide exactly-once execution.  It is a simple asyncio
background task that keeps the lease alive while the current process
is running the DAG.

Lease renewal semantics:
  * Only the current lease owner can renew.
  * Expired leases cannot be renewed.
  * Released leases cannot be renewed.
  * If renewal fails, ``lease_lost`` is set to ``True`` and the
    background task stops itself.
  * The caller (DagExecutor) checks ``lease_lost`` after execution and
    raises a stable error if the lease was lost.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_app.runtime.dag_run_state import WorkflowRunLease

# Re-export LeaseLostError from the canonical location (dag_run_state).
from agent_app.runtime.dag_run_state import LeaseLostError  # noqa: E402

logger = logging.getLogger(__name__)

# Default renewal interval when not explicitly configured.
_DEFAULT_RENEW_RATIO = 3  # renew every ttl / 3 seconds


class LeaseRenewer:
    """Best-effort background lease renewal for a workflow run.

    Runs an asyncio background task that periodically renews the lease
    held by the current worker.  The task stops itself on the first
    renewal failure, setting ``lease_lost = True``.

    Args:
        state_store: (Legacy) The workflow state store (must implement
            ``renew_run_lease``).  If ``lease_backend`` is not provided,
            this is used as the lease backend via ``StateStoreLeaseBackend``.
        run_id: The workflow run to renew the lease for.
        worker_id: The worker ID that holds the lease.
        ttl_seconds: Lease TTL in seconds (used for default interval).
        interval_seconds: Renewal interval in seconds.  Defaults to
            ``ttl_seconds / 3`` if not provided.
        lease_backend: (Phase 16.2) Optional ``WorkflowLeaseBackend``
            instance.  Takes precedence over ``state_store`` when provided.

    Usage::

        renewer = LeaseRenewer(store, "run-1", "worker-1", ttl_seconds=30)
        async with renewer:
            # ... execute DAG ...
            pass  # lease is renewed in background
        # lease_lost is True if renewal failed at any point
    """

    def __init__(
        self,
        state_store: object = None,
        run_id: str = "",
        worker_id: str = "",
        ttl_seconds: int = 300,
        interval_seconds: float | None = None,
        lease_backend: object = None,
    ) -> None:
        # Phase 16.2: Support lease_backend; fall back to state_store wrapping
        if lease_backend is not None:
            self._lease_backend = lease_backend
        elif state_store is not None:
            from agent_app.runtime.lease_backend import StateStoreLeaseBackend
            self._lease_backend = StateStoreLeaseBackend(state_store)
        else:
            raise ValueError(
                "LeaseRenewer requires either 'state_store' or 'lease_backend'."
            )
        # Keep state_store reference for terminal-state check (get_run)
        self._state_store = state_store
        self._run_id = run_id
        self._worker_id = worker_id
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = (
            interval_seconds if interval_seconds is not None
            else ttl_seconds / _DEFAULT_RENEW_RATIO
        )
        self._task: asyncio.Task | None = None
        self.lease_lost: bool = False
        self._last_error: Exception | None = None
        self._stopped = asyncio.Event()

    # -- Lifecycle --

    async def start(self) -> None:
        """Start the background renewal task.

        Idempotent: if already running, does nothing.
        """
        if self._task is not None and not self._task.done():
            return  # Already running
        self._stopped.clear()
        self.lease_lost = False
        self._last_error = None
        self._task = asyncio.create_task(self._renew_loop())

    async def stop(self) -> None:
        """Stop the background renewal task.

        Idempotent: safe to call multiple times.  Waits for the task
        to finish.
        """
        self._stopped.set()
        task = self._task
        if task is None or task.done():
            self._task = None
            return
        self._task = None
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(
                "LeaseRenewer task for run '%s' did not stop within 5s; cancelling.",
                self._run_id,
            )
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass

    # -- Async context manager --

    async def __aenter__(self) -> LeaseRenewer:
        """Start renewal on context enter."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Stop renewal on context exit (regardless of exception)."""
        await self.stop()

    # -- Background loop --

    async def _renew_loop(self) -> None:
        """Background task: periodically renew the lease.

        Stops on the first failure, setting ``lease_lost = True``.
        Also stops if the workflow run is no longer in a renew-able
        state (completed, failed, cancelled).
        """
        from agent_app.runtime.dag_run_state import (
            LeasePolicy,
            WorkflowRunStatus,
        )

        policy = LeasePolicy()
        worker = _make_worker(self._worker_id)

        # Wait for the first interval before the first renewal, so the
        # lease has time to be used before we extend it.
        try:
            await asyncio.wait_for(
                self._stopped.wait(),
                timeout=self._interval_seconds,
            )
            return  # stopped before first renewal
        except asyncio.TimeoutError:
            pass  # time to renew

        while not self._stopped.is_set():
            # Check if the run is still in a renewable state
            try:
                run = await self._state_store.get_run(self._run_id)
                if run.status in (
                    WorkflowRunStatus.COMPLETED.value,
                    WorkflowRunStatus.FAILED.value,
                    WorkflowRunStatus.PARTIAL.value,
                ):
                    # Run already finished — no need to renew
                    logger.debug(
                        "LeaseRenewer: run '%s' is in terminal state '%s', stopping.",
                        self._run_id, run.status,
                    )
                    return
            except (KeyError, Exception):
                # Run not found or store error — treat as lease lost
                pass

            # Attempt renewal
            try:
                renewed = await self._lease_backend.renew_run_lease(
                    self._run_id, worker, policy
                )
                logger.debug(
                    "LeaseRenewer: renewed lease for run '%s', "
                    "new expiry=%s (version=%d)",
                    self._run_id,
                    renewed.expires_at.isoformat(),
                    renewed.version,
                )
            except Exception as exc:
                self.lease_lost = True
                self._last_error = exc
                logger.warning(
                    "LeaseRenewer: renewal failed for run '%s' (worker '%s'): %s",
                    self._run_id, self._worker_id, exc,
                )
                return  # Stop on first failure

            # Wait for next interval
            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self._interval_seconds,
                )
                return  # stopped during wait
            except asyncio.TimeoutError:
                pass  # time to renew again


# -- Helpers --


def _make_worker(worker_id: str) -> object:
    """Create a minimal WorkerIdentity for lease renewal calls."""
    from agent_app.runtime.dag_run_state import WorkerIdentity

    return WorkerIdentity(worker_id=worker_id)
