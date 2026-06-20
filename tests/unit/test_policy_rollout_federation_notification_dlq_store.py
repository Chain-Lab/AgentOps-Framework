"""Tests for FederationNotificationDLQStore — InMemory, SQLite, and factory."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationDeadLetter,
    FederationNotificationDLQReason,
    FederationNotificationDLQStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_dlq_store import (
    FederationNotificationDLQStore,
    InMemoryFederationNotificationDLQStore,
    SQLiteFederationNotificationDLQStore,
    create_federation_notification_dlq_store,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _now(offset_seconds: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)


def _make_dlq_item(**overrides) -> FederationNotificationDeadLetter:
    now = _now()
    defaults = dict(
        dlq_id=f"fdlq_{uuid.uuid4().hex}",
        notification_id="fn_test123",
        approval_id="fap_test123",
        federation_id="frp_test123",
        channel="webhook",
        adapter="webhook",
        recipient="https://example.com/hook",
        reason=FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
        status=FederationNotificationDLQStatus.PENDING,
        failure_count=3,
        last_error="Connection timeout",
        payload={"test": "data"},
        metadata={"source": "test"},
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return FederationNotificationDeadLetter(**defaults)


# ---------------------------------------------------------------------------
# InMemoryFederationNotificationDLQStore
# ---------------------------------------------------------------------------


class TestInMemoryFederationNotificationDLQStore:
    def test_inmemory_create_and_get(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        item = _make_dlq_item(dlq_id="fdlq_001")
        result = _run_async(store.create(item))
        assert result == item
        assert _run_async(store.get("fdlq_001")) == item

    def test_inmemory_get_nonexistent_returns_none(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        assert _run_async(store.get("fdlq_missing")) is None

    def test_inmemory_list_all(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        i1 = _make_dlq_item(dlq_id="fdlq_001", created_at=_now(0))
        i2 = _make_dlq_item(dlq_id="fdlq_002", created_at=_now(10))
        _run_async(store.create(i1))
        _run_async(store.create(i2))

        result = _run_async(store.list())
        assert len(result) == 2
        assert result[0].dlq_id == "fdlq_001"
        assert result[1].dlq_id == "fdlq_002"

    def test_inmemory_list_by_status(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        i1 = _make_dlq_item(dlq_id="fdlq_001", status=FederationNotificationDLQStatus.PENDING, created_at=_now(0))
        i2 = _make_dlq_item(dlq_id="fdlq_002", status=FederationNotificationDLQStatus.RETRIED, created_at=_now(10))
        i3 = _make_dlq_item(dlq_id="fdlq_003", status=FederationNotificationDLQStatus.PENDING, created_at=_now(20))
        _run_async(store.create(i1))
        _run_async(store.create(i2))
        _run_async(store.create(i3))

        result = _run_async(store.list(status=FederationNotificationDLQStatus.PENDING))
        assert len(result) == 2
        assert result[0].dlq_id == "fdlq_001"
        assert result[1].dlq_id == "fdlq_003"

    def test_inmemory_list_by_federation_id(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        i1 = _make_dlq_item(dlq_id="fdlq_001", federation_id="frp_alpha", created_at=_now(0))
        i2 = _make_dlq_item(dlq_id="fdlq_002", federation_id="frp_beta", created_at=_now(10))
        i3 = _make_dlq_item(dlq_id="fdlq_003", federation_id="frp_alpha", created_at=_now(20))
        _run_async(store.create(i1))
        _run_async(store.create(i2))
        _run_async(store.create(i3))

        result = _run_async(store.list(federation_id="frp_alpha"))
        assert len(result) == 2
        assert result[0].dlq_id == "fdlq_001"
        assert result[1].dlq_id == "fdlq_003"

    def test_inmemory_list_by_approval_id(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        i1 = _make_dlq_item(dlq_id="fdlq_001", approval_id="fap_001", created_at=_now(0))
        i2 = _make_dlq_item(dlq_id="fdlq_002", approval_id="fap_002", created_at=_now(10))
        i3 = _make_dlq_item(dlq_id="fdlq_003", approval_id="fap_001", created_at=_now(20))
        _run_async(store.create(i1))
        _run_async(store.create(i2))
        _run_async(store.create(i3))

        result = _run_async(store.list(approval_id="fap_001"))
        assert len(result) == 2
        assert result[0].dlq_id == "fdlq_001"
        assert result[1].dlq_id == "fdlq_003"

    def test_inmemory_list_by_channel(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        i1 = _make_dlq_item(dlq_id="fdlq_001", channel="webhook", created_at=_now(0))
        i2 = _make_dlq_item(dlq_id="fdlq_002", channel="email", created_at=_now(10))
        i3 = _make_dlq_item(dlq_id="fdlq_003", channel="webhook", created_at=_now(20))
        _run_async(store.create(i1))
        _run_async(store.create(i2))
        _run_async(store.create(i3))

        result = _run_async(store.list(channel="webhook"))
        assert len(result) == 2
        assert result[0].dlq_id == "fdlq_001"
        assert result[1].dlq_id == "fdlq_003"

    def test_inmemory_list_pagination_limit(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        for i in range(5):
            _run_async(store.create(_make_dlq_item(dlq_id=f"fdlq_{i:03d}", created_at=_now(i))))

        result = _run_async(store.list(limit=2))
        assert len(result) == 2
        assert result[0].dlq_id == "fdlq_000"
        assert result[1].dlq_id == "fdlq_001"

    def test_inmemory_list_pagination_offset(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        for i in range(5):
            _run_async(store.create(_make_dlq_item(dlq_id=f"fdlq_{i:03d}", created_at=_now(i))))

        result = _run_async(store.list(offset=2))
        assert len(result) == 3
        assert result[0].dlq_id == "fdlq_002"

    def test_inmemory_mark_retried(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        item = _make_dlq_item(dlq_id="fdlq_001")
        _run_async(store.create(item))

        result = _run_async(store.mark_retried("fdlq_001"))
        assert result.status == FederationNotificationDLQStatus.RETRIED
        assert result.retried_at is not None
        assert result.retried_at.tzinfo is not None
        assert result.updated_at.tzinfo is not None

    def test_inmemory_mark_purged(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        item = _make_dlq_item(dlq_id="fdlq_001")
        _run_async(store.create(item))

        result = _run_async(store.mark_purged("fdlq_001"))
        assert result.status == FederationNotificationDLQStatus.PURGED
        assert result.purged_at is not None
        assert result.purged_at.tzinfo is not None
        assert result.updated_at.tzinfo is not None

    def test_inmemory_delete(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        item = _make_dlq_item(dlq_id="fdlq_001")
        _run_async(store.create(item))

        _run_async(store.delete("fdlq_001"))
        assert _run_async(store.get("fdlq_001")) is None

    def test_inmemory_delete_nonexistent(self) -> None:
        store = InMemoryFederationNotificationDLQStore()
        # Should not raise
        _run_async(store.delete("fdlq_missing"))


# ---------------------------------------------------------------------------
# SQLiteFederationNotificationDLQStore
# ---------------------------------------------------------------------------


class TestSQLiteFederationNotificationDLQStore:
    def test_sqlite_create_and_get(self, tmp_path: Path) -> None:
        db_path = tmp_path / "dlq.db"
        store = SQLiteFederationNotificationDLQStore(str(db_path))
        item = _make_dlq_item(dlq_id="fdlq_001")
        _run_async(store.create(item))

        result = _run_async(store.get("fdlq_001"))
        assert result is not None
        assert result.dlq_id == "fdlq_001"
        assert result.notification_id == "fn_test123"
        assert result.approval_id == "fap_test123"
        assert result.federation_id == "frp_test123"
        assert result.channel == "webhook"
        assert result.adapter == "webhook"
        assert result.recipient == "https://example.com/hook"
        assert result.reason == FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED
        assert result.status == FederationNotificationDLQStatus.PENDING
        assert result.failure_count == 3
        assert result.last_error == "Connection timeout"
        assert result.payload == {"test": "data"}
        assert result.metadata == {"source": "test"}
        assert result.retried_at is None
        assert result.purged_at is None
        store.close()

    def test_sqlite_list_by_status(self, tmp_path: Path) -> None:
        db_path = tmp_path / "dlq.db"
        store = SQLiteFederationNotificationDLQStore(str(db_path))
        i1 = _make_dlq_item(dlq_id="fdlq_001", status=FederationNotificationDLQStatus.PENDING, created_at=_now(0))
        i2 = _make_dlq_item(dlq_id="fdlq_002", status=FederationNotificationDLQStatus.RETRIED, created_at=_now(10))
        i3 = _make_dlq_item(dlq_id="fdlq_003", status=FederationNotificationDLQStatus.PENDING, created_at=_now(20))
        _run_async(store.create(i1))
        _run_async(store.create(i2))
        _run_async(store.create(i3))

        result = _run_async(store.list(status=FederationNotificationDLQStatus.PENDING))
        assert len(result) == 2
        assert result[0].dlq_id == "fdlq_001"
        assert result[1].dlq_id == "fdlq_003"
        store.close()

    def test_sqlite_list_by_federation_id(self, tmp_path: Path) -> None:
        db_path = tmp_path / "dlq.db"
        store = SQLiteFederationNotificationDLQStore(str(db_path))
        i1 = _make_dlq_item(dlq_id="fdlq_001", federation_id="frp_alpha", created_at=_now(0))
        i2 = _make_dlq_item(dlq_id="fdlq_002", federation_id="frp_beta", created_at=_now(10))
        _run_async(store.create(i1))
        _run_async(store.create(i2))

        result = _run_async(store.list(federation_id="frp_alpha"))
        assert len(result) == 1
        assert result[0].dlq_id == "fdlq_001"
        store.close()

    def test_sqlite_list_pagination(self, tmp_path: Path) -> None:
        db_path = tmp_path / "dlq.db"
        store = SQLiteFederationNotificationDLQStore(str(db_path))
        for i in range(5):
            _run_async(store.create(_make_dlq_item(dlq_id=f"fdlq_{i:03d}", created_at=_now(i))))

        result = _run_async(store.list(limit=2, offset=1))
        assert len(result) == 2
        assert result[0].dlq_id == "fdlq_001"
        assert result[1].dlq_id == "fdlq_002"
        store.close()

    def test_sqlite_mark_retried(self, tmp_path: Path) -> None:
        db_path = tmp_path / "dlq.db"
        store = SQLiteFederationNotificationDLQStore(str(db_path))
        item = _make_dlq_item(dlq_id="fdlq_001")
        _run_async(store.create(item))

        result = _run_async(store.mark_retried("fdlq_001"))
        assert result.status == FederationNotificationDLQStatus.RETRIED
        assert result.retried_at is not None
        assert result.retried_at.tzinfo is not None

        # Verify persistence
        loaded = _run_async(store.get("fdlq_001"))
        assert loaded is not None
        assert loaded.status == FederationNotificationDLQStatus.RETRIED
        assert loaded.retried_at is not None
        store.close()

    def test_sqlite_mark_purged(self, tmp_path: Path) -> None:
        db_path = tmp_path / "dlq.db"
        store = SQLiteFederationNotificationDLQStore(str(db_path))
        item = _make_dlq_item(dlq_id="fdlq_001")
        _run_async(store.create(item))

        result = _run_async(store.mark_purged("fdlq_001"))
        assert result.status == FederationNotificationDLQStatus.PURGED
        assert result.purged_at is not None
        assert result.purged_at.tzinfo is not None

        # Verify persistence
        loaded = _run_async(store.get("fdlq_001"))
        assert loaded is not None
        assert loaded.status == FederationNotificationDLQStatus.PURGED
        assert loaded.purged_at is not None
        store.close()

    def test_sqlite_delete(self, tmp_path: Path) -> None:
        db_path = tmp_path / "dlq.db"
        store = SQLiteFederationNotificationDLQStore(str(db_path))
        item = _make_dlq_item(dlq_id="fdlq_001")
        _run_async(store.create(item))

        _run_async(store.delete("fdlq_001"))
        assert _run_async(store.get("fdlq_001")) is None
        store.close()

    def test_sqlite_persists_across_instances(self, tmp_path: Path) -> None:
        db_path = tmp_path / "dlq.db"
        store = SQLiteFederationNotificationDLQStore(str(db_path))
        item = _make_dlq_item(
            dlq_id="fdlq_001",
            payload={"key": "value"},
            metadata={"env": "prod"},
        )
        _run_async(store.create(item))
        store.close()

        # Reopen same DB
        store2 = SQLiteFederationNotificationDLQStore(str(db_path))
        loaded = _run_async(store2.get("fdlq_001"))

        assert loaded is not None
        assert loaded.dlq_id == "fdlq_001"
        assert loaded.notification_id == "fn_test123"
        assert loaded.approval_id == "fap_test123"
        assert loaded.federation_id == "frp_test123"
        assert loaded.channel == "webhook"
        assert loaded.adapter == "webhook"
        assert loaded.recipient == "https://example.com/hook"
        assert loaded.reason == FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED
        assert loaded.status == FederationNotificationDLQStatus.PENDING
        assert loaded.failure_count == 3
        assert loaded.last_error == "Connection timeout"
        assert loaded.payload == {"key": "value"}
        assert loaded.metadata == {"env": "prod"}
        store2.close()

    def test_sqlite_json_fields_stored_correctly(self, tmp_path: Path) -> None:
        db_path = tmp_path / "dlq.db"
        store = SQLiteFederationNotificationDLQStore(str(db_path))
        item = _make_dlq_item(
            dlq_id="fdlq_001",
            payload={"nested": {"deep": [1, 2, 3]}, "flag": True},
            metadata={"tags": ["a", "b"], "count": 42},
        )
        _run_async(store.create(item))

        result = _run_async(store.get("fdlq_001"))
        assert result is not None
        assert result.payload == {"nested": {"deep": [1, 2, 3]}, "flag": True}
        assert result.metadata == {"tags": ["a", "b"], "count": 42}
        store.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateFederationNotificationDLQStore:
    def test_factory_memory_type(self) -> None:
        store = create_federation_notification_dlq_store("memory")
        assert isinstance(store, InMemoryFederationNotificationDLQStore)
        assert isinstance(store, FederationNotificationDLQStore)

    def test_factory_sqlite_type(self, tmp_path: Path) -> None:
        db_path = tmp_path / "dlq.db"
        store = create_federation_notification_dlq_store("sqlite", str(db_path))
        assert isinstance(store, SQLiteFederationNotificationDLQStore)
        assert isinstance(store, FederationNotificationDLQStore)
        store.close()

    def test_factory_unknown_type_raises(self) -> None:
        try:
            create_federation_notification_dlq_store("redis")
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "Unknown DLQ store type" in str(e)
