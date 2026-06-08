"""Tests for Phase 16.3 LeaseCoordinator metrics, health, and diagnostics."""

from __future__ import annotations

import asyncio

import pytest

from agent_app.runtime.dag_run_state import (
    LeaseAcquireResult,
    LeasePolicy,
    WorkerIdentity,
    WorkflowRunLease,
)
from agent_app.runtime.lease_backend import InMemoryWorkflowLeaseBackend
from agent_app.runtime.lease_coordinator import LeaseCoordinator
from agent_app.runtime.lease_health import LeaseHealthCheckResult, LeaseHealthStatus
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


# ===========================================================================
# LeaseCoordinator metrics tests
# ===========================================================================


class TestLeaseCoordinatorMetrics:
    """Tests for LeaseCoordinator with metrics."""

    @pytest.fixture
    def coordinator_with_metrics(self):
        backend = InMemoryWorkflowLeaseBackend()
        metrics = LeaseMetrics()
        return LeaseCoordinator(
            backend,
            default_policy=_make_policy(),
            metrics=metrics,
        )

    @pytest.fixture
    def coordinator_without_metrics(self):
        backend = InMemoryWorkflowLeaseBackend()
        return LeaseCoordinator(
            backend,
            default_policy=_make_policy(),
        )

    def test_metrics_opt_in_works(self, coordinator_with_metrics):
        """When metrics is provided, it is used."""
        assert coordinator_with_metrics._metrics is not None

    def test_metrics_absent_by_default(self, coordinator_without_metrics):
        """Without metrics, _metrics is None."""
        assert coordinator_without_metrics._metrics is None

    def test_metrics_snapshot_returns_none_without_metrics(self, coordinator_without_metrics):
        assert coordinator_without_metrics.metrics_snapshot() is None

    def test_metrics_records_acquire(self, coordinator_with_metrics):
        worker = _make_worker()
        _run(coordinator_with_metrics.acquire("run-1", worker))
        snap = coordinator_with_metrics.metrics_snapshot()
        assert snap.acquire.attempts == 1
        assert snap.acquire.successes == 1

    def test_metrics_records_renew(self, coordinator_with_metrics):
        worker = _make_worker()
        policy = _make_policy()
        _run(coordinator_with_metrics.acquire("run-1", worker, policy))
        _run(coordinator_with_metrics.renew("run-1", worker, policy))
        snap = coordinator_with_metrics.metrics_snapshot()
        assert snap.renew.attempts == 1
        assert snap.renew.successes == 1

    def test_metrics_records_release(self, coordinator_with_metrics):
        worker = _make_worker()
        policy = _make_policy()
        _run(coordinator_with_metrics.acquire("run-1", worker, policy))
        _run(coordinator_with_metrics.release("run-1", worker))
        snap = coordinator_with_metrics.metrics_snapshot()
        assert snap.release.attempts == 1
        assert snap.release.successes == 1

    def test_existing_acquire_release_behavior_unchanged(self, coordinator_with_metrics):
        """Adding metrics doesn't change acquire/release return values."""
        worker = _make_worker()
        policy = _make_policy()
        result = _run(coordinator_with_metrics.acquire("run-1", worker, policy))
        assert result.acquired is True
        released = _run(coordinator_with_metrics.release("run-1", worker))
        assert released.released_at is not None

    def test_backend_wrapped_with_metrics(self, coordinator_with_metrics):
        """When metrics is provided, backend is wrapped."""
        assert hasattr(coordinator_with_metrics._backend, "_backend")

    def test_metrics_backend_delegates_correctly(self, coordinator_with_metrics):
        """Metrics wrapper still delegates to the inner backend."""
        worker = _make_worker()
        policy = _make_policy()
        result = _run(coordinator_with_metrics.acquire("run-1", worker, policy))
        assert result.acquired is True
        assert result.lease.owner_id == "worker-1"


# ===========================================================================
# Health check tests
# ===========================================================================


class TestLeaseCoordinatorHealth:
    """Tests for LeaseCoordinator health checks."""

    @pytest.fixture
    def coordinator(self):
        backend = InMemoryWorkflowLeaseBackend()
        return LeaseCoordinator(backend, default_policy=_make_policy())

    def test_health_check_returns_result(self, coordinator):
        result = _run(coordinator.health_check())
        assert isinstance(result, LeaseHealthCheckResult)
        assert result.status == LeaseHealthStatus.HEALTHY

    def test_health_check_has_backend_type(self, coordinator):
        result = _run(coordinator.health_check())
        assert result.backend_type == "memory"

    def test_health_check_has_checked_at(self, coordinator):
        result = _run(coordinator.health_check())
        assert result.checked_at.tzinfo is not None


# ===========================================================================
# Diagnostics tests
# ===========================================================================


class TestLeaseCoordinatorDiagnostics:
    """Tests for LeaseCoordinator diagnostics."""

    @pytest.fixture
    def coordinator(self):
        backend = InMemoryWorkflowLeaseBackend()
        metrics = LeaseMetrics()
        return LeaseCoordinator(
            backend,
            default_policy=_make_policy(),
            metrics=metrics,
        )

    def test_diagnostics_includes_backend_type(self, coordinator):
        diag = _run(coordinator.diagnostics())
        assert diag.backend_type == "memory"

    def test_diagnostics_includes_health(self, coordinator):
        diag = _run(coordinator.diagnostics())
        assert diag.health is not None
        assert diag.health.status == LeaseHealthStatus.HEALTHY

    def test_diagnostics_includes_metrics_when_provided(self, coordinator):
        diag = _run(coordinator.diagnostics())
        assert diag.metrics is not None
        assert "acquire" in diag.metrics

    def test_diagnostics_no_metrics_when_not_provided(self):
        backend = InMemoryWorkflowLeaseBackend()
        coordinator = LeaseCoordinator(backend, default_policy=_make_policy())
        diag = _run(coordinator.diagnostics())
        assert diag.metrics is None

    def test_diagnostics_can_include_expired_sample(self):
        backend = InMemoryWorkflowLeaseBackend()
        coordinator = LeaseCoordinator(backend, default_policy=_make_policy())
        diag = _run(coordinator.diagnostics(
            include_expired_sample=True,
            expired_sample_limit=5,
        ))
        # No expired leases in this test, but sample should be empty list
        assert diag.sample_expired_leases == []

    def test_diagnostics_sample_is_limited(self):
        """Sample limit is respected."""
        backend = InMemoryWorkflowLeaseBackend()
        coordinator = LeaseCoordinator(backend, default_policy=_make_policy())
        # Request sample with limit 0
        diag = _run(coordinator.diagnostics(
            include_expired_sample=True,
            expired_sample_limit=0,
        ))
        assert len(diag.sample_expired_leases) == 0

    def test_diagnostics_failure_handled_gracefully(self):
        """Diagnostics failure doesn't raise."""

        class BrokenBackend:
            async def acquire_run_lease(self, *a, **kw):
                raise RuntimeError("fail")

            async def renew_run_lease(self, *a, **kw):
                raise RuntimeError("fail")

            async def release_run_lease(self, *a, **kw):
                raise RuntimeError("fail")

            async def get_run_lease(self, run_id):
                raise RuntimeError("fail")

            async def list_expired_leases(self, before=None):
                raise RuntimeError("fail")

        coordinator = LeaseCoordinator(BrokenBackend(), default_policy=_make_policy())
        diag = _run(coordinator.diagnostics())
        # Health check should detect the broken backend
        assert diag.health.status == LeaseHealthStatus.UNHEALTHY
