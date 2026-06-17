"""Tests for PolicyNotificationStore -- Protocol, InMemory, SQLite, factory."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_notification import (
    PolicyNotificationMessage,
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
)
from agent_app.runtime.policy_notification_store import (
    InMemoryPolicyNotificationStore,
    PolicyNotificationStore,
    SQLitePolicyNotificationStore,
    create_policy_notification_store,
)


def _make_msg(
    notification_id: str = "pn_001",
    event_type: str = "test.event",
    status: PolicyNotificationStatus | None = None,
) -> PolicyNotificationMessage:
    return PolicyNotificationMessage(
        notification_id=notification_id,
        event_type=event_type,
        severity=PolicyNotificationSeverity.INFO,
        title="Test",
        body="Body",
        status=status or PolicyNotificationStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )


# -- InMemory tests --


class TestInMemoryPolicyNotificationStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        store = InMemoryPolicyNotificationStore()
        msg = _make_msg(notification_id="pn_001")
        created = await store.create(msg)
        assert created.notification_id == "pn_001"
        fetched = await store.get("pn_001")
        assert fetched is not None
        assert fetched.notification_id == "pn_001"
        assert fetched.event_type == "test.event"

    @pytest.mark.asyncio
    async def test_get_missing(self):
        store = InMemoryPolicyNotificationStore()
        assert await store.get("pn_nonexistent") is None

    @pytest.mark.asyncio
    async def test_update(self):
        store = InMemoryPolicyNotificationStore()
        msg = _make_msg(notification_id="pn_002")
        await store.create(msg)
        msg.status = PolicyNotificationStatus.SENT
        updated = await store.update(msg)
        assert updated.status == PolicyNotificationStatus.SENT
        fetched = await store.get("pn_002")
        assert fetched is not None
        assert fetched.status == PolicyNotificationStatus.SENT

    @pytest.mark.asyncio
    async def test_list_all(self):
        store = InMemoryPolicyNotificationStore()
        await store.create(_make_msg(notification_id="pn_010"))
        await store.create(_make_msg(notification_id="pn_011"))
        await store.create(_make_msg(notification_id="pn_012"))
        results = await store.list()
        assert len(results) == 3
        # Newest first
        assert results[0].notification_id == "pn_012"

    @pytest.mark.asyncio
    async def test_list_by_status(self):
        store = InMemoryPolicyNotificationStore()
        await store.create(_make_msg(notification_id="pn_s1", status=PolicyNotificationStatus.PENDING))
        await store.create(_make_msg(notification_id="pn_s2", status=PolicyNotificationStatus.SENT))
        await store.create(_make_msg(notification_id="pn_s3", status=PolicyNotificationStatus.PENDING))
        pending = await store.list(status=PolicyNotificationStatus.PENDING)
        assert len(pending) == 2
        assert all(m.status == PolicyNotificationStatus.PENDING for m in pending)
        sent = await store.list(status=PolicyNotificationStatus.SENT)
        assert len(sent) == 1

    @pytest.mark.asyncio
    async def test_list_by_event_type(self):
        store = InMemoryPolicyNotificationStore()
        await store.create(_make_msg(notification_id="pn_e1", event_type="rollout.started"))
        await store.create(_make_msg(notification_id="pn_e2", event_type="rollout.completed"))
        await store.create(_make_msg(notification_id="pn_e3", event_type="rollout.started"))
        started = await store.list(event_type="rollout.started")
        assert len(started) == 2
        assert all(m.event_type == "rollout.started" for m in started)

    @pytest.mark.asyncio
    async def test_list_with_limit(self):
        store = InMemoryPolicyNotificationStore()
        for i in range(5):
            await store.create(_make_msg(notification_id=f"pn_lim_{i:03d}"))
        results = await store.list(limit=2)
        assert len(results) == 2
        # Newest first
        assert results[0].notification_id == "pn_lim_004"
        assert results[1].notification_id == "pn_lim_003"


# -- SQLite tests --


class TestSQLitePolicyNotificationStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = SQLitePolicyNotificationStore(db_path=db_path)
            msg = _make_msg(notification_id="pn_sql_001")
            created = await store.create(msg)
            assert created.notification_id == "pn_sql_001"
            fetched = await store.get("pn_sql_001")
            assert fetched is not None
            assert fetched.notification_id == "pn_sql_001"
            assert fetched.event_type == "test.event"
            store.close()
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_persists_across_instances(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = SQLitePolicyNotificationStore(db_path=db_path)
            msg = _make_msg(notification_id="pn_persist_001", event_type="persist.event")
            await store.create(msg)
            store.close()
            # Read with a new instance
            store2 = SQLitePolicyNotificationStore(db_path=db_path)
            fetched = await store2.get("pn_persist_001")
            assert fetched is not None
            assert fetched.notification_id == "pn_persist_001"
            assert fetched.event_type == "persist.event"
            assert fetched.status == PolicyNotificationStatus.PENDING
            store2.close()
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_list_by_status(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = SQLitePolicyNotificationStore(db_path=db_path)
            await store.create(_make_msg(notification_id="pn_ls1", status=PolicyNotificationStatus.PENDING))
            await store.create(_make_msg(notification_id="pn_ls2", status=PolicyNotificationStatus.SENT))
            await store.create(_make_msg(notification_id="pn_ls3", status=PolicyNotificationStatus.PENDING))
            pending = await store.list(status=PolicyNotificationStatus.PENDING)
            assert len(pending) == 2
            assert all(m.status == PolicyNotificationStatus.PENDING for m in pending)
            store.close()
        finally:
            os.unlink(db_path)


# -- Factory tests --


class TestCreatePolicyNotificationStore:
    def test_memory(self):
        store = create_policy_notification_store("memory")
        assert isinstance(store, InMemoryPolicyNotificationStore)

    def test_sqlite(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = create_policy_notification_store("sqlite", db_path)
            assert isinstance(store, SQLitePolicyNotificationStore)
            store.close()
        finally:
            os.unlink(db_path)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown policy notification store type"):
            create_policy_notification_store("redis")
