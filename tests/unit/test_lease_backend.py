"""Tests for Phase 16.2 lease backend abstraction.

Covers:
- WorkflowLeaseBackend Protocol (structural typing)
- StateStoreLeaseBackend adapter (delegation)
- InMemoryWorkflowLeaseBackend (standalone in-memory)
- SQLiteWorkflowLeaseBackend (standalone SQLite)
- create_lease_backend() factory
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta, timezone
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
    WorkflowLeaseBackend,
    create_lease_backend,
)
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _run(coro):
    """Run async coroutine synchronously using asyncio.run."""
    return asyncio.run(coro)


def _make_expired_lease(run_id: str, owner_id: str = "worker-1") -> WorkflowRunLease:
    """Create an already-expired lease for testing steal behavior."""
    past = _utcnow() - timedelta(seconds=10)
    return WorkflowRunLease(
        run_id=run_id,
        owner_id=owner_id,
        acquired_at=past - timedelta(seconds=300),
        expires_at=past,
        version=1,
    )


# ===========================================================================
# StateStoreLeaseBackend tests
# ===========================================================================


class MockStateStore:
    """Minimal mock state store with lease methods for adapter testing."""

    def __init__(self):
        self.leases: dict[str, WorkflowRunLease] = {}

    async def acquire_run_lease(self, run_id, worker, policy=None):
        policy = policy or LeasePolicy()
        now = _utcnow()
        existing = self.leases.get(run_id)

        if existing is None or existing.released_at is not None:
            lease = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=now,
                expires_at=now + timedelta(seconds=policy.ttl_seconds),
            )
            self.leases[run_id] = lease
            return LeaseAcquireResult(
                acquired=True, run_id=run_id,
                owner_id=worker.worker_id, lease=lease,
            )

        if now >= existing.expires_at:
            if policy.allow_steal_expired:
                lease = WorkflowRunLease(
                    run_id=run_id,
                    owner_id=worker.worker_id,
                    acquired_at=now,
                    expires_at=now + timedelta(seconds=policy.ttl_seconds),
                    version=existing.version + 1,
                )
                self.leases[run_id] = lease
                return LeaseAcquireResult(
                    acquired=True, run_id=run_id,
                    owner_id=worker.worker_id, lease=lease,
                )
            return LeaseAcquireResult(
                acquired=False, run_id=run_id,
                owner_id=worker.worker_id,
                reason="Expired, steal not allowed",
                current_owner_id=existing.owner_id,
                expires_at=existing.expires_at,
            )

        if existing.owner_id == worker.worker_id:
            refreshed = WorkflowRunLease(
                run_id=run_id,
                owner_id=worker.worker_id,
                acquired_at=existing.acquired_at,
                expires_at=now + timedelta(seconds=policy.ttl_seconds),
                renewed_at=now,
                version=existing.version + 1,
            )
            self.leases[run_id] = refreshed
            return LeaseAcquireResult(
                acquired=True, run_id=run_id,
                owner_id=worker.worker_id, lease=refreshed,
            )

        return LeaseAcquireResult(
            acquired=False, run_id=run_id,
            owner_id=worker.worker_id,
            reason=f"Leased by {existing.owner_id}",
            current_owner_id=existing.owner_id,
            expires_at=existing.expires_at,
        )

    async def renew_run_lease(self, run_id, worker, policy=None):
        policy = policy or LeasePolicy()
        existing = self.leases.get(run_id)
        if existing is None:
            raise KeyError(f"No active lease for '{run_id}'.")
        if existing.owner_id != worker.worker_id:
            raise KeyError("Wrong owner.")
        if existing.released_at is not None:
            raise KeyError("Already released.")
        now = _utcnow()
        if now >= existing.expires_at:
            raise KeyError("Expired.")
        renewed = WorkflowRunLease(
            run_id=run_id,
            owner_id=worker.worker_id,
            acquired_at=existing.acquired_at,
            expires_at=now + timedelta(seconds=policy.ttl_seconds),
            renewed_at=now,
            version=existing.version + 1,
        )
        self.leases[run_id] = renewed
        return renewed

    async def release_run_lease(self, run_id, worker):
        existing = self.leases.get(run_id)
        if existing is None:
            raise KeyError("No lease.")
        if existing.owner_id != worker.worker_id:
            raise KeyError("Wrong owner.")
        if existing.released_at is not None:
            raise KeyError("Already released.")
        now = _utcnow()
        released = WorkflowRunLease(
            run_id=run_id,
            owner_id=existing.owner_id,
            acquired_at=existing.acquired_at,
            expires_at=existing.expires_at,
            renewed_at=existing.renewed_at,
            released_at=now,
            version=existing.version,
        )
        self.leases[run_id] = released
        return released

    async def get_run_lease(self, run_id):
        lease = self.leases.get(run_id)
        if lease is None or lease.released_at is not None:
            return None
        return lease

    async def list_expired_leases(self, before=None):
        cutoff = before or _utcnow()
        return [
            l for l in self.leases.values()
            if l.released_at is None and l.expires_at <= cutoff
        ]


@pytest.fixture
def mock_state_store():
    return MockStateStore()


class TestStateStoreLeaseBackend:
    """Tests for the StateStoreLeaseBackend adapter."""

    def test_adapter_delegates_acquire(self, mock_state_store):
        backend = StateStoreLeaseBackend(mock_state_store)
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        result = _run(backend.acquire_run_lease("run-1", worker, policy))
        assert result.acquired is True
        assert result.owner_id == "worker-1"

    def test_adapter_delegates_renew(self, mock_state_store):
        backend = StateStoreLeaseBackend(mock_state_store)
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        renewed = _run(backend.renew_run_lease("run-1", worker, policy))
        assert renewed.owner_id == "worker-1"
        assert renewed.version == 2

    def test_adapter_delegates_release(self, mock_state_store):
        backend = StateStoreLeaseBackend(mock_state_store)
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        released = _run(backend.release_run_lease("run-1", worker))
        assert released.released_at is not None

    def test_adapter_delegates_get(self, mock_state_store):
        backend = StateStoreLeaseBackend(mock_state_store)
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        lease = _run(backend.get_run_lease("run-1"))
        assert lease is not None
        assert lease.owner_id == "worker-1"

    def test_adapter_delegates_list_expired(self, mock_state_store):
        backend = StateStoreLeaseBackend(mock_state_store)
        worker = _make_worker()
        # Use ttl_seconds=1 and wait for lease to expire
        policy = _make_policy(ttl_seconds=1)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        time.sleep(1.1)
        expired = _run(backend.list_expired_leases())
        assert len(expired) == 1

    def test_adapter_preserves_denied_acquire(self, mock_state_store):
        backend = StateStoreLeaseBackend(mock_state_store)
        w1 = _make_worker("worker-1")
        w2 = _make_worker("worker-2")
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", w1, policy))
        result = _run(backend.acquire_run_lease("run-1", w2, policy))
        assert result.acquired is False
        assert result.current_owner_id == "worker-1"

    def test_adapter_preserves_expired_steal(self, mock_state_store):
        backend = StateStoreLeaseBackend(mock_state_store)
        w1 = _make_worker("worker-1")
        w2 = _make_worker("worker-2")
        # Use ttl_seconds=1 and wait for expiry
        policy = _make_policy(ttl_seconds=1, allow_steal_expired=True)
        _run(backend.acquire_run_lease("run-1", w1, policy))
        time.sleep(1.1)
        result = _run(backend.acquire_run_lease("run-1", w2, policy))
        assert result.acquired is True
        assert result.owner_id == "worker-2"


# ===========================================================================
# InMemoryWorkflowLeaseBackend tests
# ===========================================================================


class TestInMemoryLeaseBackend:
    """Tests for standalone InMemoryWorkflowLeaseBackend."""

    @pytest.fixture
    def backend(self):
        return InMemoryWorkflowLeaseBackend()

    def test_acquire_lease(self, backend):
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        result = _run(backend.acquire_run_lease("run-1", worker, policy))
        assert result.acquired is True
        assert result.lease is not None
        assert result.lease.owner_id == "worker-1"

    def test_deny_competing_owner(self, backend):
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", w1, policy))
        result = _run(backend.acquire_run_lease("run-1", w2, policy))
        assert result.acquired is False
        assert result.current_owner_id == "w1"

    def test_renew_by_owner(self, backend):
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        renewed = _run(backend.renew_run_lease("run-1", worker, policy))
        assert renewed.version == 2

    def test_reject_renew_by_non_owner(self, backend):
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", w1, policy))
        with pytest.raises(KeyError):
            _run(backend.renew_run_lease("run-1", w2, policy))

    def test_release_by_owner(self, backend):
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        released = _run(backend.release_run_lease("run-1", worker))
        assert released.released_at is not None

    def test_reject_release_by_non_owner(self, backend):
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", w1, policy))
        with pytest.raises(KeyError):
            _run(backend.release_run_lease("run-1", w2))

    def test_acquire_after_release(self, backend):
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        _run(backend.release_run_lease("run-1", worker))
        result = _run(backend.acquire_run_lease("run-1", worker, policy))
        assert result.acquired is True

    def test_steal_expired_lease(self, backend):
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")
        policy = _make_policy(ttl_seconds=1, allow_steal_expired=True)
        _run(backend.acquire_run_lease("run-1", w1, policy))
        time.sleep(1.1)
        result = _run(backend.acquire_run_lease("run-1", w2, policy))
        assert result.acquired is True
        assert result.owner_id == "w2"

    def test_steal_expired_denied_when_disallowed(self, backend):
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")
        policy = _make_policy(ttl_seconds=1, allow_steal_expired=False)
        _run(backend.acquire_run_lease("run-1", w1, policy))
        time.sleep(1.1)
        result = _run(backend.acquire_run_lease("run-1", w2, policy))
        assert result.acquired is False

    def test_get_run_lease_returns_none_when_none(self, backend):
        lease = _run(backend.get_run_lease("nonexistent"))
        assert lease is None

    def test_get_run_lease_returns_none_after_release(self, backend):
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        _run(backend.release_run_lease("run-1", worker))
        lease = _run(backend.get_run_lease("run-1"))
        assert lease is None

    def test_list_expired_leases(self, backend):
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=1)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        time.sleep(1.1)
        expired = _run(backend.list_expired_leases())
        assert len(expired) == 1
        assert expired[0].run_id == "run-1"


# ===========================================================================
# SQLiteWorkflowLeaseBackend tests
# ===========================================================================


class TestSQLiteLeaseBackend:
    """Tests for standalone SQLiteWorkflowLeaseBackend."""

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "leases.db")

    @pytest.fixture
    def backend(self, db_path):
        return SQLiteWorkflowLeaseBackend(db_path)

    def test_auto_creates_table(self, db_path):
        """Backend creates the lease table on init."""
        # Create the backend to trigger DB creation
        SQLiteWorkflowLeaseBackend(db_path)
        assert Path(db_path).exists()
        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "workflow_run_leases" in table_names
        conn.close()

    def test_acquire_lease(self, backend):
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        result = _run(backend.acquire_run_lease("run-1", worker, policy))
        assert result.acquired is True
        assert result.lease.owner_id == "worker-1"

    def test_deny_competing_owner(self, backend):
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", w1, policy))
        result = _run(backend.acquire_run_lease("run-1", w2, policy))
        assert result.acquired is False
        assert result.current_owner_id == "w1"

    def test_renew_by_owner(self, backend):
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        renewed = _run(backend.renew_run_lease("run-1", worker, policy))
        assert renewed.version == 2

    def test_release_by_owner(self, backend):
        worker = _make_worker()
        policy = _make_policy(ttl_seconds=300)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        released = _run(backend.release_run_lease("run-1", worker))
        assert released.released_at is not None

    def test_persists_across_instances(self, db_path):
        """Lease acquired by one backend instance is visible to another."""
        w1 = _make_worker("w1")
        policy = _make_policy(ttl_seconds=300)

        # First instance acquires
        backend1 = SQLiteWorkflowLeaseBackend(db_path)
        result = _run(backend1.acquire_run_lease("run-1", w1, policy))
        assert result.acquired is True

        # Second instance sees the lease
        backend2 = SQLiteWorkflowLeaseBackend(db_path)
        lease = _run(backend2.get_run_lease("run-1"))
        assert lease is not None
        assert lease.owner_id == "w1"

    def test_cross_instance_deny(self, db_path):
        """Second instance cannot acquire lease held by first."""
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")
        policy = _make_policy(ttl_seconds=300)

        backend1 = SQLiteWorkflowLeaseBackend(db_path)
        _run(backend1.acquire_run_lease("run-1", w1, policy))

        backend2 = SQLiteWorkflowLeaseBackend(db_path)
        result = _run(backend2.acquire_run_lease("run-1", w2, policy))
        assert result.acquired is False

    def test_steal_expired_lease(self, db_path):
        w1 = _make_worker("w1")
        w2 = _make_worker("w2")

        backend1 = SQLiteWorkflowLeaseBackend(db_path)
        policy = _make_policy(ttl_seconds=1, allow_steal_expired=True)
        _run(backend1.acquire_run_lease("run-1", w1, policy))
        time.sleep(1.1)

        backend2 = SQLiteWorkflowLeaseBackend(db_path)
        result = _run(backend2.acquire_run_lease("run-1", w2, policy))
        assert result.acquired is True
        assert result.owner_id == "w2"

    def test_list_expired_leases(self, db_path):
        worker = _make_worker()
        backend = SQLiteWorkflowLeaseBackend(db_path)
        policy = _make_policy(ttl_seconds=1)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        time.sleep(1.1)
        expired = _run(backend.list_expired_leases())
        assert len(expired) == 1
        assert expired[0].run_id == "run-1"

    def test_list_expired_leases_db_query(self, db_path):
        """list_expired_leases queries DB directly."""
        worker = _make_worker()
        backend = SQLiteWorkflowLeaseBackend(db_path)
        policy = _make_policy(ttl_seconds=1)
        _run(backend.acquire_run_lease("run-1", worker, policy))
        time.sleep(1.1)

        # New instance — cache is empty but DB has the lease
        backend2 = SQLiteWorkflowLeaseBackend(db_path)
        expired = _run(backend2.list_expired_leases())
        assert len(expired) == 1


# ===========================================================================
# create_lease_backend() factory tests
# ===========================================================================


class TestCreateLeaseBackend:
    """Tests for the create_lease_backend factory function."""

    def test_create_state_store_backend(self):
        mock_store = object()
        backend = create_lease_backend(
            backend_type="state_store", state_store=mock_store
        )
        assert isinstance(backend, StateStoreLeaseBackend)

    def test_create_memory_backend(self):
        backend = create_lease_backend(backend_type="memory")
        assert isinstance(backend, InMemoryWorkflowLeaseBackend)

    def test_create_sqlite_backend(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        backend = create_lease_backend(
            backend_type="sqlite", db_path=db_path
        )
        assert isinstance(backend, SQLiteWorkflowLeaseBackend)

    def test_state_store_requires_state_store(self):
        with pytest.raises(ValueError, match="state_store is required"):
            create_lease_backend(backend_type="state_store")

    def test_sqlite_requires_db_path(self):
        with pytest.raises(ValueError, match="db_path is required"):
            create_lease_backend(backend_type="sqlite")

    def test_invalid_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown lease backend"):
            create_lease_backend(backend_type="redis")

    def test_default_is_state_store(self):
        with pytest.raises(ValueError, match="state_store is required"):
            create_lease_backend()


# ===========================================================================
# Protocol structural typing test
# ===========================================================================


class TestLeaseBackendProtocol:
    """Verify that implementations satisfy the WorkflowLeaseBackend protocol."""

    def test_inmemory_satisfies_protocol(self):
        backend = InMemoryWorkflowLeaseBackend()
        assert hasattr(backend, "acquire_run_lease")
        assert hasattr(backend, "renew_run_lease")
        assert hasattr(backend, "release_run_lease")
        assert hasattr(backend, "get_run_lease")
        assert hasattr(backend, "list_expired_leases")

    def test_sqlite_satisfies_protocol(self, tmp_path):
        backend = SQLiteWorkflowLeaseBackend(str(tmp_path / "test.db"))
        assert hasattr(backend, "acquire_run_lease")
        assert hasattr(backend, "renew_run_lease")
        assert hasattr(backend, "release_run_lease")
        assert hasattr(backend, "get_run_lease")
        assert hasattr(backend, "list_expired_leases")

    def test_adapter_satisfies_protocol(self):
        backend = StateStoreLeaseBackend(object())
        assert hasattr(backend, "acquire_run_lease")
        assert hasattr(backend, "renew_run_lease")
        assert hasattr(backend, "release_run_lease")
        assert hasattr(backend, "get_run_lease")
        assert hasattr(backend, "list_expired_leases")
