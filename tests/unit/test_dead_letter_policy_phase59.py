"""Tests for dead letter policy (Phase 59 Task 736)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue_store import (
    AlertPriorityQueueItem,
    AlertPriorityQueueItemStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_dead_letter_policy import (
    DeadLetterPolicyConfig,
    DeadLetterPolicyResult,
    DeadLetterPolicyStore,
    DeadLetterRecord,
    InMemoryDeadLetterPolicyStore,
    SQLiteDeadLetterPolicyStore,
    create_dead_letter_policy_store,
    _now,
)


def _make_item(
    attempt_id: str = "nda_1",
    alert_id: str = "a1",
    target_id: str = "t1",
    attempt: int = 1,
    status: str = AlertPriorityQueueItemStatus.QUEUED.value,
    metadata_json: str = "{}",
) -> AlertPriorityQueueItem:
    return AlertPriorityQueueItem(
        attempt_id=attempt_id,
        alert_id=alert_id,
        target_id=target_id,
        channel_type=AlertDeliveryChannelType.EMAIL,
        status=status,
        priority=1,
        created_at=_now(),
        attempt=attempt,
        metadata_json=metadata_json,
    )


class TestInMemoryDeadLetterPolicy:
    """In-memory dead letter policy tests."""

    def test_below_max_retries_not_dead_letter(self):
        """Item below max retries is not dead-lettered."""
        store = InMemoryDeadLetterPolicyStore(
            config=DeadLetterPolicyConfig(max_retries=5)
        )
        item = _make_item(attempt=3)
        result = store.evaluate(item)
        assert result.is_dead_letter is False

    def test_at_max_retries_not_dead_letter(self):
        """Item at exactly max retries is not dead-lettered (attempt > max)."""
        store = InMemoryDeadLetterPolicyStore(
            config=DeadLetterPolicyConfig(max_retries=5)
        )
        item = _make_item(attempt=5)
        result = store.evaluate(item)
        assert result.is_dead_letter is False

    def test_above_max_retries_is_dead_letter(self):
        """Item above max retries is dead-lettered."""
        store = InMemoryDeadLetterPolicyStore(
            config=DeadLetterPolicyConfig(max_retries=5)
        )
        item = _make_item(attempt=6)
        result = store.evaluate(item)
        assert result.is_dead_letter is True
        assert result.reason == "max_retries_exceeded"
        assert result.record is not None
        assert result.record.attempt_count == 6

    def test_record_dead_letter_stores(self):
        """Record dead letter stores the record."""
        store = InMemoryDeadLetterPolicyStore(
            config=DeadLetterPolicyConfig(max_retries=3)
        )
        item = _make_item(attempt=4)
        result = store.evaluate(item)
        assert result.is_dead_letter is True
        stored = store.record_dead_letter(result.record)
        assert stored.attempt_id == "nda_1"
        retrieved = store.get_record("nda_1")
        assert retrieved is not None
        assert retrieved.alert_id == "a1"

    def test_get_nonexistent_returns_none(self):
        """Get nonexistent returns None."""
        store = InMemoryDeadLetterPolicyStore()
        assert store.get_record("nonexistent") is None

    def test_list_all_records(self):
        """List all records returns all dead letter records."""
        store = InMemoryDeadLetterPolicyStore(
            config=DeadLetterPolicyConfig(max_retries=1)
        )
        for i in range(3):
            item = _make_item(attempt_id=f"nda_{i}", attempt=2)
            result = store.evaluate(item)
            store.record_dead_letter(result.record)
        records = store.list_records()
        assert len(records) == 3

    def test_list_by_alert_id(self):
        """List records filtered by alert_id."""
        store = InMemoryDeadLetterPolicyStore(
            config=DeadLetterPolicyConfig(max_retries=1)
        )
        item_a1 = _make_item(attempt_id="nda_1", alert_id="a1", attempt=2)
        item_a2 = _make_item(attempt_id="nda_2", alert_id="a2", attempt=2)
        store.record_dead_letter(store.evaluate(item_a1).record)
        store.record_dead_letter(store.evaluate(item_a2).record)
        records = store.list_records(alert_id="a1")
        assert len(records) == 1
        assert records[0].alert_id == "a1"

    def test_list_by_target_id(self):
        """List records filtered by target_id."""
        store = InMemoryDeadLetterPolicyStore(
            config=DeadLetterPolicyConfig(max_retries=1)
        )
        item_t1 = _make_item(attempt_id="nda_1", target_id="t1", attempt=2)
        item_t2 = _make_item(attempt_id="nda_2", target_id="t2", attempt=2)
        store.record_dead_letter(store.evaluate(item_t1).record)
        store.record_dead_letter(store.evaluate(item_t2).record)
        records = store.list_records(target_id="t1")
        assert len(records) == 1
        assert records[0].target_id == "t1"

    def test_custom_max_retries(self):
        """Custom max_retries config works."""
        store = InMemoryDeadLetterPolicyStore(
            config=DeadLetterPolicyConfig(max_retries=10)
        )
        item = _make_item(attempt=5)
        result = store.evaluate(item)
        assert result.is_dead_letter is False
        item2 = _make_item(attempt=11)
        result2 = store.evaluate(item2)
        assert result2.is_dead_letter is True

    def test_metadata_preserved(self):
        """Metadata from original item is preserved."""
        metadata = '{"last_error": "timeout", "requeue_count": 3}'
        store = InMemoryDeadLetterPolicyStore(
            config=DeadLetterPolicyConfig(max_retries=1)
        )
        item = _make_item(attempt=2, metadata_json=metadata)
        result = store.evaluate(item)
        assert result.is_dead_letter is True
        assert result.record.metadata_json == metadata


class TestSQLiteDeadLetterPolicy:
    """SQLite dead letter policy tests."""

    def test_evaluate_and_record(self, tmp_path):
        """Evaluate and record in SQLite."""
        db = str(tmp_path / "dl_policy.db")
        store = SQLiteDeadLetterPolicyStore(
            db_path=db, config=DeadLetterPolicyConfig(max_retries=3)
        )
        item = _make_item(attempt=4)
        result = store.evaluate(item)
        assert result.is_dead_letter is True
        store.record_dead_letter(result.record)
        retrieved = store.get_record("nda_1")
        assert retrieved is not None
        assert retrieved.attempt_count == 4
        store.close()

    def test_persists_across_instances(self, tmp_path):
        """Record persists across SQLite store instances."""
        db = str(tmp_path / "dl_policy.db")
        store1 = SQLiteDeadLetterPolicyStore(
            db_path=db, config=DeadLetterPolicyConfig(max_retries=1)
        )
        item = _make_item(attempt=2)
        result = store1.evaluate(item)
        store1.record_dead_letter(result.record)
        store1.close()

        store2 = SQLiteDeadLetterPolicyStore(
            db_path=db, config=DeadLetterPolicyConfig(max_retries=1)
        )
        retrieved = store2.get_record("nda_1")
        assert retrieved is not None
        store2.close()

    def test_list_by_alert_id(self, tmp_path):
        """List by alert_id in SQLite."""
        db = str(tmp_path / "dl_policy.db")
        store = SQLiteDeadLetterPolicyStore(
            db_path=db, config=DeadLetterPolicyConfig(max_retries=1)
        )
        item_a1 = _make_item(attempt_id="nda_1", alert_id="a1", attempt=2)
        item_a2 = _make_item(attempt_id="nda_2", alert_id="a2", attempt=2)
        store.record_dead_letter(store.evaluate(item_a1).record)
        store.record_dead_letter(store.evaluate(item_a2).record)
        records = store.list_records(alert_id="a1")
        assert len(records) == 1
        assert records[0].alert_id == "a1"
        store.close()


class TestDeadLetterPolicyFactory:
    """Factory function tests."""

    def test_memory_factory(self):
        store = create_dead_letter_policy_store("memory")
        assert isinstance(store, InMemoryDeadLetterPolicyStore)

    def test_sqlite_factory(self, tmp_path):
        db = str(tmp_path / "dl_policy.db")
        store = create_dead_letter_policy_store("sqlite", db_path=db, max_retries=10)
        assert isinstance(store, SQLiteDeadLetterPolicyStore)
        assert store.config.max_retries == 10

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown dead letter policy store type"):
            create_dead_letter_policy_store("redis")
