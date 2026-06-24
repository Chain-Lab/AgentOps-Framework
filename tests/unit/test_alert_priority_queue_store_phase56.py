"""Tests for AlertPriorityQueueItem model and AlertPriorityQueueStore implementations.

Phase 56 Task 730: SQLite Priority Queue Store.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue_store import (
    AlertPriorityQueueItem,
    AlertPriorityQueueStore,
    InMemoryAlertPriorityQueueStore,
    SQLiteAlertPriorityQueueStore,
    create_alert_priority_queue_store,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_item(
    attempt_id: str = "nda_t1_a1_1",
    alert_id: str = "a1",
    target_id: str = "t1",
    priority: int = 50,
    status: str = AlertDeliveryStatus.RETRY_SCHEDULED,
    now: datetime | None = None,
) -> AlertPriorityQueueItem:
    if now is None:
        now = datetime.now(timezone.utc)
    return AlertPriorityQueueItem(
        attempt_id=attempt_id,
        alert_id=alert_id,
        target_id=target_id,
        channel_type=AlertDeliveryChannelType.WEBHOOK,
        status=status,
        priority=priority,
        created_at=now,
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestAlertPriorityQueueItemModel:
    """Tests for the AlertPriorityQueueItem Pydantic model."""

    def test_valid_item(self) -> None:
        item = _make_item()
        assert item.attempt_id == "nda_t1_a1_1"
        assert item.priority == 50
        assert item.status == AlertDeliveryStatus.RETRY_SCHEDULED

    def test_invalid_attempt_id_prefix(self) -> None:
        with pytest.raises(ValueError, match="must start with 'nda_'"):
            AlertPriorityQueueItem(
                attempt_id="bad_t1_a1_1",
                alert_id="a1",
                target_id="t1",
                channel_type=AlertDeliveryChannelType.WEBHOOK,
                status=AlertDeliveryStatus.RETRY_SCHEDULED,
                priority=50,
                created_at=datetime.now(timezone.utc),
            )

    def test_naive_datetime_raises(self) -> None:
        with pytest.raises(ValueError, match="must be timezone-aware"):
            AlertPriorityQueueItem(
                attempt_id="nda_t1_a1_1",
                alert_id="a1",
                target_id="t1",
                channel_type=AlertDeliveryChannelType.WEBHOOK,
                status=AlertDeliveryStatus.RETRY_SCHEDULED,
                priority=50,
                created_at=datetime(2024, 1, 1),  # naive
            )

    def test_default_priority_is_zero(self) -> None:
        item = AlertPriorityQueueItem(
            attempt_id="nda_t1_a1_1",
            alert_id="a1",
            target_id="t1",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            status=AlertDeliveryStatus.RETRY_SCHEDULED,
            created_at=datetime.now(timezone.utc),
        )
        assert item.priority == 0

    def test_default_metadata_json_empty(self) -> None:
        item = _make_item()
        assert item.metadata_json == "{}"


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestAlertPriorityQueueStoreProtocol:
    """Verify store implementations satisfy the Protocol."""

    def test_in_memory_satisfies_protocol(self) -> None:
        store = InMemoryAlertPriorityQueueStore()
        assert isinstance(store, AlertPriorityQueueStore)

    def test_sqlite_satisfies_protocol(self, tmp_path) -> None:
        store = SQLiteAlertPriorityQueueStore(str(tmp_path / "test.db"))
        assert isinstance(store, AlertPriorityQueueStore)
        store.close()


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class TestInMemoryAlertPriorityQueueStore:
    """Tests for InMemoryAlertPriorityQueueStore."""

    def test_enqueue_and_dequeue(self) -> None:
        store = InMemoryAlertPriorityQueueStore()
        item1 = _make_item(attempt_id="nda_t1_a1_1", priority=10)
        item2 = _make_item(attempt_id="nda_t1_a2_1", priority=90)
        import asyncio

        asyncio.run(store.enqueue(item1))
        asyncio.run(store.enqueue(item2))

        results = asyncio.run(store.dequeue(limit=10))
        assert len(results) == 2
        assert results[0].attempt_id == "nda_t1_a2_1"  # higher priority first
        assert results[1].attempt_id == "nda_t1_a1_1"

    def test_dequeue_filters_by_status(self) -> None:
        store = InMemoryAlertPriorityQueueStore()
        item1 = _make_item(
            attempt_id="nda_t1_a1_1", status=AlertDeliveryStatus.RETRY_SCHEDULED
        )
        item2 = _make_item(
            attempt_id="nda_t1_a2_1", status=AlertDeliveryStatus.DELIVERED
        )
        import asyncio

        asyncio.run(store.enqueue(item1))
        asyncio.run(store.enqueue(item2))

        results = asyncio.run(
            store.dequeue(status=AlertDeliveryStatus.RETRY_SCHEDULED)
        )
        assert len(results) == 1
        assert results[0].attempt_id == "nda_t1_a1_1"

    def test_count(self) -> None:
        store = InMemoryAlertPriorityQueueStore()
        import asyncio

        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a1_1")))
        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a2_1")))

        assert asyncio.run(store.count()) == 2
        assert asyncio.run(store.count(status=AlertDeliveryStatus.RETRY_SCHEDULED)) == 2
        assert asyncio.run(store.count(status=AlertDeliveryStatus.DELIVERED)) == 0

    def test_count_by_priority(self) -> None:
        store = InMemoryAlertPriorityQueueStore()
        import asyncio

        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a1_1", priority=10)))
        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a2_1", priority=10)))
        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a3_1", priority=90)))

        counts = asyncio.run(store.count_by_priority())
        assert counts == {10: 2, 90: 1}

    def test_update_status(self) -> None:
        store = InMemoryAlertPriorityQueueStore()
        import asyncio

        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a1_1")))

        updated = asyncio.run(
            store.update_status("nda_t1_a1_1", AlertDeliveryStatus.DELIVERED)
        )
        assert updated is not None
        assert updated.status == AlertDeliveryStatus.DELIVERED

        # Verify status persists
        results = asyncio.run(
            store.dequeue(status=AlertDeliveryStatus.DELIVERED)
        )
        assert len(results) == 1

    def test_update_status_not_found(self) -> None:
        store = InMemoryAlertPriorityQueueStore()
        import asyncio

        result = asyncio.run(
            store.update_status("nda_nonexistent", AlertDeliveryStatus.DELIVERED)
        )
        assert result is None

    def test_remove(self) -> None:
        store = InMemoryAlertPriorityQueueStore()
        import asyncio

        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a1_1")))

        assert asyncio.run(store.remove("nda_t1_a1_1")) is True
        assert asyncio.run(store.count()) == 0
        assert asyncio.run(store.remove("nda_t1_a1_1")) is False

    def test_dequeue_limit(self) -> None:
        store = InMemoryAlertPriorityQueueStore()
        import asyncio

        for i in range(5):
            asyncio.run(store.enqueue(_make_item(attempt_id=f"nda_t1_a{i}_1")))

        results = asyncio.run(store.dequeue(limit=3))
        assert len(results) == 3


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class TestSQLiteAlertPriorityQueueStore:
    """Tests for SQLiteAlertPriorityQueueStore."""

    def test_persisted_priority_ordering(self, tmp_path) -> None:
        store = SQLiteAlertPriorityQueueStore(str(tmp_path / "test.db"))
        import asyncio

        item_low = _make_item(attempt_id="nda_t1_a1_1", priority=10)
        item_high = _make_item(attempt_id="nda_t1_a2_1", priority=90)

        asyncio.run(store.enqueue(item_low))
        asyncio.run(store.enqueue(item_high))

        results = asyncio.run(store.dequeue(limit=10))
        assert len(results) == 2
        assert results[0].attempt_id == "nda_t1_a2_1"
        assert results[1].attempt_id == "nda_t1_a1_1"

        store.close()

    def test_persists_across_reopen(self, tmp_path) -> None:
        db_path = str(tmp_path / "persist.db")
        store1 = SQLiteAlertPriorityQueueStore(db_path)
        import asyncio

        asyncio.run(store1.enqueue(_make_item(attempt_id="nda_t1_a1_1", priority=25)))
        store1.close()

        # Re-open
        store2 = SQLiteAlertPriorityQueueStore(db_path)
        results = asyncio.run(store2.dequeue(limit=10))
        assert len(results) == 1
        assert results[0].attempt_id == "nda_t1_a1_1"
        assert results[0].priority == 25
        store2.close()

    def test_sqlite_default_priority_is_zero(self, tmp_path) -> None:
        store = SQLiteAlertPriorityQueueStore(str(tmp_path / "test.db"))
        import asyncio

        item = _make_item(attempt_id="nda_t1_a1_1", priority=0)
        asyncio.run(store.enqueue(item))

        results = asyncio.run(store.dequeue())
        assert len(results) == 1
        assert results[0].priority == 0
        store.close()

    def test_sqlite_count_and_count_by_priority(self, tmp_path) -> None:
        store = SQLiteAlertPriorityQueueStore(str(tmp_path / "test.db"))
        import asyncio

        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a1_1", priority=10)))
        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a2_1", priority=10)))
        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a3_1", priority=90)))

        assert asyncio.run(store.count()) == 3
        counts = asyncio.run(store.count_by_priority())
        assert counts == {10: 2, 90: 1}
        store.close()

    def test_sqlite_update_status(self, tmp_path) -> None:
        store = SQLiteAlertPriorityQueueStore(str(tmp_path / "test.db"))
        import asyncio

        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a1_1")))

        updated = asyncio.run(
            store.update_status("nda_t1_a1_1", AlertDeliveryStatus.DELIVERED)
        )
        assert updated is not None
        assert updated.status == AlertDeliveryStatus.DELIVERED
        store.close()

    def test_sqlite_remove(self, tmp_path) -> None:
        store = SQLiteAlertPriorityQueueStore(str(tmp_path / "test.db"))
        import asyncio

        asyncio.run(store.enqueue(_make_item(attempt_id="nda_t1_a1_1")))

        assert asyncio.run(store.remove("nda_t1_a1_1")) is True
        assert asyncio.run(store.count()) == 0
        store.close()

    def test_sqlite_dequeue_filter_by_status(self, tmp_path) -> None:
        store = SQLiteAlertPriorityQueueStore(str(tmp_path / "test.db"))
        import asyncio

        asyncio.run(
            store.enqueue(
                _make_item(
                    attempt_id="nda_t1_a1_1",
                    status=AlertDeliveryStatus.RETRY_SCHEDULED,
                )
            )
        )
        asyncio.run(
            store.enqueue(
                _make_item(
                    attempt_id="nda_t1_a2_1",
                    status=AlertDeliveryStatus.DELIVERED,
                )
            )
        )

        results = asyncio.run(
            store.dequeue(status=AlertDeliveryStatus.RETRY_SCHEDULED)
        )
        assert len(results) == 1
        assert results[0].attempt_id == "nda_t1_a1_1"
        store.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateAlertPriorityQueueStore:
    """Tests for the store factory function."""

    def test_factory_memory(self) -> None:
        import asyncio

        store = create_alert_priority_queue_store("memory")
        assert isinstance(store, InMemoryAlertPriorityQueueStore)
        assert isinstance(store, AlertPriorityQueueStore)

    def test_factory_sqlite(self, tmp_path) -> None:
        store = create_alert_priority_queue_store(
            "sqlite", str(tmp_path / "factory.db")
        )
        assert isinstance(store, SQLiteAlertPriorityQueueStore)
        assert isinstance(store, AlertPriorityQueueStore)
        store.close()

    def test_factory_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown priority queue store type"):
            create_alert_priority_queue_store("redis")
