"""Tests for Phase 55 Task 5 — Alert Priority Queue."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryAttempt,
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    severity_to_priority,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_store import (
    InMemoryAlertDeliveryStore,
    SQLiteAlertDeliveryStore,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_priority_queue import (
    AlertPriorityQueue,
)
from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationAlertEvent,
)


def _make_alert(
    alert_id: str = "nae_001",
    severity: str = "warning",
    name: str = "Test Alert",
    metric: str = "cpu_usage",
    observed_value: float = 95.0,
    threshold: float = 90.0,
) -> NotificationAlertEvent:
    now = datetime.now(timezone.utc)
    return NotificationAlertEvent(
        alert_id=alert_id,
        rule_id="rule_001",
        name=name,
        severity=severity,
        metric=metric,
        observed_value=observed_value,
        threshold=threshold,
        message=f"{name}: {metric} is {observed_value}",
        status="open",
        created_at=now,
    )


def _make_attempt(
    alert_id: str = "nae_001",
    target_id: str = "ndt_001",
    priority: int = 50,
    status: str = AlertDeliveryStatus.RETRY_SCHEDULED.value,
    attempt_num: int = 1,
    created_at: datetime | None = None,
    attempt_id: str | None = None,
) -> AlertDeliveryAttempt:
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    if attempt_id is None:
        attempt_id = f"nda_{target_id}_{alert_id}_{attempt_num}"
    return AlertDeliveryAttempt(
        attempt_id=attempt_id,
        alert_id=alert_id,
        target_id=target_id,
        channel_type=AlertDeliveryChannelType.WEBHOOK,
        status=AlertDeliveryStatus(status),
        attempt=attempt_num,
        priority=priority,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# severity_to_priority helper
# ---------------------------------------------------------------------------


class TestSeverityToPriority:
    def test_critical_maps_to_100(self):
        assert severity_to_priority("critical") == 100

    def test_error_maps_to_75(self):
        assert severity_to_priority("error") == 75

    def test_warning_maps_to_50(self):
        assert severity_to_priority("warning") == 50

    def test_info_maps_to_25(self):
        assert severity_to_priority("info") == 25

    def test_unknown_defaults_to_0(self):
        assert severity_to_priority("unknown") == 0

    def test_case_insensitive(self):
        assert severity_to_priority("CRITICAL") == 100
        assert severity_to_priority("Warning") == 50

    def test_strips_whitespace(self):
        assert severity_to_priority("  critical  ") == 100

    def test_empty_string_defaults_to_0(self):
        assert severity_to_priority("") == 0


# ---------------------------------------------------------------------------
# InMemory priority ordering
# ---------------------------------------------------------------------------


class TestInMemoryPriorityOrdering:
    @pytest.mark.asyncio
    async def test_highest_priority_first(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        low = _make_attempt(priority=10, alert_id="nae_low", attempt_id="nda_low_001")
        high = _make_attempt(priority=90, alert_id="nae_high", attempt_id="nda_high_001",
                              created_at=datetime.now(timezone.utc) - timedelta(seconds=10))
        await queue.enqueue(low)
        await queue.enqueue(high)

        results = await queue.dequeue()
        assert len(results) == 2
        assert results[0].priority == 90  # highest first
        assert results[1].priority == 10

    @pytest.mark.asyncio
    async def test_same_priority_ordered_by_created_at(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        older = _make_attempt(priority=50, alert_id="nae_old", attempt_id="nda_old_001",
                               created_at=datetime.now(timezone.utc) - timedelta(seconds=10))
        newer = _make_attempt(priority=50, alert_id="nae_new", attempt_id="nda_new_001",
                               created_at=datetime.now(timezone.utc))
        await queue.enqueue(older)
        await queue.enqueue(newer)

        results = await queue.dequeue()
        assert results[0].attempt_id == "nda_new_001"  # newest first within same priority
        assert results[1].attempt_id == "nda_old_001"

    @pytest.mark.asyncio
    async def test_mixed_priority_and_timestamp(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        # Low priority, old
        a1 = _make_attempt(priority=10, alert_id="nae_1", attempt_id="nda_1",
                            created_at=datetime.now(timezone.utc) - timedelta(hours=1))
        # High priority, very old
        a2 = _make_attempt(priority=90, alert_id="nae_2", attempt_id="nda_2",
                            created_at=datetime.now(timezone.utc) - timedelta(hours=2))
        # Medium priority, new
        a3 = _make_attempt(priority=50, alert_id="nae_3", attempt_id="nda_3",
                            created_at=datetime.now(timezone.utc))

        await queue.enqueue(a1)
        await queue.enqueue(a2)
        await queue.enqueue(a3)

        results = await queue.dequeue()
        assert results[0].priority == 90  # highest priority wins regardless of age
        assert results[1].priority == 50
        assert results[2].priority == 10


# ---------------------------------------------------------------------------
# AlertPriorityQueue operations
# ---------------------------------------------------------------------------


class TestAlertPriorityQueue:
    @pytest.mark.asyncio
    async def test_peek_returns_highest(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        low = _make_attempt(priority=10, alert_id="nae_low", attempt_id="nda_low_001")
        high = _make_attempt(priority=90, alert_id="nae_high", attempt_id="nda_high_001")
        await queue.enqueue(low)
        await queue.enqueue(high)

        top = await queue.peek()
        assert top is not None
        assert top.priority == 90

    @pytest.mark.asyncio
    async def test_peek_empty_returns_none(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)
        assert await queue.peek() is None

    @pytest.mark.asyncio
    async def test_count_all(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        for i in range(3):
            await queue.enqueue(_make_attempt(alert_id=f"nae_{i}", priority=i * 25))

        assert await queue.count() == 3

    @pytest.mark.asyncio
    async def test_count_by_priority(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        await queue.enqueue(_make_attempt(priority=100, alert_id="nae_1", attempt_id="nda_1"))
        await queue.enqueue(_make_attempt(priority=100, alert_id="nae_2", attempt_id="nda_2"))
        await queue.enqueue(_make_attempt(priority=50, alert_id="nae_3", attempt_id="nda_3"))

        counts = await queue.count_by_priority()
        assert counts[100] == 2
        assert counts[50] == 1

    @pytest.mark.asyncio
    async def test_dequeue_with_limit(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        for i in range(5):
            await queue.enqueue(_make_attempt(alert_id=f"nae_{i}", priority=i * 10))

        results = await queue.dequeue(limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_dequeue_filters_by_status(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        await queue.enqueue(_make_attempt(status=AlertDeliveryStatus.RETRY_SCHEDULED.value, alert_id="nae_1", attempt_id="nda_1"))
        await queue.enqueue(_make_attempt(status=AlertDeliveryStatus.DELIVERED.value, alert_id="nae_2", attempt_id="nda_2"))
        await queue.enqueue(_make_attempt(status=AlertDeliveryStatus.RETRY_SCHEDULED.value, priority=100, alert_id="nae_3", attempt_id="nda_3"))

        results = await queue.dequeue(status=AlertDeliveryStatus.RETRY_SCHEDULED.value)
        assert len(results) == 2
        assert all(a.status == AlertDeliveryStatus.RETRY_SCHEDULED for a in results)

    @pytest.mark.asyncio
    async def test_enqueue_from_alert_sets_priority(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        alert = _make_alert(severity="critical")
        attempt = await queue.enqueue_from_alert(
            alert=alert,
            target_id="ndt_001",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
        )
        assert attempt.priority == 100

    @pytest.mark.asyncio
    async def test_enqueue_from_alert_warning_severity(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        alert = _make_alert(severity="warning")
        attempt = await queue.enqueue_from_alert(
            alert=alert,
            target_id="ndt_001",
            channel_type=AlertDeliveryChannelType.EMAIL,
        )
        assert attempt.priority == 50

    @pytest.mark.asyncio
    async def test_enqueue_from_alert_unknown_severity(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        alert = _make_alert(severity="unknown_level")
        attempt = await queue.enqueue_from_alert(
            alert=alert,
            target_id="ndt_001",
            channel_type=AlertDeliveryChannelType.CONSOLE,
        )
        assert attempt.priority == 0

    @pytest.mark.asyncio
    async def test_enqueue_preserves_existing_priority(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        attempt = _make_attempt(priority=42)
        result = await queue.enqueue(attempt)
        assert result.priority == 42

    @pytest.mark.asyncio
    async def test_dequeue_with_limit_zero(self):
        store = InMemoryAlertDeliveryStore()
        queue = AlertPriorityQueue(store)

        await queue.enqueue(_make_attempt())
        results = await queue.dequeue(limit=0)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# SQLite priority ordering
# ---------------------------------------------------------------------------


class TestSQLitePriorityOrdering:
    @pytest.fixture
    def tmp_db(self, tmp_path):
        db = str(tmp_path / "priority.db")
        yield db

    @pytest.mark.asyncio
    async def test_persisted_priority_ordering(self, tmp_db):
        store = SQLiteAlertDeliveryStore(tmp_db)
        queue = AlertPriorityQueue(store)

        low = _make_attempt(priority=10, attempt_id="nda_low_001")
        high = _make_attempt(priority=90, attempt_id="nda_high_001")
        await queue.enqueue(low)
        await queue.enqueue(high)
        store.close()

        # Re-open and verify ordering
        store2 = SQLiteAlertDeliveryStore(tmp_db)
        queue2 = AlertPriorityQueue(store2)
        results = await queue2.dequeue()
        assert results[0].priority == 90
        assert results[1].priority == 10
        store2.close()

    @pytest.mark.asyncio
    async def test_sqlite_default_priority_is_zero(self, tmp_db):
        store = SQLiteAlertDeliveryStore(tmp_db)
        # Attempt with explicit priority=0
        attempt = _make_attempt(priority=0)
        await store.record_attempt(attempt)
        fetched = await store.get_attempt(attempt.attempt_id)
        assert fetched is not None
        assert fetched.priority == 0
        store.close()
