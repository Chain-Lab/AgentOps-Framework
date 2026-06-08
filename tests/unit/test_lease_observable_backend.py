"""Tests for Phase 16.3 MetricsWorkflowLeaseBackend."""

from __future__ import annotations

import asyncio

import pytest

from agent_app.runtime.dag_run_state import (
    LeaseAcquireResult,
    LeasePolicy,
    WorkerIdentity,
    WorkflowRunLease,
)
from agent_app.runtime.lease_backend import (
    InMemoryWorkflowLeaseBackend,
    MetricsWorkflowLeaseBackend,
)
from agent_app.runtime.lease_metrics import LeaseMetrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker(worker_id: str = "worker-1") -> WorkerIdentity:
    return WorkerIdentity(worker_id=worker_id)


def _make_policy(ttl_seconds: int = 300) -> LeasePolicy:
    return LeasePolicy(ttl_seconds=ttl_seconds)


def _run(coro):
    return asyncio.run(coro)


class DenyingBackend:
    """Backend that always denies acquire."""

    async def acquire_run_lease(self, run_id, worker, policy=None):
        return LeaseAcquireResult(
            acquired=False, run_id=run_id,
            owner_id=worker.worker_id,
            reason="Already leased by someone else",
        )

    async def renew_run_lease(self, *a, **kw):
        raise KeyError("No lease")

    async def release_run_lease(self, *a, **kw):
        raise KeyError("No lease")

    async def get_run_lease(self, run_id):
        return None

    async def list_expired_leases(self, before=None):
        return []


class FailingBackend:
    """Backend that always raises on renew."""

    def __init__(self):
        self.leases: dict[str, WorkflowRunLease] = {}

    async def acquire_run_lease(self, run_id, worker, policy=None):
        now = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
        policy = policy or _make_policy()
        lease = WorkflowRunLease(
            run_id=run_id,
            owner_id=worker.worker_id,
            acquired_at=now,
            expires_at=now + __import__("datetime").timedelta(seconds=policy.ttl_seconds),
        )
        self.leases[run_id] = lease
        return LeaseAcquireResult(
            acquired=True, run_id=run_id,
            owner_id=worker.worker_id, lease=lease,
        )

    async def renew_run_lease(self, run_id, worker, policy=None):
        raise RuntimeError("Simulated renew failure")

    async def release_run_lease(self, run_id, worker):
        raise RuntimeError("Simulated release failure")

    async def get_run_lease(self, run_id):
        return None

    async def list_expired_leases(self, before=None):
        return []


# ===========================================================================
# MetricsWorkflowLeaseBackend tests
# ===========================================================================


class TestMetricsWorkflowLeaseBackend:
    """Tests for the metrics-collecting lease backend wrapper."""

    def test_records_acquire_success(self):
        inner = InMemoryWorkflowLeaseBackend()
        metrics = LeaseMetrics()
        backend = MetricsWorkflowLeaseBackend(inner, metrics)

        worker = _make_worker()
        policy = _make_policy()
        result = _run(backend.acquire_run_lease("run-1", worker, policy))

        assert result.acquired is True
        snap = metrics.snapshot()
        assert snap.acquire.attempts == 1
        assert snap.acquire.successes == 1

    def test_records_acquire_denied(self):
        inner = DenyingBackend()
        metrics = LeaseMetrics()
        backend = MetricsWorkflowLeaseBackend(inner, metrics)

        worker = _make_worker()
        policy = _make_policy()
        result = _run(backend.acquire_run_lease("run-1", worker, policy))

        assert result.acquired is False
        snap = metrics.snapshot()
        assert snap.acquire.attempts == 1
        # Denied acquires are recorded as failures (not exceptions)
        assert snap.acquire.failures == 1

    def test_records_renew_success(self):
        inner = InMemoryWorkflowLeaseBackend()
        metrics = LeaseMetrics()
        backend = MetricsWorkflowLeaseBackend(inner, metrics)

        worker = _make_worker()
        policy = _make_policy()
        _run(backend.acquire_run_lease("run-1", worker, policy))
        _run(backend.renew_run_lease("run-1", worker, policy))

        snap = metrics.snapshot()
        assert snap.renew.attempts == 1
        assert snap.renew.successes == 1

    def test_records_renew_failure(self):
        inner = FailingBackend()
        metrics = LeaseMetrics()
        backend = MetricsWorkflowLeaseBackend(inner, metrics)

        worker = _make_worker()
        policy = _make_policy()
        _run(backend.acquire_run_lease("run-1", worker, policy))
        with pytest.raises(RuntimeError, match="renew"):
            _run(backend.renew_run_lease("run-1", worker, policy))

        snap = metrics.snapshot()
        # RuntimeError is caught as exception (not KeyError)
        assert snap.renew.exceptions == 1

    def test_records_release_success(self):
        inner = InMemoryWorkflowLeaseBackend()
        metrics = LeaseMetrics()
        backend = MetricsWorkflowLeaseBackend(inner, metrics)

        worker = _make_worker()
        policy = _make_policy()
        _run(backend.acquire_run_lease("run-1", worker, policy))
        _run(backend.release_run_lease("run-1", worker))

        snap = metrics.snapshot()
        assert snap.release.attempts == 1
        assert snap.release.successes == 1

    def test_records_release_failure(self):
        inner = FailingBackend()
        metrics = LeaseMetrics()
        backend = MetricsWorkflowLeaseBackend(inner, metrics)

        worker = _make_worker()
        policy = _make_policy()
        _run(backend.acquire_run_lease("run-1", worker, policy))
        with pytest.raises(RuntimeError, match="release"):
            _run(backend.release_run_lease("run-1", worker))

        snap = metrics.snapshot()
        # RuntimeError is caught as exception (not KeyError)
        assert snap.release.exceptions == 1

    def test_records_exception_and_reraises(self):
        """Exceptions are recorded and re-raised."""
        inner = FailingBackend()
        metrics = LeaseMetrics()
        backend = MetricsWorkflowLeaseBackend(inner, metrics)

        worker = _make_worker()
        policy = _make_policy()
        _run(backend.acquire_run_lease("run-1", worker, policy))
        with pytest.raises(RuntimeError):
            _run(backend.renew_run_lease("run-1", worker, policy))

        snap = metrics.snapshot()
        assert snap.renew.exceptions == 1

    def test_does_not_change_backend_result(self):
        """Wrapper preserves the underlying backend's return values."""
        inner = InMemoryWorkflowLeaseBackend()
        metrics = LeaseMetrics()
        backend = MetricsWorkflowLeaseBackend(inner, metrics)

        worker = _make_worker()
        policy = _make_policy()
        result = _run(backend.acquire_run_lease("run-1", worker, policy))
        assert result.acquired is True
        assert result.lease.owner_id == "worker-1"
        assert result.lease.version == 1

    def test_no_metrics_no_recording(self):
        """Without metrics, no recording happens."""
        inner = InMemoryWorkflowLeaseBackend()
        backend = MetricsWorkflowLeaseBackend(inner, metrics=None)

        worker = _make_worker()
        policy = _make_policy()
        _run(backend.acquire_run_lease("run-1", worker, policy))
        # Should not raise — metrics is None, so no recording
        assert True  # If we get here, it worked

    def test_get_records_success_and_failure(self):
        """get_run_lease records success when lease found, failure when None."""
        inner = InMemoryWorkflowLeaseBackend()
        metrics = LeaseMetrics()
        backend = MetricsWorkflowLeaseBackend(inner, metrics)

        # No lease — records failure
        _run(backend.get_run_lease("nonexistent"))
        snap = metrics.snapshot()
        assert snap.get.attempts == 1
        assert snap.get.failures == 1

        # Acquire lease, then get — records success
        worker = _make_worker()
        policy = _make_policy()
        _run(backend.acquire_run_lease("run-1", worker, policy))
        _run(backend.get_run_lease("run-1"))
        snap = metrics.snapshot()
        assert snap.get.attempts == 2
        assert snap.get.successes == 1

    def test_list_expired_records_success(self):
        """list_expired_leases records success."""
        inner = InMemoryWorkflowLeaseBackend()
        metrics = LeaseMetrics()
        backend = MetricsWorkflowLeaseBackend(inner, metrics)

        _run(backend.list_expired_leases())
        snap = metrics.snapshot()
        assert snap.list_expired.attempts == 1
        assert snap.list_expired.successes == 1
