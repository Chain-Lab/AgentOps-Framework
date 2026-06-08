"""Tests for Phase 16.2 LeaseCoordinator."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_app.runtime.dag_run_state import (
    LeaseAcquireResult,
    LeasePolicy,
    WorkerIdentity,
    WorkflowRunLease,
)
from agent_app.runtime.lease_backend import InMemoryWorkflowLeaseBackend
from agent_app.runtime.lease_coordinator import LeaseCoordinator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker(worker_id: str = "worker-1") -> WorkerIdentity:
    return WorkerIdentity(worker_id=worker_id)


def _make_policy(
    ttl_seconds: int = 300,
    allow_steal_expired: bool = True,
    renew_before_seconds: int = 60,
) -> LeasePolicy:
    return LeasePolicy(
        ttl_seconds=ttl_seconds,
        allow_steal_expired=allow_steal_expired,
        renew_before_seconds=renew_before_seconds,
    )


def _run(coro):
    """Run async coroutine synchronously."""
    return asyncio.run(coro)


# ===========================================================================
# LeaseCoordinator tests
# ===========================================================================


class TestLeaseCoordinator:
    """Tests for LeaseCoordinator."""

    @pytest.fixture
    def backend(self):
        return InMemoryWorkflowLeaseBackend()

    @pytest.fixture
    def coordinator(self, backend):
        return LeaseCoordinator(backend, default_policy=_make_policy(ttl_seconds=300))

    def test_applies_default_policy(self, coordinator):
        """Coordinator applies default policy when none provided."""
        worker = _make_worker()
        result = _run(coordinator.acquire("run-1", worker))
        assert result.acquired is True
        assert result.lease is not None

    def test_explicit_policy_overrides_default(self, backend):
        """Explicit policy takes precedence over default."""
        coordinator = LeaseCoordinator(
            backend,
            default_policy=_make_policy(ttl_seconds=300),
        )
        worker = _make_worker()
        short_policy = _make_policy(ttl_seconds=10)
        result = _run(coordinator.acquire("run-1", worker, short_policy))
        assert result.acquired is True
        # With 10s TTL, expiry should be ~10s from now
        now = datetime.now(timezone.utc)
        delta = result.lease.expires_at - now
        assert 5 < delta.total_seconds() <= 15

    def test_acquire_returns_denied_result_cleanly(self, coordinator):
        """Denied acquire returns a clean LeaseAcquireResult."""
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")
        policy = _make_policy(ttl_seconds=300)
        _run(coordinator.acquire("run-1", w1, policy))
        result = _run(coordinator.acquire("run-1", w2, policy))
        assert result.acquired is False
        assert result.reason is not None
        assert result.current_owner_id == "w1"

    def test_release_passes_through_backend(self, coordinator):
        """Release delegates to the backend."""
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(coordinator.acquire("run-1", worker, policy))
        released = _run(coordinator.release("run-1", worker))
        assert released.released_at is not None

    def test_get_passes_through_backend(self, coordinator):
        """Get delegates to the backend."""
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(coordinator.acquire("run-1", worker, policy))
        lease = _run(coordinator.get("run-1"))
        assert lease is not None
        assert lease.owner_id == "worker-1"

    def test_list_expired_passes_through(self, coordinator):
        """list_expired delegates to the backend."""
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=1)
        _run(coordinator.acquire("run-1", worker, policy))
        import time
        time.sleep(1.1)
        expired = _run(coordinator.list_expired())
        assert len(expired) == 1

    def test_renew_passes_through_backend(self, coordinator):
        """Renew delegates to the backend."""
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(coordinator.acquire("run-1", worker, policy))
        renewed = _run(coordinator.renew("run-1", worker, policy))
        assert renewed.version == 2

    def test_none_policy_uses_default(self, coordinator):
        """When policy is None, default policy is used."""
        worker = _make_worker()
        result = _run(coordinator.acquire("run-1", worker, policy=None))
        assert result.acquired is True

    def test_renew_raises_on_non_owner(self, coordinator):
        """Renew raises KeyError for non-owner."""
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")
        policy = _make_policy(ttl_seconds=300)
        _run(coordinator.acquire("run-1", w1, policy))
        with pytest.raises(KeyError):
            _run(coordinator.renew("run-1", w2, policy))

    def test_release_raises_on_non_owner(self, coordinator):
        """Release raises KeyError for non-owner."""
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")
        policy = _make_policy(ttl_seconds=300)
        _run(coordinator.acquire("run-1", w1, policy))
        with pytest.raises(KeyError):
            _run(coordinator.release("run-1", w2))
