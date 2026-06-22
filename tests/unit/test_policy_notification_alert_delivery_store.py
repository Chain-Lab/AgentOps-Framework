"""Tests for Phase 53 Task 2 — Alert delivery store (InMemory + SQLite)."""
from __future__ import annotations

import os
import tempfile
import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    AlertDeliveryTarget,
    AlertDeliveryAttempt,
)
from agent_app.runtime.policy_rollout_federation_notification_alert_delivery_store import (
    AlertDeliveryStore,
    InMemoryAlertDeliveryStore,
    SQLiteAlertDeliveryStore,
    create_alert_delivery_store,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_target(target_id: str = "ndt_1") -> AlertDeliveryTarget:
    return AlertDeliveryTarget(
        target_id=target_id, name="Ops Console",
        channel_type=AlertDeliveryChannelType.CONSOLE,
    )


def _make_attempt(attempt_id: str = "nda_1", alert_id: str = "nae_1",
                  target_id: str = "ndt_1") -> AlertDeliveryAttempt:
    return AlertDeliveryAttempt(
        attempt_id=attempt_id, alert_id=alert_id, target_id=target_id,
        channel_type=AlertDeliveryChannelType.CONSOLE,
        status=AlertDeliveryStatus.DELIVERED,
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Protocol check
# ---------------------------------------------------------------------------


def test_inmemory_implements_protocol():
    store = InMemoryAlertDeliveryStore()
    assert isinstance(store, AlertDeliveryStore)


# ---------------------------------------------------------------------------
# InMemory target CRUD
# ---------------------------------------------------------------------------


class TestInMemoryTargetCRUD:
    @pytest.mark.asyncio
    async def test_create_target(self):
        store = InMemoryAlertDeliveryStore()
        t = _make_target()
        result = await store.create_target(t)
        assert result.target_id == "ndt_1"

    @pytest.mark.asyncio
    async def test_duplicate_target_raises(self):
        store = InMemoryAlertDeliveryStore()
        t1 = _make_target("ndt_1")
        t2 = _make_target("ndt_1")
        await store.create_target(t1)
        with pytest.raises(ValueError, match="already exists"):
            await store.create_target(t2)

    @pytest.mark.asyncio
    async def test_get_target(self):
        store = InMemoryAlertDeliveryStore()
        t = _make_target()
        await store.create_target(t)
        result = await store.get_target("ndt_1")
        assert result is not None
        assert result.name == "Ops Console"

    @pytest.mark.asyncio
    async def test_get_missing_target(self):
        store = InMemoryAlertDeliveryStore()
        assert await store.get_target("ndt_missing") is None

    @pytest.mark.asyncio
    async def test_list_targets_all(self):
        store = InMemoryAlertDeliveryStore()
        await store.create_target(_make_target("ndt_1"))
        await store.create_target(_make_target("ndt_2"))
        results = await store.list_targets()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_targets_enabled_filter(self):
        store = InMemoryAlertDeliveryStore()
        await store.create_target(_make_target("ndt_1"))
        await store.create_target(AlertDeliveryTarget(
            target_id="ndt_2", name="Disabled", channel_type=AlertDeliveryChannelType.CONSOLE,
            enabled=False,
        ))
        enabled = await store.list_targets(enabled=True)
        assert len(enabled) == 1
        assert enabled[0].target_id == "ndt_1"

    @pytest.mark.asyncio
    async def test_list_targets_disabled_filter(self):
        store = InMemoryAlertDeliveryStore()
        await store.create_target(_make_target("ndt_1"))
        await store.create_target(AlertDeliveryTarget(
            target_id="ndt_2", name="Disabled", channel_type=AlertDeliveryChannelType.CONSOLE,
            enabled=False,
        ))
        disabled = await store.list_targets(enabled=False)
        assert len(disabled) == 1
        assert disabled[0].target_id == "ndt_2"

    @pytest.mark.asyncio
    async def test_update_target(self):
        store = InMemoryAlertDeliveryStore()
        t = _make_target()
        await store.create_target(t)
        t.name = "Updated Name"
        result = await store.update_target(t)
        assert result.name == "Updated Name"
        # Verify persisted
        fetched = await store.get_target("ndt_1")
        assert fetched.name == "Updated Name"

    @pytest.mark.asyncio
    async def test_delete_target(self):
        store = InMemoryAlertDeliveryStore()
        t = _make_target()
        await store.create_target(t)
        await store.delete_target("ndt_1")
        assert await store.get_target("ndt_1") is None

    @pytest.mark.asyncio
    async def test_delete_missing_target_no_error(self):
        store = InMemoryAlertDeliveryStore()
        await store.delete_target("ndt_missing")  # Should not raise


# ---------------------------------------------------------------------------
# InMemory attempt CRUD
# ---------------------------------------------------------------------------


class TestInMemoryAttemptCRUD:
    @pytest.mark.asyncio
    async def test_record_attempt(self):
        store = InMemoryAlertDeliveryStore()
        a = _make_attempt()
        result = await store.record_attempt(a)
        assert result.attempt_id == "nda_1"

    @pytest.mark.asyncio
    async def test_get_attempt(self):
        store = InMemoryAlertDeliveryStore()
        a = _make_attempt()
        await store.record_attempt(a)
        result = await store.get_attempt("nda_1")
        assert result is not None
        assert result.alert_id == "nae_1"

    @pytest.mark.asyncio
    async def test_list_attempts_by_alert_id(self):
        store = InMemoryAlertDeliveryStore()
        await store.record_attempt(_make_attempt("nda_1", "nae_1"))
        await store.record_attempt(_make_attempt("nda_2", "nae_1"))
        await store.record_attempt(_make_attempt("nda_3", "nae_2"))
        results = await store.list_attempts(alert_id="nae_1")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_attempts_by_target_id(self):
        store = InMemoryAlertDeliveryStore()
        await store.record_attempt(_make_attempt("nda_1", "nae_1", "ndt_1"))
        await store.record_attempt(_make_attempt("nda_2", "nae_1", "ndt_2"))
        results = await store.list_attempts(target_id="ndt_1")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_list_attempts_by_status(self):
        store = InMemoryAlertDeliveryStore()
        await store.record_attempt(_make_attempt("nda_1", "nae_1"))
        await store.record_attempt(AlertDeliveryAttempt(
            attempt_id="nda_2", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            status=AlertDeliveryStatus.FAILED,
            created_at=datetime.now(timezone.utc),
        ))
        results = await store.list_attempts(status="failed")
        assert len(results) == 1
        assert results[0].status == AlertDeliveryStatus.FAILED

    @pytest.mark.asyncio
    async def test_list_attempts_pagination(self):
        store = InMemoryAlertDeliveryStore()
        for i in range(5):
            await store.record_attempt(_make_attempt(f"nda_{i}", "nae_1"))
        page = await store.list_attempts(limit=2, offset=2)
        assert len(page) == 2
        # Should be sorted by created_at desc
        assert page[0].attempt_id == "nda_2"


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------


class TestSQLiteAlertDeliveryStore:
    @pytest.fixture
    def db_path(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        yield path
        try:
            os.unlink(path)
        except OSError:
            pass

    @pytest.mark.asyncio
    async def test_persists_target_across_instances(self, db_path):
        store1 = SQLiteAlertDeliveryStore(db_path)
        t = _make_target("ndt_1")
        await store1.create_target(t)
        store1.close()

        store2 = SQLiteAlertDeliveryStore(db_path)
        result = await store2.get_target("ndt_1")
        assert result is not None
        assert result.name == "Ops Console"
        store2.close()

    @pytest.mark.asyncio
    async def test_persists_attempt_across_instances(self, db_path):
        store1 = SQLiteAlertDeliveryStore(db_path)
        a = _make_attempt("nda_1", "nae_1", "ndt_1")
        await store1.record_attempt(a)
        store1.close()

        store2 = SQLiteAlertDeliveryStore(db_path)
        result = await store2.get_attempt("nda_1")
        assert result is not None
        assert result.alert_id == "nae_1"
        store2.close()

    @pytest.mark.asyncio
    async def test_list_targets_enabled_filter_sqlite(self, db_path):
        store = SQLiteAlertDeliveryStore(db_path)
        await store.create_target(_make_target("ndt_1"))
        await store.create_target(AlertDeliveryTarget(
            target_id="ndt_2", name="Disabled", channel_type=AlertDeliveryChannelType.CONSOLE,
            enabled=False,
        ))
        enabled = await store.list_targets(enabled=True)
        assert len(enabled) == 1
        store.close()

    @pytest.mark.asyncio
    async def test_update_and_delete_sqlite(self, db_path):
        store = SQLiteAlertDeliveryStore(db_path)
        t = _make_target("ndt_1")
        await store.create_target(t)
        t.name = "Updated"
        await store.update_target(t)
        result = await store.get_target("ndt_1")
        assert result.name == "Updated"
        await store.delete_target("ndt_1")
        assert await store.get_target("ndt_1") is None
        store.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_create_memory(self):
        store = create_alert_delivery_store("memory")
        assert isinstance(store, InMemoryAlertDeliveryStore)

    def test_create_sqlite(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            store = create_alert_delivery_store("sqlite", path)
            assert isinstance(store, SQLiteAlertDeliveryStore)
            store.close()
        finally:
            os.unlink(path)

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            create_alert_delivery_store("unknown")
