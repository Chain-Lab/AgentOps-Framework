"""Tests for NotificationObservabilityStore — InMemory, SQLite, and factory."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationDeliveryEvent,
    NotificationDeliveryEventType,
)
from agent_app.runtime.policy_rollout_federation_notification_observability_store import (
    InMemoryNotificationObservabilityStore,
    NotificationObservabilityStore,
    SQLiteNotificationObservabilityStore,
    create_notification_observability_store,
)


def _now(offset_seconds: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)


def _make_event(**overrides) -> NotificationDeliveryEvent:
    now = _now()
    defaults = dict(
        event_id=f"nde_{uuid.uuid4().hex[:12]}",
        notification_id="fn_001",
        approval_id="fap_001",
        federation_id="fed_a",
        channel="webhook",
        event_type=NotificationDeliveryEventType.SENT,
        status="delivered",
        attempt=1,
        latency_ms=150,
        error_code=None,
        error_message=None,
        adapter_name="webhook_adapter",
        template_id="fnt_001",
        preference_decision="send",
        metadata={},
        created_at=now,
    )
    defaults.update(overrides)
    return NotificationDeliveryEvent(**defaults)


# ---------------------------------------------------------------------------
# InMemoryNotificationObservabilityStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInMemoryNotificationObservabilityStore:
    async def test_record_and_get_event(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        event = _make_event(event_id="nde_001")
        result = await store.record_event(event)
        assert result == event
        loaded = await store.get_event("nde_001")
        assert loaded == event

    async def test_get_nonexistent_returns_none(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        assert await store.get_event("nde_missing") is None

    async def test_list_by_notification_id(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        e1 = _make_event(event_id="nde_001", notification_id="fn_001", created_at=_now(0))
        e2 = _make_event(event_id="nde_002", notification_id="fn_002", created_at=_now(10))
        e3 = _make_event(event_id="nde_003", notification_id="fn_001", created_at=_now(20))
        await store.record_event(e1)
        await store.record_event(e2)
        await store.record_event(e3)

        result = await store.list_events(notification_id="fn_001")
        assert len(result) == 2
        assert result[0].event_id == "nde_003"  # newest first
        assert result[1].event_id == "nde_001"

    async def test_list_by_federation_id(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        e1 = _make_event(event_id="nde_001", federation_id="fed_a", created_at=_now(0))
        e2 = _make_event(event_id="nde_002", federation_id="fed_b", created_at=_now(10))
        e3 = _make_event(event_id="nde_003", federation_id="fed_a", created_at=_now(20))
        await store.record_event(e1)
        await store.record_event(e2)
        await store.record_event(e3)

        result = await store.list_events(federation_id="fed_a")
        assert len(result) == 2
        assert result[0].event_id == "nde_003"
        assert result[1].event_id == "nde_001"

    async def test_list_by_channel(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        e1 = _make_event(event_id="nde_001", channel="webhook", created_at=_now(0))
        e2 = _make_event(event_id="nde_002", channel="email", created_at=_now(10))
        e3 = _make_event(event_id="nde_003", channel="webhook", created_at=_now(20))
        await store.record_event(e1)
        await store.record_event(e2)
        await store.record_event(e3)

        result = await store.list_events(channel="webhook")
        assert len(result) == 2
        assert result[0].event_id == "nde_003"
        assert result[1].event_id == "nde_001"

    async def test_list_by_event_type(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        e1 = _make_event(
            event_id="nde_001",
            event_type=NotificationDeliveryEventType.SENT,
            created_at=_now(0),
        )
        e2 = _make_event(
            event_id="nde_002",
            event_type=NotificationDeliveryEventType.FAILED,
            created_at=_now(10),
        )
        e3 = _make_event(
            event_id="nde_003",
            event_type=NotificationDeliveryEventType.SENT,
            created_at=_now(20),
        )
        await store.record_event(e1)
        await store.record_event(e2)
        await store.record_event(e3)

        result = await store.list_events(event_type="sent")
        assert len(result) == 2
        assert result[0].event_id == "nde_003"
        assert result[1].event_id == "nde_001"

    async def test_list_by_time_range(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        e1 = _make_event(event_id="nde_001", created_at=_now(0))
        e2 = _make_event(event_id="nde_002", created_at=_now(300))
        e3 = _make_event(event_id="nde_003", created_at=_now(600))
        await store.record_event(e1)
        await store.record_event(e2)
        await store.record_event(e3)

        since = _now(60)
        until = _now(500)
        result = await store.list_events(since=since, until=until)
        assert len(result) == 1
        assert result[0].event_id == "nde_002"

    async def test_pagination(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        for i in range(5):
            await store.record_event(
                _make_event(event_id=f"nde_{i:03d}", created_at=_now(i))
            )

        # Limit
        result = await store.list_events(limit=2)
        assert len(result) == 2
        assert result[0].event_id == "nde_004"  # newest first
        assert result[1].event_id == "nde_003"

        # Offset
        result = await store.list_events(offset=2)
        assert len(result) == 3
        assert result[0].event_id == "nde_002"

    async def test_aggregate_metrics_empty_returns_zeros(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        result = await store.aggregate_metrics(window_minutes=60)
        assert result.total == 0
        assert result.sent == 0
        assert result.failed == 0
        assert result.suppressed == 0
        assert result.dlq == 0
        assert result.retry_scheduled == 0
        assert result.success_rate == 0.0
        assert result.failure_rate == 0.0
        assert result.dlq_rate == 0.0
        assert result.avg_latency_ms is None
        assert result.p95_latency_ms is None

    async def test_aggregate_metrics_calculates_rates(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        now = _now()
        await store.record_event(
            _make_event(
                event_id="nde_001",
                event_type=NotificationDeliveryEventType.SENT,
                federation_id="fed_a",
                channel="webhook",
                latency_ms=100,
                created_at=now,
            )
        )
        await store.record_event(
            _make_event(
                event_id="nde_002",
                event_type=NotificationDeliveryEventType.SENT,
                federation_id="fed_a",
                channel="webhook",
                latency_ms=200,
                created_at=now,
            )
        )
        await store.record_event(
            _make_event(
                event_id="nde_003",
                event_type=NotificationDeliveryEventType.FAILED,
                federation_id="fed_a",
                channel="webhook",
                latency_ms=50,
                created_at=now,
            )
        )

        result = await store.aggregate_metrics(
            federation_id="fed_a", channel="webhook", window_minutes=60, now=now
        )
        assert result.total == 3
        assert result.sent == 2
        assert result.failed == 1
        assert result.suppressed == 0
        assert result.dlq == 0
        assert result.retry_scheduled == 0
        assert result.success_rate == 2 / 3
        assert result.failure_rate == 1 / 3
        assert result.dlq_rate == 0.0

    async def test_aggregate_metrics_calculates_latency(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        now = _now()
        latencies = [100, 200, 300, 400, 500]
        for i, lat in enumerate(latencies):
            await store.record_event(
                _make_event(
                    event_id=f"nde_{i:03d}",
                    event_type=NotificationDeliveryEventType.SENT,
                    latency_ms=lat,
                    created_at=now,
                )
            )

        result = await store.aggregate_metrics(window_minutes=60, now=now)
        assert result.avg_latency_ms == 300.0
        # p95 of [100, 200, 300, 400, 500]:
        # idx = ceil(5 * 0.95) - 1 = ceil(4.75) - 1 = 5 - 1 = 4
        assert result.p95_latency_ms == 500

    async def test_aggregate_metrics_with_filters(self) -> None:
        store = InMemoryNotificationObservabilityStore()
        now = _now()
        await store.record_event(
            _make_event(
                event_id="nde_001",
                federation_id="fed_a",
                channel="webhook",
                event_type=NotificationDeliveryEventType.SENT,
                created_at=now,
            )
        )
        await store.record_event(
            _make_event(
                event_id="nde_002",
                federation_id="fed_b",
                channel="webhook",
                event_type=NotificationDeliveryEventType.SENT,
                created_at=now,
            )
        )
        await store.record_event(
            _make_event(
                event_id="nde_003",
                federation_id="fed_a",
                channel="email",
                event_type=NotificationDeliveryEventType.FAILED,
                created_at=now,
            )
        )

        # Filter by federation only
        result = await store.aggregate_metrics(
            federation_id="fed_a", window_minutes=60, now=now
        )
        assert result.total == 2
        assert result.sent == 1
        assert result.failed == 1

        # Filter by channel only
        result = await store.aggregate_metrics(
            channel="webhook", window_minutes=60, now=now
        )
        assert result.total == 2
        assert result.sent == 2


# ---------------------------------------------------------------------------
# SQLiteNotificationObservabilityStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSQLiteNotificationObservabilityStore:
    async def test_record_and_get_event(self, tmp_path: Path) -> None:
        db_path = tmp_path / "observability.db"
        store = SQLiteNotificationObservabilityStore(str(db_path))
        event = _make_event(event_id="nde_001")
        await store.record_event(event)

        result = await store.get_event("nde_001")
        assert result is not None
        assert result.event_id == "nde_001"
        assert result.notification_id == "fn_001"
        assert result.approval_id == "fap_001"
        assert result.federation_id == "fed_a"
        assert result.channel == "webhook"
        assert result.event_type == NotificationDeliveryEventType.SENT
        assert result.status == "delivered"
        assert result.attempt == 1
        assert result.latency_ms == 150
        assert result.adapter_name == "webhook_adapter"
        assert result.template_id == "fnt_001"
        assert result.preference_decision == "send"
        assert result.metadata == {}
        assert result.created_at is not None
        store.close()

    async def test_list_events_with_filters(self, tmp_path: Path) -> None:
        db_path = tmp_path / "observability.db"
        store = SQLiteNotificationObservabilityStore(str(db_path))
        now = _now()
        e1 = _make_event(
            event_id="nde_001",
            notification_id="fn_001",
            federation_id="fed_a",
            channel="webhook",
            event_type=NotificationDeliveryEventType.SENT,
            created_at=now,
        )
        e2 = _make_event(
            event_id="nde_002",
            notification_id="fn_002",
            federation_id="fed_b",
            channel="email",
            event_type=NotificationDeliveryEventType.FAILED,
            created_at=_now(10),
        )
        e3 = _make_event(
            event_id="nde_003",
            notification_id="fn_001",
            federation_id="fed_a",
            channel="webhook",
            event_type=NotificationDeliveryEventType.SENT,
            created_at=_now(20),
        )
        await store.record_event(e1)
        await store.record_event(e2)
        await store.record_event(e3)

        # Filter by notification_id
        result = await store.list_events(notification_id="fn_001")
        assert len(result) == 2
        assert result[0].event_id == "nde_003"  # newest first

        # Filter by federation_id
        result = await store.list_events(federation_id="fed_a")
        assert len(result) == 2

        # Filter by channel
        result = await store.list_events(channel="webhook")
        assert len(result) == 2

        # Filter by event_type
        result = await store.list_events(event_type="sent")
        assert len(result) == 2

        # Filter by time range
        since = _now(5)
        until = _now(15)
        result = await store.list_events(since=since, until=until)
        assert len(result) == 1
        assert result[0].event_id == "nde_002"

        store.close()

    async def test_aggregate_metrics(self, tmp_path: Path) -> None:
        db_path = tmp_path / "observability.db"
        store = SQLiteNotificationObservabilityStore(str(db_path))
        now = _now()
        await store.record_event(
            _make_event(
                event_id="nde_001",
                federation_id="fed_a",
                channel="webhook",
                event_type=NotificationDeliveryEventType.SENT,
                latency_ms=100,
                created_at=now,
            )
        )
        await store.record_event(
            _make_event(
                event_id="nde_002",
                federation_id="fed_a",
                channel="webhook",
                event_type=NotificationDeliveryEventType.SENT,
                latency_ms=200,
                created_at=now,
            )
        )
        await store.record_event(
            _make_event(
                event_id="nde_003",
                federation_id="fed_a",
                channel="webhook",
                event_type=NotificationDeliveryEventType.FAILED,
                latency_ms=50,
                created_at=now,
            )
        )

        result = await store.aggregate_metrics(
            federation_id="fed_a", channel="webhook", window_minutes=60, now=now
        )
        assert result.total == 3
        assert result.sent == 2
        assert result.failed == 1
        assert result.success_rate == 2 / 3
        assert result.failure_rate == 1 / 3
        assert result.avg_latency_ms == (100 + 200 + 50) / 3
        # p95 of [50, 100, 200]: idx = ceil(3*0.95)-1 = ceil(2.85)-1 = 3-1 = 2
        assert result.p95_latency_ms == 200
        store.close()

    async def test_persists_across_instances(self, tmp_path: Path) -> None:
        db_path = tmp_path / "observability.db"
        store1 = SQLiteNotificationObservabilityStore(str(db_path))
        event = _make_event(
            event_id="nde_001",
            notification_id="fn_persist",
            federation_id="fed_persist",
            channel="email",
            event_type=NotificationDeliveryEventType.SENT,
            status="delivered",
            attempt=2,
            latency_ms=250,
            error_code=None,
            error_message=None,
            adapter_name="smtp_adapter",
            template_id="fnt_persist",
            preference_decision="send",
            metadata={"priority": "high", "tags": ["urgent", "review"]},
            created_at=_now(),
        )
        await store1.record_event(event)
        store1.close()

        # Reopen same DB
        store2 = SQLiteNotificationObservabilityStore(str(db_path))
        loaded = await store2.get_event("nde_001")

        assert loaded is not None
        assert loaded.event_id == "nde_001"
        assert loaded.notification_id == "fn_persist"
        assert loaded.federation_id == "fed_persist"
        assert loaded.channel == "email"
        assert loaded.event_type == NotificationDeliveryEventType.SENT
        assert loaded.status == "delivered"
        assert loaded.attempt == 2
        assert loaded.latency_ms == 250
        assert loaded.adapter_name == "smtp_adapter"
        assert loaded.template_id == "fnt_persist"
        assert loaded.preference_decision == "send"
        assert loaded.metadata == {"priority": "high", "tags": ["urgent", "review"]}
        assert loaded.created_at is not None
        store2.close()

    async def test_sensitive_metadata_sanitized(self, tmp_path: Path) -> None:
        db_path = tmp_path / "observability.db"
        store = SQLiteNotificationObservabilityStore(str(db_path))

        event = _make_event(
            event_id="nde_sensitive",
            error_message="Request failed with Authorization: Bearer secret_token_123",
            metadata={
                "authorization": "Bearer abc",
                "api_key": "my-secret-key",
                "retry_count": 3,
                "headers": {
                    "x-api-key": "should-be-redacted",
                    "content-type": "application/json",
                },
            },
            created_at=_now(),
        )
        await store.record_event(event)

        loaded = await store.get_event("nde_sensitive")
        assert loaded is not None
        # error_message should have sensitive values redacted
        assert "secret_token_123" not in (loaded.error_message or "")
        assert "[REDACTED]" in (loaded.error_message or "")
        # metadata sensitive keys should be redacted
        assert loaded.metadata["authorization"] == "[REDACTED]"
        assert loaded.metadata["api_key"] == "[REDACTED]"
        assert loaded.metadata["retry_count"] == 3  # non-sensitive key preserved
        assert loaded.metadata["headers"]["x-api-key"] == "[REDACTED]"
        assert loaded.metadata["headers"]["content-type"] == "application/json"

        store.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateNotificationObservabilityStore:
    def test_factory_memory_type(self) -> None:
        store = create_notification_observability_store("memory")
        assert isinstance(store, InMemoryNotificationObservabilityStore)
        assert isinstance(store, NotificationObservabilityStore)

    def test_factory_sqlite_type(self, tmp_path: Path) -> None:
        db_path = tmp_path / "observability.db"
        store = create_notification_observability_store("sqlite", str(db_path))
        assert isinstance(store, SQLiteNotificationObservabilityStore)
        assert isinstance(store, NotificationObservabilityStore)
        store.close()

    def test_unknown_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown observability store type"):
            create_notification_observability_store("redis")
