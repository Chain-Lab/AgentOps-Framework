"""Tests for Redis priority queue store (Phase 59 Task 732).

Uses fakeredis for offline testing — no real Redis server required.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue_store import (
    AlertPriorityQueueItem,
    AlertPriorityQueueItemStatus,
    _redact_error,
)
from agent_app.runtime.policy_rollout_federation_notification_redis_priority_queue import (
    RedisAlertPriorityQueueStore,
    create_redis_alert_priority_queue_store,
    _make_keys,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(attempt_id: str = "nda_t1_a1_1", priority: int = 50, **kwargs) -> AlertPriorityQueueItem:
    defaults = dict(
        attempt_id=attempt_id,
        alert_id="a1",
        target_id="t1",
        channel_type=AlertDeliveryChannelType.WEBHOOK,
        status="queued",
        priority=priority,
        created_at=datetime.now(timezone.utc),
        available_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    defaults.update(kwargs)
    return AlertPriorityQueueItem(**defaults)


# ---------------------------------------------------------------------------
# Import guard test
# ---------------------------------------------------------------------------


class TestRedisImportGuard:
    """Redis dependency availability tests."""

    def test_missing_redis_dependency_error(self):
        """When redis is not installed, store raises clear error."""
        import agent_app.runtime.policy_rollout_federation_notification_redis_priority_queue as mod
        original = mod._REDIS_AVAILABLE
        try:
            mod._REDIS_AVAILABLE = False
            with pytest.raises(ImportError, match="pip install 'agent-app-framework\\[redis\\]'"):
                RedisAlertPriorityQueueStore()
        finally:
            mod._REDIS_AVAILABLE = original


# ---------------------------------------------------------------------------
# fakeredis-based tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def redis_store():
    """Create a Redis store backed by fakeredis."""
    try:
        import fakeredis
    except ImportError:
        pytest.skip("fakeredis not installed")
    client = fakeredis.FakeRedis()
    store = RedisAlertPriorityQueueStore(
        redis_url="redis://localhost:6379/0",
        key_prefix="agentapp:test:pq",
        queue_name="testq",
        queue_id="q1",
    )
    # Replace real client with fake
    store._client = client
    store._keys = _make_keys("agentapp:test:pq", "testq", "q1")
    return store


class TestRedisPriorityQueueBasic:
    """Basic CRUD operations on Redis priority queue."""

    def test_enqueue_and_dequeue(self, redis_store):
        """Enqueue then dequeue returns the item."""
        item = _make_item(attempt_id="nda_t1_a1_1", priority=50)
        import asyncio
        asyncio.run(redis_store.enqueue(item))
        items = asyncio.run(redis_store.dequeue(limit=10))
        assert len(items) == 1
        assert items[0].attempt_id == "nda_t1_a1_1"
        assert items[0].priority == 50

    def test_count_empty(self, redis_store):
        """Count returns 0 for empty store."""
        import asyncio
        assert asyncio.run(redis_store.count()) == 0

    def test_count_by_status(self, redis_store):
        """Count by specific status."""
        import asyncio
        asyncio.run(redis_store.enqueue(_make_item("nda_1", priority=10)))
        asyncio.run(redis_store.enqueue(_make_item("nda_2", priority=20)))
        assert asyncio.run(redis_store.count("queued")) == 2

    def test_count_by_priority(self, redis_store):
        """Count by priority distribution."""
        import asyncio
        asyncio.run(redis_store.enqueue(_make_item("nda_1", priority=10)))
        asyncio.run(redis_store.enqueue(_make_item("nda_2", priority=20)))
        asyncio.run(redis_store.enqueue(_make_item("nda_3", priority=10)))
        counts = asyncio.run(redis_store.count_by_priority("queued"))
        assert counts[10] == 2
        assert counts[20] == 1

    def test_remove_item(self, redis_store):
        """Remove deletes item from all indexes."""
        import asyncio
        asyncio.run(redis_store.enqueue(_make_item("nda_1")))
        result = asyncio.run(redis_store.remove("nda_1"))
        assert result is True
        assert asyncio.run(redis_store.count()) == 0

    def test_remove_nonexistent(self, redis_store):
        """Remove nonexistent returns False."""
        import asyncio
        result = asyncio.run(redis_store.remove("nda_nonexistent"))
        assert result is False


class TestRedisPriorityQueueClaim:
    """Atomic claim tests with fakeredis."""

    def test_claim_highest_priority(self, redis_store):
        """Claim selects highest priority item first."""
        import asyncio
        asyncio.run(redis_store.enqueue(_make_item("nda_1", priority=10)))
        asyncio.run(redis_store.enqueue(_make_item("nda_2", priority=50)))
        asyncio.run(redis_store.enqueue(_make_item("nda_3", priority=30)))
        claimed = asyncio.run(redis_store.claim_next(worker_id="w1", limit=3))
        assert len(claimed) == 3
        # Highest priority first
        assert claimed[0].attempt_id == "nda_2"
        assert claimed[0].priority == 50
        assert claimed[1].attempt_id == "nda_3"
        assert claimed[2].attempt_id == "nda_1"

    def test_two_workers_no_duplicate_claim(self, redis_store):
        """Two workers cannot claim the same item."""
        import asyncio
        asyncio.run(redis_store.enqueue(_make_item("nda_1", priority=50)))
        w1_claims = asyncio.run(redis_store.claim_next(worker_id="w1", limit=1))
        w2_claims = asyncio.run(redis_store.claim_next(worker_id="w2", limit=1))
        assert len(w1_claims) == 1
        assert len(w2_claims) == 0

    def test_claim_respects_available_at(self, redis_store):
        """Items with future available_at are not claimable."""
        import asyncio
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        asyncio.run(redis_store.enqueue(_make_item("nda_1", priority=50, available_at=future)))
        asyncio.run(redis_store.enqueue(_make_item("nda_2", priority=30)))
        claimed = asyncio.run(redis_store.claim_next(worker_id="w1", limit=3))
        assert len(claimed) == 1
        assert claimed[0].attempt_id == "nda_2"

    def test_claim_updates_status_to_claimed(self, redis_store):
        """Claimed items have status 'claimed'."""
        import asyncio
        asyncio.run(redis_store.enqueue(_make_item("nda_1")))
        claimed = asyncio.run(redis_store.claim_next(worker_id="w1", limit=1))
        assert len(claimed) == 1
        assert claimed[0].status == "claimed"
        assert claimed[0].claimed_by == "w1"


class TestRedisPriorityQueueLifecycle:
    """acknowledge, fail, requeue lifecycle tests."""

    def test_acknowledge_completes_item(self, redis_store):
        """Acknowledge marks item as completed."""
        import asyncio
        asyncio.run(redis_store.enqueue(_make_item("nda_1")))
        claimed = asyncio.run(redis_store.claim_next(worker_id="w1", limit=1))
        result = asyncio.run(redis_store.acknowledge("nda_1", worker_id="w1"))
        assert result is not None
        assert result.status == "completed"

    def test_fail_marks_failed(self, redis_store):
        """Fail marks item as failed with redacted error."""
        import asyncio
        asyncio.run(redis_store.enqueue(_make_item("nda_1")))
        asyncio.run(redis_store.claim_next(worker_id="w1", limit=1))
        result = asyncio.run(redis_store.fail("nda_1", error="token=secret123", worker_id="w1"))
        assert result is not None
        assert result.status == "failed"
        metadata = json.loads(result.metadata_json)
        assert metadata.get("last_error") == "token=[REDACTED]"

    def test_requeue_returns_to_queue(self, redis_store):
        """Requeue returns item to requeued status."""
        import asyncio
        asyncio.run(redis_store.enqueue(_make_item("nda_1")))
        asyncio.run(redis_store.claim_next(worker_id="w1", limit=1))
        result = asyncio.run(redis_store.requeue("nda_1", reason="retry", worker_id="w1"))
        assert result is not None
        assert result.status == "requeued"
        metadata = json.loads(result.metadata_json)
        assert metadata.get("requeue_count") == 1


class TestRedisPriorityQueueLease:
    """Lease expiry tests."""

    def test_reset_expired_leases(self, redis_store):
        """Expired leases are reset to queued."""
        import asyncio
        past = datetime.now(timezone.utc) - timedelta(seconds=30)
        item = _make_item("nda_1", claimed_at=past, lease_expires_at=past + timedelta(seconds=5))
        item.status = "claimed"
        asyncio.run(redis_store.enqueue(item))
        # Also add to claimed sorted set with expired lease score
        redis_store._client.zadd(
            redis_store._keys["claimed"],
            {"nda_1": (past + timedelta(seconds=5)).timestamp()},
        )
        reset = asyncio.run(redis_store.reset_expired_leases(limit=10))
        assert reset == 1
        # Item should be claimable again
        claimed = asyncio.run(redis_store.claim_next(worker_id="w1", limit=1))
        assert len(claimed) == 1


class TestRedisPriorityQueueIsolation:
    """Key prefix and queue isolation tests."""

    def test_key_prefix_isolation(self):
        """Different key prefixes have separate queues."""
        try:
            import fakeredis
        except ImportError:
            pytest.skip("fakeredis not installed")
        client = fakeredis.FakeRedis()
        store_a = RedisAlertPriorityQueueStore(
            redis_url="redis://localhost:6379/0",
            key_prefix="app:a",
            queue_name="q",
            queue_id="1",
        )
        store_b = RedisAlertPriorityQueueStore(
            redis_url="redis://localhost:6379/0",
            key_prefix="app:b",
            queue_name="q",
            queue_id="1",
        )
        store_a._client = client
        store_b._client = client
        store_a._keys = _make_keys("app:a", "q", "1")
        store_b._keys = _make_keys("app:b", "q", "1")

        import asyncio
        asyncio.run(store_a.enqueue(_make_item("nda_a1", priority=50)))
        asyncio.run(store_b.enqueue(_make_item("nda_b1", priority=50)))

        items_a = asyncio.run(store_a.dequeue())
        items_b = asyncio.run(store_b.dequeue())
        assert len(items_a) == 1 and items_a[0].attempt_id == "nda_a1"
        assert len(items_b) == 1 and items_b[0].attempt_id == "nda_b1"


class TestRedisPriorityQueueRedaction:
    """Metadata and error redaction tests."""

    def test_metadata_redacted_in_fail(self, redis_store):
        """Fail stores redacted error in metadata."""
        import asyncio
        asyncio.run(redis_store.enqueue(_make_item("nda_1")))
        asyncio.run(redis_store.claim_next(worker_id="w1", limit=1))
        result = asyncio.run(redis_store.fail("nda_1", error="api_key=sk-12345", worker_id="w1"))
        assert result is not None
        metadata = json.loads(result.metadata_json)
        assert "api_key=[REDACTED]" in metadata.get("last_error", "")

    def test_requeue_reason_redacted(self, redis_store):
        """Requeue reason is redacted."""
        import asyncio
        asyncio.run(redis_store.enqueue(_make_item("nda_1")))
        asyncio.run(redis_store.claim_next(worker_id="w1", limit=1))
        result = asyncio.run(redis_store.requeue("nda_1", reason="secret=xyz", worker_id="w1"))
        assert result is not None
        metadata = json.loads(result.metadata_json)
        assert "secret=[REDACTED]" in metadata.get("last_requeue_reason", "")


class TestRedisPriorityQueueSerialization:
    """Serialization roundtrip tests."""

    def test_roundtrip_preserves_fields(self, redis_store):
        """All fields survive Redis roundtrip."""
        import asyncio
        item = _make_item(
            "nda_rt_1",
            priority=75,
            next_retry_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            attempt=3,
        )
        asyncio.run(redis_store.enqueue(item))
        items = asyncio.run(redis_store.dequeue())
        assert len(items) == 1
        rt = items[0]
        assert rt.attempt_id == "nda_rt_1"
        assert rt.priority == 75
        assert rt.attempt == 3
        assert rt.channel_type == AlertDeliveryChannelType.WEBHOOK
