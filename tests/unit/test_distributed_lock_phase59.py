"""Tests for distributed lock (Phase 59 Task 733)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from agent_app.runtime.policy_rollout_federation_notification_distributed_lock import (
    DistributedLockStatus,
    DistributedLockStore,
    InMemoryDistributedLockStore,
    SQLiteDistributedLockStore,
    create_distributed_lock_store,
    _now,
)


def _make_store(store_type: str = "memory", db_path: str | None = None) -> DistributedLockStore:
    return create_distributed_lock_store(store_type, db_path)


class TestInMemoryDistributedLock:
    """In-memory distributed lock tests."""

    def test_acquire_free_lock(self):
        """Acquiring a free lock succeeds."""
        store = _make_store("memory")
        result = store.acquire("lock1", "owner1", 60)
        assert result.acquired is True
        assert result.owner_id == "owner1"
        assert result.fencing_token is not None
        assert result.lock_name == "lock1"

    def test_second_owner_denied(self):
        """Second owner cannot acquire held lock."""
        store = _make_store("memory")
        store.acquire("lock1", "owner1", 60)
        result = store.acquire("lock1", "owner2", 60)
        assert result.acquired is False
        assert result.owner_id == "owner1"

    def test_expired_takeover(self):
        """Expired lock can be taken over by new owner."""
        store = _make_store("memory")
        past = _now() - timedelta(seconds=30)
        # Directly set an expired lock with a token that doesn't conflict
        # with _next_token (which starts at 1)
        store._next_token = 5
        store._locks["lock1"] = {
            "owner_id": "owner1",
            "lease_expires_at": past,
            "fencing_token": 3,
            "updated_at": past,
        }
        result = store.acquire("lock1", "owner2", 60)
        assert result.acquired is True
        assert result.owner_id == "owner2"
        assert result.fencing_token == 5  # new token from _next_token

    def test_renew_by_owner(self):
        """Owner can renew their own lock."""
        store = _make_store("memory")
        store.acquire("lock1", "owner1", 60)
        result = store.renew("lock1", "owner1", 120)
        assert result.acquired is True

    def test_renew_by_non_owner_denied(self):
        """Non-owner cannot renew lock."""
        store = _make_store("memory")
        store.acquire("lock1", "owner1", 60)
        result = store.renew("lock1", "owner2", 120)
        assert result.acquired is False

    def test_release_by_owner(self):
        """Owner can release lock."""
        store = _make_store("memory")
        store.acquire("lock1", "owner1", 60)
        result = store.release("lock1", "owner1")
        assert result is True
        assert store.get_status("lock1").acquired is False

    def test_release_by_non_owner_denied(self):
        """Non-owner cannot release lock."""
        store = _make_store("memory")
        store.acquire("lock1", "owner1", 60)
        result = store.release("lock1", "owner2")
        assert result is False

    def test_fencing_token_increments(self):
        """Fencing token increments with each acquisition."""
        store = _make_store("memory")
        r1 = store.acquire("lock1", "owner1", 60)
        store.release("lock1", "owner1")
        r2 = store.acquire("lock1", "owner2", 60)
        assert r2.fencing_token == r1.fencing_token + 1

    def test_get_status_free(self):
        """get_status returns acquired=False for free lock."""
        store = _make_store("memory")
        result = store.get_status("nonexistent")
        assert result.acquired is False


class TestSQLiteDistributedLock:
    """SQLite distributed lock tests."""

    def test_acquire_free_lock(self, tmp_path):
        """Acquiring a free lock in SQLite."""
        db = str(tmp_path / "locks.db")
        store = SQLiteDistributedLockStore(db_path=db)
        result = store.acquire("lock1", "owner1", 60)
        assert result.acquired is True
        assert result.owner_id == "owner1"
        store.close()

    def test_second_owner_denied(self, tmp_path):
        """Second owner denied in SQLite."""
        db = str(tmp_path / "locks.db")
        store = SQLiteDistributedLockStore(db_path=db)
        store.acquire("lock1", "owner1", 60)
        result = store.acquire("lock1", "owner2", 60)
        assert result.acquired is False
        store.close()

    def test_expired_takeover(self, tmp_path):
        """Expired lock takeover in SQLite."""
        db = str(tmp_path / "locks.db")
        store = SQLiteDistributedLockStore(db_path=db)
        past = _now() - timedelta(seconds=120)  # well past 60s lease
        store.acquire("lock1", "owner1", 60, now=past)
        result = store.acquire("lock1", "owner2", 60)
        assert result.acquired is True
        assert result.owner_id == "owner2"
        store.close()

    def test_renew_by_owner(self, tmp_path):
        """Owner can renew in SQLite."""
        db = str(tmp_path / "locks.db")
        store = SQLiteDistributedLockStore(db_path=db)
        store.acquire("lock1", "owner1", 60)
        result = store.renew("lock1", "owner1", 120)
        assert result.acquired is True
        store.close()

    def test_renew_by_non_owner_denied(self, tmp_path):
        """Non-owner cannot renew in SQLite."""
        db = str(tmp_path / "locks.db")
        store = SQLiteDistributedLockStore(db_path=db)
        store.acquire("lock1", "owner1", 60)
        result = store.renew("lock1", "owner2", 120)
        assert result.acquired is False
        store.close()

    def test_release_by_owner(self, tmp_path):
        """Owner can release in SQLite."""
        db = str(tmp_path / "locks.db")
        store = SQLiteDistributedLockStore(db_path=db)
        store.acquire("lock1", "owner1", 60)
        result = store.release("lock1", "owner1")
        assert result is True
        store.close()

    def test_release_by_non_owner_denied(self, tmp_path):
        """Non-owner cannot release in SQLite."""
        db = str(tmp_path / "locks.db")
        store = SQLiteDistributedLockStore(db_path=db)
        store.acquire("lock1", "owner1", 60)
        result = store.release("lock1", "owner2")
        assert result is False
        store.close()

    def test_persists_across_instances(self, tmp_path):
        """Lock persists across SQLite store instances."""
        db = str(tmp_path / "locks.db")
        store1 = SQLiteDistributedLockStore(db_path=db)
        store1.acquire("lock1", "owner1", 60)
        store1.close()

        store2 = SQLiteDistributedLockStore(db_path=db)
        result = store2.get_status("lock1")
        assert result.acquired is True
        assert result.owner_id == "owner1"
        store2.close()

    def test_fencing_token_increments(self, tmp_path):
        """Fencing token increments across acquisitions."""
        db = str(tmp_path / "locks.db")
        store = SQLiteDistributedLockStore(db_path=db)
        r1 = store.acquire("lock1", "owner1", 60)
        store.release("lock1", "owner1")
        r2 = store.acquire("lock1", "owner2", 60)
        assert r2.fencing_token == r1.fencing_token + 1
        store.close()


class TestDistributedLockFactory:
    """Factory function tests."""

    def test_memory_factory(self):
        store = create_distributed_lock_store("memory")
        assert isinstance(store, InMemoryDistributedLockStore)

    def test_sqlite_factory(self, tmp_path):
        db = str(tmp_path / "locks.db")
        store = create_distributed_lock_store("sqlite", db_path=db)
        assert isinstance(store, SQLiteDistributedLockStore)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown distributed lock store type"):
            create_distributed_lock_store("redis")
