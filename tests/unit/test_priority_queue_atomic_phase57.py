"""Tests for Phase 57: Priority queue atomic claim/ack/fail/requeue/lease lifecycle.

Phase 57 Task 2: Atomic claim / acknowledge / fail / requeue / lease expiry.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue_store import (
    AlertPriorityQueueItem,
    AlertPriorityQueueItemStatus,
    AlertPriorityQueueStore,
    InMemoryAlertPriorityQueueStore,
    SQLiteAlertPriorityQueueStore,
    create_alert_priority_queue_store,
    _redact_error,
)
from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(attempt_id="nda_t1_a1_1", priority=50, status="queued", **kwargs):
    defaults = dict(
        attempt_id=attempt_id,
        alert_id="a1",
        target_id="t1",
        channel_type=AlertDeliveryChannelType.WEBHOOK,
        status=status,
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


def _make_store(store_type="memory", tmp_path=None):
    if store_type == "memory":
        return InMemoryAlertPriorityQueueStore()
    if store_type == "sqlite" and tmp_path is not None:
        return SQLiteAlertPriorityQueueStore(str(tmp_path / "test.db"))
    raise ValueError(f"Unknown store type: {store_type}")


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestAlertPriorityQueueItemModelPhase57:
    """Phase 57: Extended AlertPriorityQueueItem model tests."""

    def test_status_accepts_legacy_values(self):
        """Legacy AlertDeliveryStatus values are accepted."""
        item = _make_item(status="retry_scheduled")
        assert item.status == "retry_scheduled"

    def test_status_accepts_new_queue_statuses(self):
        """New queue statuses are accepted."""
        for status in ("queued", "claimed", "processing", "completed", "failed", "requeued", "cancelled", "expired"):
            item = _make_item(status=status)
            assert item.status == status

    def test_status_rejects_invalid(self):
        """Invalid status raises ValueError."""
        with pytest.raises(ValueError):
            _make_item(status="invalid_status")

    def test_is_claimable_queued(self):
        """A queued item with available_at <= now is claimable."""
        item = _make_item(status="queued", available_at=datetime.now(timezone.utc) - timedelta(seconds=10))
        assert item.is_claimable() is True

    def test_is_claimable_future_available_at(self):
        """A queued item with future available_at is not claimable."""
        item = _make_item(status="queued", available_at=datetime.now(timezone.utc) + timedelta(hours=1))
        assert item.is_claimable() is False

    def test_is_claimable_claimed(self):
        """A claimed item is not claimable."""
        item = _make_item(status="claimed", available_at=datetime.now(timezone.utc) - timedelta(seconds=10))
        assert item.is_claimable() is False

    def test_is_claimable_completed(self):
        """A completed item is not claimable."""
        item = _make_item(status="completed")
        assert item.is_claimable() is False

    def test_is_lease_expired_true(self):
        """is_lease_expired returns True when lease is past."""
        item = _make_item(
            status="claimed",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        assert item.is_lease_expired() is True

    def test_is_lease_expired_false(self):
        """is_lease_expired returns False when lease is active."""
        item = _make_item(
            status="claimed",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
        assert item.is_lease_expired() is False

    def test_is_lease_expired_no_lease(self):
        """is_lease_expired returns False when no lease set."""
        item = _make_item(status="queued")
        assert item.is_lease_expired() is False


# ---------------------------------------------------------------------------
# InMemory atomic lifecycle tests
# ---------------------------------------------------------------------------


class TestInMemoryPriorityQueueAtomicPhase57:
    """Phase 57: InMemory atomic claim/ack/fail/requeue tests."""

    def test_claim_next_claims_highest_priority_first(self):
        """claim_next returns items ordered by priority DESC."""
        store = _make_store("memory")
        low = _make_item("nda_t1_a1_1", priority=10)
        mid = _make_item("nda_t1_a2_1", priority=50)
        high = _make_item("nda_t1_a3_1", priority=90)
        asyncio.run(store.enqueue(low))
        asyncio.run(store.enqueue(mid))
        asyncio.run(store.enqueue(high))

        claimed = asyncio.run(store.claim_next(limit=3))
        assert len(claimed) == 3
        assert claimed[0].attempt_id == "nda_t1_a3_1"
        assert claimed[1].attempt_id == "nda_t1_a2_1"
        assert claimed[2].attempt_id == "nda_t1_a1_1"

    def test_claim_next_marks_items_claimed(self):
        """After claim_next, items have status 'claimed'."""
        store = _make_store("memory")
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=1))
        assert len(claimed) == 1
        assert claimed[0].status == "claimed"
        assert claimed[0].claimed_by is None
        assert claimed[0].claimed_at is not None
        assert claimed[0].lease_expires_at is not None

    def test_claim_next_respects_limit(self):
        """claim_next only claims up to limit items."""
        store = _make_store("memory")
        for i in range(10):
            item = _make_item(f"nda_t1_a{i}_1", priority=i * 10)
            asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=3))
        assert len(claimed) == 3

    def test_second_claim_returns_no_already_claimed(self):
        """Second claim_next does not re-claim already claimed items."""
        store = _make_store("memory")
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store.enqueue(item))
        asyncio.run(store.claim_next(limit=1))
        claimed_again = asyncio.run(store.claim_next(limit=1))
        assert len(claimed_again) == 0

    def test_acknowledge_completes_item(self):
        """acknowledge changes status from claimed to completed."""
        store = _make_store("memory")
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=1))
        result = asyncio.run(store.acknowledge(claimed[0].attempt_id))
        assert result is not None
        assert result.status == "completed"
        assert result.claimed_by is None

    def test_fail_marks_failed(self):
        """fail changes status from claimed to failed with redacted error."""
        store = _make_store("memory")
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=1))
        result = asyncio.run(store.fail(claimed[0].attempt_id, error="Connection timeout with token=abc123"))
        assert result is not None
        assert result.status == "failed"
        assert "token=abc123" not in result.metadata_json

    def test_requeue_returns_item_to_queued(self):
        """requeue changes status from claimed to requeued."""
        store = _make_store("memory")
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=1))
        result = asyncio.run(store.requeue(claimed[0].attempt_id, reason="transient error"))
        assert result is not None
        assert result.status == "requeued"
        assert result.available_at > datetime.now(timezone.utc) - timedelta(seconds=5)

    def test_worker_mismatch_rejected(self):
        """acknowledge/fail reject mismatched worker_id."""
        store = _make_store("memory")
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=1, worker_id="worker-a"))
        # Try ack with wrong worker
        result = asyncio.run(store.acknowledge(claimed[0].attempt_id, worker_id="worker-b"))
        assert result is None
        # Try ack with no worker_id (should work - backward compat)
        result = asyncio.run(store.acknowledge(claimed[0].attempt_id))
        assert result is not None
        assert result.status == "completed"

    def test_lease_expiry_reset(self):
        """reset_expired_leases returns expired items to queued."""
        store = _make_store("memory")
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        item = _make_item(
            "nda_t1_a1_1",
            status="claimed",
            claimed_at=past,
            lease_expires_at=past + timedelta(seconds=5),
            available_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        asyncio.run(store.enqueue(item))
        reset_count = asyncio.run(store.reset_expired_leases())
        assert reset_count == 1
        # Item should be claimable again
        claimed = asyncio.run(store.claim_next(limit=1))
        assert len(claimed) == 1
        assert claimed[0].status == "claimed"

    def test_requeue_increments_attempt(self):
        """requeue increments attempt count."""
        store = _make_store("memory")
        item = _make_item("nda_t1_a1_1", attempt=1)
        asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=1))
        result = asyncio.run(store.requeue(claimed[0].attempt_id))
        assert result is not None
        assert result.attempt == 2


# ---------------------------------------------------------------------------
# SQLite atomic lifecycle tests
# ---------------------------------------------------------------------------


class TestSQLitePriorityQueueAtomicPhase57:
    """Phase 57: SQLite atomic claim/ack/fail/requeue tests."""

    def test_sqlite_claim_atomicity_two_instances(self, tmp_path):
        """Two SQLite store instances cannot claim the same item."""
        store1 = _make_store("sqlite", tmp_path)
        store2 = _make_store("sqlite", tmp_path)
        item = _make_item("nda_t1_a1_1", priority=50)
        asyncio.run(store1.enqueue(item))

        claimed1 = asyncio.run(store1.claim_next(limit=1, worker_id="worker-1"))
        claimed2 = asyncio.run(store2.claim_next(limit=1, worker_id="worker-2"))

        assert len(claimed1) == 1
        assert len(claimed2) == 0
        store1.close()
        store2.close()

    def test_sqlite_acknowledge(self, tmp_path):
        """SQLite acknowledge works correctly."""
        store = _make_store("sqlite", tmp_path)
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=1, worker_id="w1"))
        result = asyncio.run(store.acknowledge(claimed[0].attempt_id, worker_id="w1"))
        assert result is not None
        assert result.status == "completed"
        store.close()

    def test_sqlite_fail(self, tmp_path):
        """SQLite fail marks item as failed."""
        store = _make_store("sqlite", tmp_path)
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=1, worker_id="w1"))
        result = asyncio.run(store.fail(claimed[0].attempt_id, error="timeout", worker_id="w1"))
        assert result is not None
        assert result.status == "failed"
        store.close()

    def test_sqlite_requeue(self, tmp_path):
        """SQLite requeue returns item to queue."""
        store = _make_store("sqlite", tmp_path)
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=1, worker_id="w1"))
        result = asyncio.run(store.requeue(claimed[0].attempt_id, reason="retry"))
        assert result is not None
        assert result.status == "requeued"
        assert result.attempt == 2
        store.close()

    def test_sqlite_reset_expired_leases(self, tmp_path):
        """SQLite reset_expired_leases works."""
        store = _make_store("sqlite", tmp_path)
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        item = _make_item(
            "nda_t1_a1_1",
            status="claimed",
            claimed_at=past,
            lease_expires_at=past + timedelta(seconds=5),
        )
        asyncio.run(store.enqueue(item))
        count = asyncio.run(store.reset_expired_leases())
        assert count == 1
        store.close()

    def test_sqlite_persist_claim_across_reopen(self, tmp_path):
        """Claimed items persist across store reopen."""
        store1 = _make_store("sqlite", tmp_path)
        item = _make_item("nda_t1_a1_1")
        asyncio.run(store1.enqueue(item))
        claimed = asyncio.run(store1.claim_next(limit=1, worker_id="w1"))
        assert len(claimed) == 1
        store1.close()

        store2 = _make_store("sqlite", tmp_path)
        items = asyncio.run(store2.dequeue())
        assert len(items) == 1
        assert items[0].status == "claimed"
        assert items[0].claimed_by == "w1"
        store2.close()


# ---------------------------------------------------------------------------
# Priority ordering tests
# ---------------------------------------------------------------------------


class TestPriorityOrderingPhase57:
    """Phase 57: Priority ordering preservation tests."""

    def test_claim_respects_priority_order(self):
        """Higher priority items are claimed first."""
        store = _make_store("memory")
        items = [
            _make_item(f"nda_t1_a{i}_1", priority=i * 25)
            for i in range(4)
        ]
        for item in items:
            asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=4))
        priorities = [c.priority for c in claimed]
        assert priorities == sorted(priorities, reverse=True)

    def test_requeue_preserves_priority(self):
        """requeue without new priority preserves original."""
        store = _make_store("memory")
        item = _make_item("nda_t1_a1_1", priority=75)
        asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=1))
        result = asyncio.run(store.requeue(claimed[0].attempt_id))
        assert result is not None
        assert result.priority == 75

    def test_requeue_allows_new_priority(self):
        """requeue with new priority updates it."""
        store = _make_store("memory")
        item = _make_item("nda_t1_a1_1", priority=10)
        asyncio.run(store.enqueue(item))
        claimed = asyncio.run(store.claim_next(limit=1))
        result = asyncio.run(store.requeue(claimed[0].attempt_id, priority=99))
        assert result is not None
        assert result.priority == 99


# ---------------------------------------------------------------------------
# Error redaction tests
# ---------------------------------------------------------------------------


class TestErrorRedactionPhase57:
    """Phase 57: Error message redaction tests."""

    def test_redact_token(self):
        """Token values are redacted."""
        result = _redact_error("Auth failed: token=abc123xyz")
        assert "abc123xyz" not in result
        assert "[REDACTED]" in result

    def test_redact_secret(self):
        """Secret values are redacted."""
        result = _redact_error("Invalid secret=supersecretvalue")
        assert "supersecretvalue" not in result

    def test_redact_api_key(self):
        """API key values are redacted."""
        result = _redact_error("api_key=sk-1234567890abcdef")
        assert "sk-1234567890abcdef" not in result

    def test_no_false_positive(self):
        """Normal error messages are not mangled."""
        result = _redact_error("Connection refused to host: port 443")
        assert result == "Connection refused to host: port 443"

    def test_none_input(self):
        """None input returns None."""
        assert _redact_error(None) is None

    def test_empty_input(self):
        """Empty input returns empty."""
        assert _redact_error("") == ""


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestCreatePriorityQueueStorePhase57:
    """Phase 57: Factory function tests."""

    def test_create_memory(self):
        store = create_alert_priority_queue_store("memory")
        assert isinstance(store, InMemoryAlertPriorityQueueStore)

    def test_create_sqlite(self, tmp_path):
        store = create_alert_priority_queue_store("sqlite", str(tmp_path / "test.db"))
        assert isinstance(store, SQLiteAlertPriorityQueueStore)
        store.close()

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            create_alert_priority_queue_store("redis")

    def test_create_sqlite_default_path(self, tmp_path):
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            store = create_alert_priority_queue_store("sqlite")
            assert isinstance(store, SQLiteAlertPriorityQueueStore)
            store.close()
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Protocol compliance tests
# ---------------------------------------------------------------------------


class TestProtocolCompliancePhase57:
    """Phase 57: Verify implementations satisfy the extended Protocol."""

    def test_memory_satisfies_protocol(self):
        store = _make_store("memory")
        assert isinstance(store, AlertPriorityQueueStore)
        # Phase 57 methods
        assert hasattr(store, "claim_next")
        assert hasattr(store, "acknowledge")
        assert hasattr(store, "fail")
        assert hasattr(store, "requeue")
        assert hasattr(store, "reset_expired_leases")

    def test_sqlite_satisfies_protocol(self, tmp_path):
        store = _make_store("sqlite", tmp_path)
        assert isinstance(store, AlertPriorityQueueStore)
        assert hasattr(store, "claim_next")
        assert hasattr(store, "acknowledge")
        assert hasattr(store, "fail")
        assert hasattr(store, "requeue")
        assert hasattr(store, "reset_expired_leases")
        store.close()


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestEdgeCasesPhase57:
    """Phase 57: Edge case tests."""

    def test_acknowledge_nonexistent_returns_none(self):
        store = _make_store("memory")
        result = asyncio.run(store.acknowledge("nda_nonexistent"))
        assert result is None

    def test_fail_nonexistent_returns_none(self):
        store = _make_store("memory")
        result = asyncio.run(store.fail("nda_nonexistent"))
        assert result is None

    def test_requeue_nonexistent_returns_none(self):
        store = _make_store("memory")
        result = asyncio.run(store.requeue("nda_nonexistent"))
        assert result is None

    def test_claim_next_empty_store(self):
        store = _make_store("memory")
        claimed = asyncio.run(store.claim_next())
        assert claimed == []

    def test_claim_only_queued_items(self):
        """claim_next only claims queued/requeued items, not completed/failed."""
        store = _make_store("memory")
        asyncio.run(store.enqueue(_make_item("nda_a1", status="completed")))
        asyncio.run(store.enqueue(_make_item("nda_a2", status="failed")))
        claimed = asyncio.run(store.claim_next())
        assert len(claimed) == 0

    def test_requeue_only_from_valid_states(self):
        """requeue only works from claimed/failed/expired, not from completed."""
        store = _make_store("memory")
        item = _make_item("nda_a1", status="completed")
        asyncio.run(store.enqueue(item))
        result = asyncio.run(store.requeue("nda_a1"))
        assert result is None

    def test_acknowledge_only_from_valid_states(self):
        """acknowledge only works from claimed/processing, not from completed."""
        store = _make_store("memory")
        item = _make_item("nda_a1", status="queued")
        asyncio.run(store.enqueue(item))
        result = asyncio.run(store.acknowledge("nda_a1"))
        assert result is None
