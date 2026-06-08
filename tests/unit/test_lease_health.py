"""Tests for Phase 16.3 lease health checks."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_app.runtime.dag_run_state import (
    LeaseAcquireResult,
    LeasePolicy,
    WorkerIdentity,
    WorkflowRunLease,
)
from agent_app.runtime.lease_backend import (
    InMemoryWorkflowLeaseBackend,
    SQLiteWorkflowLeaseBackend,
    StateStoreLeaseBackend,
)
from agent_app.runtime.lease_health import (
    LeaseBackendHealthChecker,
    LeaseHealthCheckResult,
    LeaseHealthStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker(worker_id: str = "worker-1") -> WorkerIdentity:
    return WorkerIdentity(worker_id=worker_id)


def _run(coro):
    return asyncio.run(coro)


class MockStateStore:
    """Minimal mock state store for StateStoreLeaseBackend testing."""

    async def acquire_run_lease(self, *a, **kw):
        raise NotImplementedError

    async def renew_run_lease(self, *a, **kw):
        raise NotImplementedError

    async def release_run_lease(self, *a, **kw):
        raise NotImplementedError

    async def get_run_lease(self, *a, **kw):
        return None

    async def list_expired_leases(self, *a, **kw):
        return []


# ===========================================================================
# LeaseBackendHealthChecker tests
# ===========================================================================


class TestLeaseBackendHealthChecker:
    """Tests for lease backend health checking."""

    def test_inmemory_backend_is_healthy(self):
        backend = InMemoryWorkflowLeaseBackend()
        checker = LeaseBackendHealthChecker(backend)
        result = _run(checker.check())
        assert result.status == LeaseHealthStatus.HEALTHY
        assert result.backend_type == "memory"
        assert result.error is None

    def test_sqlite_backend_is_healthy(self, tmp_path):
        db_path = str(tmp_path / "health_test.db")
        backend = SQLiteWorkflowLeaseBackend(db_path)
        checker = LeaseBackendHealthChecker(backend)
        result = _run(checker.check())
        assert result.status == LeaseHealthStatus.HEALTHY
        assert result.backend_type == "sqlite"
        assert result.error is None
        assert "active_leases" in result.details

    def test_sqlite_backend_with_active_leases(self, tmp_path):
        db_path = str(tmp_path / "health_test.db")
        backend = SQLiteWorkflowLeaseBackend(db_path)
        worker = _make_worker()
        policy = LeasePolicy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", worker, policy))

        checker = LeaseBackendHealthChecker(backend)
        result = _run(checker.check())
        assert result.details.get("active_leases") == 1

    def test_state_store_backend_is_healthy(self):
        store = MockStateStore()
        backend = StateStoreLeaseBackend(store)
        checker = LeaseBackendHealthChecker(backend)
        result = _run(checker.check())
        assert result.status == LeaseHealthStatus.HEALTHY
        assert result.backend_type == "state_store"

    def test_health_result_has_timezone_aware_checked_at(self):
        backend = InMemoryWorkflowLeaseBackend()
        checker = LeaseBackendHealthChecker(backend)
        result = _run(checker.check())
        assert result.checked_at.tzinfo is not None

    def test_failing_backend_is_unhealthy(self):
        """A backend that raises on health check should return unhealthy."""

        class BrokenBackend:
            async def acquire_run_lease(self, *a, **kw):
                raise RuntimeError("DB connection failed")

            async def renew_run_lease(self, *a, **kw):
                raise RuntimeError("DB connection failed")

            async def release_run_lease(self, *a, **kw):
                raise RuntimeError("DB connection failed")

            async def get_run_lease(self, run_id):
                raise RuntimeError("DB connection failed")

            async def list_expired_leases(self, before=None):
                raise RuntimeError("DB connection failed")

        checker = LeaseBackendHealthChecker(BrokenBackend())
        result = _run(checker.check())
        assert result.status == LeaseHealthStatus.UNHEALTHY
        assert result.error is not None
        assert "DB connection failed" in result.error

    def test_health_result_has_backend_type_for_broken(self):
        """Even broken backends report their type."""

        class BrokenBackend:
            async def acquire_run_lease(self, *a, **kw):
                raise RuntimeError("fail")

            async def renew_run_lease(self, *a, **kw):
                raise RuntimeError("fail")

            async def release_run_lease(self, *a, **kw):
                raise RuntimeError("fail")

            async def get_run_lease(self, *a, **kw):
                raise RuntimeError("fail")

            async def list_expired_leases(self, *a, **kw):
                raise RuntimeError("fail")

        checker = LeaseBackendHealthChecker(BrokenBackend())
        result = _run(checker.check())
        assert result.backend_type == "brokenbackend"
