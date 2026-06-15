"""Tests for Phase 16.2 LeaseRenewer with lease_backend support."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agent_app.runtime.dag_run_state import (
    LeasePolicy,
    WorkerIdentity,
    WorkflowRunLease,
)
from agent_app.runtime.lease_backend import InMemoryWorkflowLeaseBackend
from agent_app.runtime.lease_renewer import LeaseRenewer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockStateStore:
    """Minimal mock state store for backward-compat testing."""

    def __init__(self):
        self.leases: dict[str, WorkflowRunLease] = {}

    async def get_run(self, run_id):
        class _Run:
            status = "completed"
        return _Run()

    async def renew_run_lease(self, run_id, worker, policy=None):
        policy = policy or LeasePolicy()
        existing = self.leases.get(run_id)
        if existing is None:
            raise KeyError("No lease")
        renewed = WorkflowRunLease(
            run_id=run_id,
            owner_id=existing.owner_id,
            acquired_at=existing.acquired_at,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=policy.ttl_seconds),
            renewed_at=datetime.now(timezone.utc),
            version=existing.version + 1,
        )
        self.leases[run_id] = renewed
        return renewed


def _make_worker(worker_id: str = "worker-1") -> WorkerIdentity:
    return WorkerIdentity(worker_id=worker_id)


def _run(coro):
    return asyncio.run(coro)


class FakeInMemoryBackend(InMemoryWorkflowLeaseBackend):
    """InMemory backend that pretends the run is always in a renewable state."""

    async def renew_run_lease(self, run_id, worker, policy=None):
        policy = policy or LeasePolicy()
        # Force-insert a lease if not present
        if run_id not in self._leases:
            now = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            )
            self._leases[run_id] = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=now,
                expires_at=now + __import__("datetime").timedelta(seconds=policy.ttl_seconds),
                version=1,
            )
        return await super().renew_run_lease(run_id, worker, policy)


# ===========================================================================
# LeaseRenewer with explicit lease_backend
# ===========================================================================


class TestLeaseRenewerWithBackend:
    """Tests for LeaseRenewer using explicit lease_backend."""

    def test_works_with_explicit_lease_backend(self):
        """LeaseRenewer accepts lease_backend parameter."""
        backend = FakeInMemoryBackend()
        renewer = LeaseRenewer(
            lease_backend=backend,
            run_id="run-1",
            worker_id="worker-1",
            ttl_seconds=30,
        )
        assert renewer._lease_backend is backend

    def test_still_works_with_legacy_state_store(self):
        """LeaseRenewer still accepts state_store for backward compat."""
        store = MockStateStore()
        renewer = LeaseRenewer(
            state_store=store,
            run_id="run-1",
            worker_id="worker-1",
            ttl_seconds=30,
        )
        assert renewer._state_store is store

    def test_requires_either_state_store_or_backend(self):
        """LeaseRenewer raises if neither state_store nor lease_backend is given."""
        with pytest.raises(ValueError, match="requires either"):
            LeaseRenewer(run_id="run-1", worker_id="w1")

    def test_lease_lost_on_renewal_failure(self):
        """lease_lost is set to True when renewal fails."""
        backend = InMemoryWorkflowLeaseBackend()
        # No lease acquired — renewal will fail
        renewer = LeaseRenewer(
            lease_backend=backend,
            run_id="run-1",
            worker_id="worker-1",
            ttl_seconds=30,
            interval_seconds=0.05,
        )

        async def _run_test():
            await renewer.start()
            # Wait for the renew loop to fail
            for _ in range(100):
                if renewer.lease_lost:
                    return
                await asyncio.sleep(0.05)
            # If we get here, force-check the task
            if renewer._task is not None:
                await renewer._task

        _run(_run_test())
        assert renewer.lease_lost is True

    def test_stop_idempotent(self):
        """Stop is safe to call multiple times."""
        backend = FakeInMemoryBackend()
        renewer = LeaseRenewer(
            lease_backend=backend,
            run_id="run-1",
            worker_id="worker-1",
            ttl_seconds=300,
        )
        _run(renewer.start())
        _run(renewer.stop())
        _run(renewer.stop())  # Should not raise

    def test_context_manager(self):
        """Async context manager starts and stops renewal."""
        backend = FakeInMemoryBackend()
        renewer = LeaseRenewer(
            lease_backend=backend,
            run_id="run-1",
            worker_id="worker-1",
            ttl_seconds=300,
            interval_seconds=60,  # Long interval — won't renew during test
        )
        async def _use():
            async with renewer:
                pass
            assert renewer._task is None
        _run(_use())

    def test_lease_backend_takes_precedence(self):
        """When both state_store and lease_backend are given, lease_backend wins."""
        backend = FakeInMemoryBackend()
        store = object()
        renewer = LeaseRenewer(
            state_store=store,
            lease_backend=backend,
            run_id="run-1",
            worker_id="worker-1",
            ttl_seconds=30,
        )
        assert renewer._lease_backend is backend
