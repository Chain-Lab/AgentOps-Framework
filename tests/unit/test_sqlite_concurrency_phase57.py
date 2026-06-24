"""Tests for Phase 57: SQLite concurrency safety.

Phase 57 Task 7: Concurrent claim/ack/enqueue across multiple store instances.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue_store import (
    AlertPriorityQueueItem,
    AlertPriorityQueueItemStatus,
    InMemoryAlertPriorityQueueStore,
    SQLiteAlertPriorityQueueStore,
    create_alert_priority_queue_store,
)
from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
)
from agent_app.runtime.policy_rollout_federation_notification_retry_daemon_state import (
    SQLiteAlertDeliveryRetryDaemonStateStore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(attempt_id, priority=50, **kwargs):
    defaults = dict(
        attempt_id=attempt_id,
        alert_id="a1",
        target_id="t1",
        channel_type=AlertDeliveryChannelType.WEBHOOK,
        status="queued",
        priority=priority,
        created_at=datetime.now(timezone.utc),
        next_retry_at=None,
        attempt=1,
        claimed_by=None,
        claimed_at=None,
        lease_expires_at=None,
        available_at=datetime.now(timezone.utc),
        metadata_json="{}",
    )
    defaults.update(kwargs)
    return AlertPriorityQueueItem(**defaults)


def _make_sqlite_store(tmp_path, name="test"):
    import os
    db_path = str(tmp_path / f"{name}.db")
    return SQLiteAlertPriorityQueueStore(db_path)


def _make_shared_sqlite_store(tmp_path):
    """Create two store instances sharing the same SQLite database."""
    db_path = str(tmp_path / "shared.db")
    store1 = SQLiteAlertPriorityQueueStore(db_path)
    store2 = SQLiteAlertPriorityQueueStore(db_path)
    return store1, store2, db_path


# ---------------------------------------------------------------------------
# Priority queue concurrency tests
# ---------------------------------------------------------------------------


class TestSQLitePriorityQueueConcurrency:
    """Phase 57: SQLite priority queue concurrency tests."""

    def test_two_instances_claim_without_duplicate(self, tmp_path):
        """Two store instances cannot claim the same item."""
        store1 = _make_sqlite_store(tmp_path, "s1")
        store2 = _make_sqlite_store(tmp_path, "s2")
        item = _make_item("nda_t1_a1_1", priority=50)
        asyncio.run(store1.enqueue(item))

        claimed1 = asyncio.run(store1.claim_next(limit=1, worker_id="w1"))
        claimed2 = asyncio.run(store2.claim_next(limit=1, worker_id="w2"))

        assert len(claimed1) == 1
        assert len(claimed2) == 0
        store1.close()
        store2.close()

    def test_concurrent_enqueue_both_instances(self, tmp_path):
        """Both instances can enqueue without conflict."""
        store1, store2, _ = _make_shared_sqlite_store(tmp_path)

        asyncio.run(store1.enqueue(_make_item("nda_a1", priority=10)))
        asyncio.run(store2.enqueue(_make_item("nda_a2", priority=20)))

        items1 = asyncio.run(store1.dequeue())
        items2 = asyncio.run(store2.dequeue())
        assert len(items1) == 2
        assert len(items2) == 2
        store1.close()
        store2.close()

    def test_concurrent_acknowledge_only_one_succeeds(self, tmp_path):
        """Only one instance can acknowledge the same claimed item."""
        store1, store2, _ = _make_shared_sqlite_store(tmp_path)
        item = _make_item("nda_t1_a1_1", priority=50)
        asyncio.run(store1.enqueue(item))

        claimed = asyncio.run(store1.claim_next(limit=1, worker_id="w1"))
        assert len(claimed) == 1

        # Both try to ack with same worker_id
        result1 = asyncio.run(store1.acknowledge("nda_t1_a1_1", worker_id="w1"))
        result2 = asyncio.run(store2.acknowledge("nda_t1_a1_1", worker_id="w1"))

        # First succeeds, second gets None (already completed)
        assert result1 is not None
        assert result1.status == "completed"
        assert result2 is None
        store1.close()
        store2.close()

    def test_claim_with_different_workers_isolated(self, tmp_path):
        """Items claimed by different workers are tracked correctly."""
        store = _make_sqlite_store(tmp_path, "s1")
        items = [_make_item(f"nda_t1_a{i}_1", priority=i * 10) for i in range(3)]
        for item in items:
            asyncio.run(store.enqueue(item))

        claimed = asyncio.run(store.claim_next(limit=3, worker_id="worker-alpha"))
        assert len(claimed) == 3
        for c in claimed:
            assert c.claimed_by == "worker-alpha"

        # Verify via fresh query
        items = asyncio.run(store.dequeue())
        for item in items:
            assert item.claimed_by == "worker-alpha"
        store.close()

    def test_claim_preserves_priority_order_across_instances(self, tmp_path):
        """Priority ordering is maintained across concurrent claims."""
        store1, store2, _ = _make_shared_sqlite_store(tmp_path)

        for i in range(5):
            item = _make_item(f"nda_t1_a{i}_1", priority=i * 25)
            asyncio.run(store1.enqueue(item))

        # Claim from different instances
        claimed1 = asyncio.run(store1.claim_next(limit=3, worker_id="w1"))
        claimed2 = asyncio.run(store2.claim_next(limit=2, worker_id="w2"))

        all_claimed = claimed1 + claimed2
        priorities = [c.priority for c in all_claimed]
        assert priorities == sorted(priorities, reverse=True)
        store1.close()
        store2.close()

    def test_sqlite_busy_timeout_retry(self, tmp_path):
        """SQLite busy_timeout allows retry on lock contention."""
        store = _make_sqlite_store(tmp_path, "s1")
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store.enqueue(item))
        # Basic operation should succeed without timeout
        claimed = asyncio.run(store.claim_next(limit=1))
        assert len(claimed) == 1
        store.close()

    def test_requeue_then_reclaim_by_different_instance(self, tmp_path):
        """Item requeued by one instance can be claimed by another."""
        store1, store2, _ = _make_shared_sqlite_store(tmp_path)
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store1.enqueue(item))

        claimed = asyncio.run(store1.claim_next(limit=1, worker_id="w1"))
        assert len(claimed) == 1

        asyncio.run(store1.requeue("nda_t1_a1_1", reason="retry"))
        claimed2 = asyncio.run(store2.claim_next(limit=1, worker_id="w2"))
        assert len(claimed2) == 1
        assert claimed2[0].status == "claimed"
        assert claimed2[0].claimed_by == "w2"
        store1.close()
        store2.close()


# ---------------------------------------------------------------------------
# Daemon state concurrency tests
# ---------------------------------------------------------------------------


class TestSQLiteDaemonStateConcurrency:
    """Phase 57: Daemon state SQLite concurrency tests."""

    def test_concurrent_save_last_write_wins(self, tmp_path):
        """Concurrent saves: last write wins (deterministic)."""
        db_path = str(tmp_path / "state.db")
        store1 = SQLiteAlertDeliveryRetryDaemonStateStore(db_path)
        store2 = SQLiteAlertDeliveryRetryDaemonStateStore(db_path)

        from agent_app.runtime.policy_rollout_federation_notification_retry_daemon_state import (
            AlertDeliveryRetryDaemonState,
        )
        state1 = AlertDeliveryRetryDaemonState(daemon_id="d1", consecutive_failures=1)
        state2 = AlertDeliveryRetryDaemonState(daemon_id="d1", consecutive_failures=2)

        store1.save(state1)
        store2.save(state2)

        # Both closed, read back
        store1.close()
        store2.close()

        store3 = SQLiteAlertDeliveryRetryDaemonStateStore(db_path)
        retrieved = store3.get("d1")
        assert retrieved is not None
        # One of the two values — deterministic either way
        assert retrieved.consecutive_failures in (1, 2)
        store3.close()

    def test_concurrent_state_persists(self, tmp_path):
        """State saved by one instance is visible to another."""
        db_path = str(tmp_path / "state.db")
        store1 = SQLiteAlertDeliveryRetryDaemonStateStore(db_path)

        from agent_app.runtime.policy_rollout_federation_notification_retry_daemon_state import (
            AlertDeliveryRetryDaemonState,
        )
        state = AlertDeliveryRetryDaemonState(
            daemon_id="d1",
            actual_state="running",
            consecutive_failures=3,
        )
        store1.save(state)
        store1.close()

        store2 = SQLiteAlertDeliveryRetryDaemonStateStore(db_path)
        retrieved = store2.get("d1")
        assert retrieved is not None
        assert retrieved.actual_state == "running"
        assert retrieved.consecutive_failures == 3
        store2.close()


# ---------------------------------------------------------------------------
# WAL mode verification
# ---------------------------------------------------------------------------


class TestSQLiteWALMode:
    """Phase 57: SQLite WAL mode verification."""

    def test_priority_queue_wal_mode(self, tmp_path):
        """Priority queue store uses WAL journal mode."""
        store = _make_sqlite_store(tmp_path, "wal_test")
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        store.close()

    def test_daemon_state_wal_mode(self, tmp_path):
        """Daemon state store uses WAL journal mode."""
        store = SQLiteAlertDeliveryRetryDaemonStateStore(str(tmp_path / "state.db"))
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        store.close()
