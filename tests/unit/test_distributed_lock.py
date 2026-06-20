"""Tests for Phase 49 Task 6 — distributed lock (InMemory + SQLite).

Covers:
- DistributedLock Protocol (structural typing)
- InMemoryDistributedLock (acquire, release, refresh, expiry)
- SQLiteDistributedLock (acquire, release, refresh, expiry, persistence)
- create_distributed_lock() factory
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_app.runtime.distributed_lock import (
    DistributedLock,
    InMemoryDistributedLock,
    SQLiteDistributedLock,
    create_distributed_lock,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run async coroutine synchronously using asyncio.run."""
    return asyncio.run(coro)


# ===========================================================================
# InMemoryDistributedLock tests
# ===========================================================================


class TestInMemoryDistributedLock:
    """Tests for InMemoryDistributedLock."""

    @pytest.fixture
    def lock(self):
        return InMemoryDistributedLock()

    def test_acquire_new_lock_returns_true(self, lock):
        result = _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        assert result is True

    def test_cannot_acquire_held_lock(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.acquire("my-lock", "owner-2", ttl_seconds=30))
        assert result is False

    def test_expired_lock_can_be_acquired_by_new_owner(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=1))
        time.sleep(1.1)
        result = _run(lock.acquire("my-lock", "owner-2", ttl_seconds=30))
        assert result is True

    def test_owner_can_re_acquire_own_lock(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.acquire("my-lock", "owner-1", ttl_seconds=60))
        assert result is True

    def test_release_by_owner_returns_true(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.release("my-lock", "owner-1"))
        assert result is True

    def test_release_removes_lock(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        _run(lock.release("my-lock", "owner-1"))
        # Lock should be gone, so a new owner can acquire
        result = _run(lock.acquire("my-lock", "owner-2", ttl_seconds=30))
        assert result is True

    def test_non_owner_cannot_release(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.release("my-lock", "owner-2"))
        assert result is False

    def test_release_nonexistent_lock_returns_false(self, lock):
        result = _run(lock.release("nonexistent", "owner-1"))
        assert result is False

    def test_refresh_by_owner_returns_true(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.refresh("my-lock", "owner-1", ttl_seconds=60))
        assert result is True

    def test_refresh_by_non_owner_returns_false(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.refresh("my-lock", "owner-2", ttl_seconds=60))
        assert result is False

    def test_refresh_nonexistent_lock_returns_false(self, lock):
        result = _run(lock.refresh("nonexistent", "owner-1", ttl_seconds=60))
        assert result is False

    def test_multiple_independent_locks(self, lock):
        r1 = _run(lock.acquire("lock-a", "owner-1", ttl_seconds=30))
        r2 = _run(lock.acquire("lock-b", "owner-2", ttl_seconds=30))
        assert r1 is True
        assert r2 is True
        # owner-2 cannot take lock-a
        r3 = _run(lock.acquire("lock-a", "owner-2", ttl_seconds=30))
        assert r3 is False


# ===========================================================================
# SQLiteDistributedLock tests
# ===========================================================================


class TestSQLiteDistributedLock:
    """Tests for SQLiteDistributedLock."""

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "distributed_locks.db")

    @pytest.fixture
    def lock(self, db_path):
        return SQLiteDistributedLock(db_path)

    def test_auto_creates_table(self, db_path):
        """Backend creates the lock table on init."""
        SQLiteDistributedLock(db_path)
        assert Path(db_path).exists()
        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "distributed_locks" in table_names
        conn.close()

    def test_acquire_new_lock_returns_true(self, lock):
        result = _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        assert result is True

    def test_cannot_acquire_held_lock(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.acquire("my-lock", "owner-2", ttl_seconds=30))
        assert result is False

    def test_expired_lock_can_be_acquired_by_new_owner(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=1))
        time.sleep(1.1)
        result = _run(lock.acquire("my-lock", "owner-2", ttl_seconds=30))
        assert result is True

    def test_owner_can_re_acquire_own_lock(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.acquire("my-lock", "owner-1", ttl_seconds=60))
        assert result is True

    def test_release_by_owner_returns_true(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.release("my-lock", "owner-1"))
        assert result is True

    def test_release_removes_lock(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        _run(lock.release("my-lock", "owner-1"))
        # Lock should be gone, so a new owner can acquire
        result = _run(lock.acquire("my-lock", "owner-2", ttl_seconds=30))
        assert result is True

    def test_non_owner_cannot_release(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.release("my-lock", "owner-2"))
        assert result is False

    def test_release_nonexistent_lock_returns_false(self, lock):
        result = _run(lock.release("nonexistent", "owner-1"))
        assert result is False

    def test_refresh_by_owner_returns_true(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.refresh("my-lock", "owner-1", ttl_seconds=60))
        assert result is True

    def test_refresh_by_non_owner_returns_false(self, lock):
        _run(lock.acquire("my-lock", "owner-1", ttl_seconds=30))
        result = _run(lock.refresh("my-lock", "owner-2", ttl_seconds=60))
        assert result is False

    def test_refresh_nonexistent_lock_returns_false(self, lock):
        result = _run(lock.refresh("nonexistent", "owner-1", ttl_seconds=60))
        assert result is False

    def test_persists_across_instances(self, db_path):
        """Lock acquired by one instance is visible to another."""
        lock1 = SQLiteDistributedLock(db_path)
        _run(lock1.acquire("my-lock", "owner-1", ttl_seconds=300))

        # Second instance should see the lock
        lock2 = SQLiteDistributedLock(db_path)
        result = _run(lock2.acquire("my-lock", "owner-2", ttl_seconds=30))
        assert result is False  # Still held by owner-1

    def test_release_persists_across_instances(self, db_path):
        """Lock released by one instance can be acquired by another."""
        lock1 = SQLiteDistributedLock(db_path)
        _run(lock1.acquire("my-lock", "owner-1", ttl_seconds=300))
        _run(lock1.release("my-lock", "owner-1"))

        lock2 = SQLiteDistributedLock(db_path)
        result = _run(lock2.acquire("my-lock", "owner-2", ttl_seconds=30))
        assert result is True


# ===========================================================================
# create_distributed_lock() factory tests
# ===========================================================================


class TestCreateDistributedLock:
    """Tests for the create_distributed_lock factory function."""

    def test_create_memory(self):
        lock = create_distributed_lock(store_type="memory")
        assert isinstance(lock, InMemoryDistributedLock)

    def test_create_sqlite(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        lock = create_distributed_lock(store_type="sqlite", db_path=db_path)
        assert isinstance(lock, SQLiteDistributedLock)

    def test_create_sqlite_default_db_path(self, tmp_path, monkeypatch):
        """SQLite backend uses default db_path when none is provided."""
        monkeypatch.chdir(tmp_path)
        lock = create_distributed_lock(store_type="sqlite")
        assert isinstance(lock, SQLiteDistributedLock)

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Unknown distributed lock store type"):
            create_distributed_lock(store_type="redis")


# ===========================================================================
# Protocol structural typing test
# ===========================================================================


class TestDistributedLockProtocol:
    """Verify that implementations satisfy the DistributedLock protocol."""

    def test_inmemory_satisfies_protocol(self):
        lock = InMemoryDistributedLock()
        assert isinstance(lock, DistributedLock)

    def test_sqlite_satisfies_protocol(self, tmp_path):
        lock = SQLiteDistributedLock(str(tmp_path / "test.db"))
        assert isinstance(lock, DistributedLock)
