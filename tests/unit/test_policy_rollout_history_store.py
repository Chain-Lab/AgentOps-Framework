"""Unit tests for rollout history store — InMemory and SQLite implementations."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from agent_app.governance.policy_rollout_history import (
    RolloutHistoryEvent,
    RolloutHistoryEventType,
)
from agent_app.runtime.policy_rollout_history_store import (
    InMemoryRolloutHistoryStore,
    RolloutHistoryStore,
    SQLiteRolloutHistoryStore,
    create_rollout_history_store,
)


def _make_event(
    history_event_id: str = "rhe_001",
    rollout_id: str = "ro_001",
    event_type: RolloutHistoryEventType = RolloutHistoryEventType.ROLLOUT_CREATED,
    step_id: str | None = None,
    created_at: datetime | None = None,
    **kwargs,
) -> RolloutHistoryEvent:
    return RolloutHistoryEvent(
        history_event_id=history_event_id,
        rollout_id=rollout_id,
        event_type=event_type,
        step_id=step_id,
        created_at=created_at or datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# InMemory tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_append_and_get():
    store = InMemoryRolloutHistoryStore()
    event = _make_event()
    result = await store.append(event)
    assert result is event

    fetched = await store.get("rhe_001")
    assert fetched is not None
    assert fetched.history_event_id == "rhe_001"
    assert fetched.rollout_id == "ro_001"
    assert fetched.event_type == RolloutHistoryEventType.ROLLOUT_CREATED


@pytest.mark.asyncio
async def test_in_memory_get_missing():
    store = InMemoryRolloutHistoryStore()
    result = await store.get("rhe_nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_in_memory_list_all():
    store = InMemoryRolloutHistoryStore()
    t1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)
    await store.append(_make_event(history_event_id="rhe_001", created_at=t2))
    await store.append(_make_event(history_event_id="rhe_002", created_at=t1))

    results = await store.list()
    assert len(results) == 2
    # Chronological order: t1 first, t2 second
    assert results[0].history_event_id == "rhe_002"
    assert results[1].history_event_id == "rhe_001"


@pytest.mark.asyncio
async def test_in_memory_list_by_rollout_id():
    store = InMemoryRolloutHistoryStore()
    await store.append(_make_event(history_event_id="rhe_001", rollout_id="ro_alpha"))
    await store.append(_make_event(history_event_id="rhe_002", rollout_id="ro_beta"))
    await store.append(_make_event(history_event_id="rhe_003", rollout_id="ro_alpha"))

    results = await store.list(rollout_id="ro_alpha")
    assert len(results) == 2
    assert all(e.rollout_id == "ro_alpha" for e in results)


@pytest.mark.asyncio
async def test_in_memory_list_by_step_id():
    store = InMemoryRolloutHistoryStore()
    await store.append(_make_event(history_event_id="rhe_001", step_id="step_1"))
    await store.append(_make_event(history_event_id="rhe_002", step_id="step_2"))
    await store.append(_make_event(history_event_id="rhe_003", step_id="step_1"))

    results = await store.list(step_id="step_1")
    assert len(results) == 2
    assert all(e.step_id == "step_1" for e in results)


@pytest.mark.asyncio
async def test_in_memory_list_by_event_type():
    store = InMemoryRolloutHistoryStore()
    await store.append(_make_event(
        history_event_id="rhe_001",
        event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
    ))
    await store.append(_make_event(
        history_event_id="rhe_002",
        event_type=RolloutHistoryEventType.STEP_SUCCEEDED,
    ))
    await store.append(_make_event(
        history_event_id="rhe_003",
        event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
    ))

    results = await store.list(event_type=RolloutHistoryEventType.ROLLOUT_STARTED)
    assert len(results) == 2
    assert all(e.event_type == RolloutHistoryEventType.ROLLOUT_STARTED for e in results)


@pytest.mark.asyncio
async def test_in_memory_list_by_window():
    store = InMemoryRolloutHistoryStore()
    t1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)
    await store.append(_make_event(history_event_id="rhe_001", created_at=t1))
    await store.append(_make_event(history_event_id="rhe_002", created_at=t2))
    await store.append(_make_event(history_event_id="rhe_003", created_at=t3))

    window_start = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)
    window_end = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    results = await store.list(window_start=window_start, window_end=window_end)
    assert len(results) == 1
    assert results[0].history_event_id == "rhe_002"


@pytest.mark.asyncio
async def test_in_memory_list_limit():
    store = InMemoryRolloutHistoryStore()
    for i in range(5):
        t = datetime(2026, 1, 1, 10 + i, 0, tzinfo=timezone.utc)
        await store.append(_make_event(history_event_id=f"rhe_{i:03d}", created_at=t))

    results = await store.list(limit=3)
    assert len(results) == 3
    # Should be the first 3 chronologically
    assert results[0].history_event_id == "rhe_000"
    assert results[1].history_event_id == "rhe_001"
    assert results[2].history_event_id == "rhe_002"


# ---------------------------------------------------------------------------
# SQLite tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_append_and_get():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        store = SQLiteRolloutHistoryStore(db_path=db_path)
        event = _make_event()
        result = await store.append(event)
        assert result is event

        fetched = await store.get("rhe_001")
        assert fetched is not None
        assert fetched.history_event_id == "rhe_001"
        assert fetched.rollout_id == "ro_001"
        assert fetched.event_type == RolloutHistoryEventType.ROLLOUT_CREATED
        store.close()


@pytest.mark.asyncio
async def test_sqlite_list_filters():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        store = SQLiteRolloutHistoryStore(db_path=db_path)

        t1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)

        await store.append(_make_event(
            history_event_id="rhe_001",
            rollout_id="ro_alpha",
            event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
            step_id="step_1",
            created_at=t1,
        ))
        await store.append(_make_event(
            history_event_id="rhe_002",
            rollout_id="ro_beta",
            event_type=RolloutHistoryEventType.STEP_SUCCEEDED,
            step_id="step_2",
            created_at=t2,
        ))
        await store.append(_make_event(
            history_event_id="rhe_003",
            rollout_id="ro_alpha",
            event_type=RolloutHistoryEventType.ROLLOUT_STARTED,
            step_id="step_3",
            created_at=t3,
        ))

        # Filter by rollout_id
        results = await store.list(rollout_id="ro_alpha")
        assert len(results) == 2

        # Filter by event_type
        results = await store.list(event_type=RolloutHistoryEventType.ROLLOUT_STARTED)
        assert len(results) == 2

        # Filter by step_id
        results = await store.list(step_id="step_2")
        assert len(results) == 1
        assert results[0].history_event_id == "rhe_002"

        # Filter by time window
        window_start = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)
        window_end = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
        results = await store.list(window_start=window_start, window_end=window_end)
        assert len(results) == 1
        assert results[0].history_event_id == "rhe_002"

        # Combined filter
        results = await store.list(rollout_id="ro_alpha", limit=1)
        assert len(results) == 1

        store.close()


@pytest.mark.asyncio
async def test_sqlite_persists_across_instances():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "persist.db")

        # First instance: write data
        store1 = SQLiteRolloutHistoryStore(db_path=db_path)
        event = _make_event(history_event_id="rhe_persist", rollout_id="ro_persist")
        await store1.append(event)
        store1.close()

        # Second instance: read data back
        store2 = SQLiteRolloutHistoryStore(db_path=db_path)
        fetched = await store2.get("rhe_persist")
        assert fetched is not None
        assert fetched.history_event_id == "rhe_persist"
        assert fetched.rollout_id == "ro_persist"
        store2.close()


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


def test_factory_memory():
    store = create_rollout_history_store(store_type="memory")
    assert isinstance(store, InMemoryRolloutHistoryStore)


def test_factory_sqlite():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "factory.db")
        store = create_rollout_history_store(store_type="sqlite", db_path=db_path)
        assert isinstance(store, SQLiteRolloutHistoryStore)
        store.close()


def test_factory_default_is_memory():
    store = create_rollout_history_store()
    assert isinstance(store, InMemoryRolloutHistoryStore)


def test_protocol_runtime_checkable():
    """Verify the Protocol is runtime-checkable."""
    mem_store = InMemoryRolloutHistoryStore()
    assert isinstance(mem_store, RolloutHistoryStore)
