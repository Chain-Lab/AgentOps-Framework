"""Tests for PolicyChangeEventStore -- Protocol, InMemory, SQLite, factory."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_change_event import PolicyChangeEvent, PolicyChangeEventType
from agent_app.runtime.policy_change_event_store import (
    InMemoryPolicyChangeEventStore,
    SQLitePolicyChangeEventStore,
    create_policy_change_event_store,
)


def _make_event(
    event_id: str | None = None,
    event_type: PolicyChangeEventType = PolicyChangeEventType.BUNDLE_CREATED,
    environment: str | None = None,
    ring_name: str | None = None,
    bundle_id: str | None = None,
    activation_id: str | None = None,
    assignment_id: str | None = None,
    actor_id: str | None = None,
    reason: str | None = None,
    data: dict | None = None,
    created_at: datetime | None = None,
) -> PolicyChangeEvent:
    return PolicyChangeEvent(
        event_id=event_id or f"pce_{uuid.uuid4().hex[:12]}",
        event_type=event_type,
        environment=environment,
        ring_name=ring_name,
        bundle_id=bundle_id,
        activation_id=activation_id,
        assignment_id=assignment_id,
        actor_id=actor_id,
        reason=reason,
        data=data or {},
        created_at=created_at or datetime.now(timezone.utc),
    )


# ── InMemory tests ──────────────────────────────────────────────────


class TestInMemoryPolicyChangeEventStore:
    @pytest.mark.asyncio
    async def test_in_memory_append_get(self):
        store = InMemoryPolicyChangeEventStore()
        event = _make_event(event_id="pce_001", environment="prod")
        appended = await store.append(event)
        assert appended.event_id == "pce_001"
        fetched = await store.get("pce_001")
        assert fetched is not None
        assert fetched.event_id == "pce_001"
        assert fetched.environment == "prod"
        # Missing id returns None
        assert await store.get("pce_nonexistent") is None

    @pytest.mark.asyncio
    async def test_in_memory_list_by_environment(self):
        store = InMemoryPolicyChangeEventStore()
        await store.append(_make_event(event_id="pce_1", environment="prod"))
        await store.append(_make_event(event_id="pce_2", environment="prod"))
        await store.append(_make_event(event_id="pce_3", environment="dev"))
        prod_events = await store.list(environment="prod")
        assert len(prod_events) == 2
        assert all(e.environment == "prod" for e in prod_events)
        # No filter returns all
        all_events = await store.list()
        assert len(all_events) == 3

    @pytest.mark.asyncio
    async def test_in_memory_list_by_ring(self):
        store = InMemoryPolicyChangeEventStore()
        await store.append(_make_event(event_id="pce_1", ring_name="canary"))
        await store.append(_make_event(event_id="pce_2", ring_name="stable"))
        await store.append(_make_event(event_id="pce_3", ring_name="canary"))
        canary_events = await store.list(ring_name="canary")
        assert len(canary_events) == 2
        assert all(e.ring_name == "canary" for e in canary_events)

    @pytest.mark.asyncio
    async def test_in_memory_latest(self):
        store = InMemoryPolicyChangeEventStore()
        await store.append(_make_event(event_id="pce_1", environment="prod", ring_name="stable"))
        await store.append(_make_event(event_id="pce_2", environment="prod", ring_name="canary"))
        await store.append(_make_event(event_id="pce_3", environment="prod", ring_name="stable"))
        latest = await store.latest(environment="prod", ring_name="stable")
        assert latest is not None
        assert latest.event_id == "pce_3"
        # No match returns None
        assert await store.latest(environment="staging") is None

    @pytest.mark.asyncio
    async def test_in_memory_chronological_order(self):
        store = InMemoryPolicyChangeEventStore()
        t1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2025, 6, 1, tzinfo=timezone.utc)
        t3 = datetime(2025, 12, 1, tzinfo=timezone.utc)
        # Append in non-chronological order
        await store.append(_make_event(event_id="pce_3", created_at=t3))
        await store.append(_make_event(event_id="pce_1", created_at=t1))
        await store.append(_make_event(event_id="pce_2", created_at=t2))
        events = await store.list()
        # list returns oldest-first based on insertion order
        assert events[0].event_id == "pce_3"
        assert events[1].event_id == "pce_1"
        assert events[2].event_id == "pce_2"


# ── SQLite tests ─────────────────────────────────────────────────────


class TestSQLitePolicyChangeEventStore:
    @pytest.mark.asyncio
    async def test_sqlite_persistence(self, tmp_path):
        db = tmp_path / "events.db"
        s1 = SQLitePolicyChangeEventStore(str(db))
        event = _make_event(event_id="pce_persist", environment="prod")
        await s1.append(event)
        s1.close()
        # Read with a new instance
        s2 = SQLitePolicyChangeEventStore(str(db))
        fetched = await s2.get("pce_persist")
        assert fetched is not None
        assert fetched.event_id == "pce_persist"
        assert fetched.environment == "prod"
        s2.close()

    @pytest.mark.asyncio
    async def test_sqlite_list_with_since(self, tmp_path):
        db = tmp_path / "events.db"
        store = SQLitePolicyChangeEventStore(str(db))
        t1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2025, 6, 1, tzinfo=timezone.utc)
        t3 = datetime(2025, 12, 1, tzinfo=timezone.utc)
        await store.append(_make_event(event_id="pce_1", created_at=t1))
        await store.append(_make_event(event_id="pce_2", created_at=t2))
        await store.append(_make_event(event_id="pce_3", created_at=t3))
        # Only events after t2
        recent = await store.list(since=t2)
        assert len(recent) == 2
        assert recent[0].event_id == "pce_2"
        assert recent[1].event_id == "pce_3"
        store.close()


# ── Factory tests ────────────────────────────────────────────────────


def test_factory_memory():
    store = create_policy_change_event_store("memory")
    assert isinstance(store, InMemoryPolicyChangeEventStore)


def test_factory_sqlite(tmp_path):
    store = create_policy_change_event_store("sqlite", str(tmp_path / "events.db"))
    assert isinstance(store, SQLitePolicyChangeEventStore)
    store.close()


def test_factory_unknown():
    with pytest.raises(ValueError, match="Unknown change event store type"):
        create_policy_change_event_store("redis")
