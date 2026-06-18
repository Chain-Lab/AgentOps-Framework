from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEvent,
    FederationHistoryEventType,
)
from agent_app.runtime.policy_rollout_federation_history_store import (
    FederationHistoryStore,
    InMemoryFederationHistoryStore,
    SQLiteFederationHistoryStore,
    create_federation_history_store,
)


def _now(offset_seconds: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)


def _make_event(
    history_event_id: str = "fhe_001",
    federation_id: str | None = "fed_a",
    target_id: str | None = "frt_a",
    rollout_id: str | None = "ro_1",
    wave_id: str | None = "wave_1",
    event_type: FederationHistoryEventType = FederationHistoryEventType.FEDERATION_CREATED,
    tenant_id: str | None = "tenant_a",
    environment: str | None = "prod",
    ring_name: str | None = "canary",
    region: str | None = "us-east",
    actor_id: str | None = "actor_1",
    source_type: str | None = "federation",
    source_id: str | None = "fed_a",
    message: str | None = "Federation created",
    metadata: dict | None = None,
    created_at: datetime | None = None,
) -> FederationHistoryEvent:
    return FederationHistoryEvent(
        history_event_id=history_event_id,
        federation_id=federation_id,
        target_id=target_id,
        rollout_id=rollout_id,
        wave_id=wave_id,
        event_type=event_type,
        tenant_id=tenant_id,
        environment=environment,
        ring_name=ring_name,
        region=region,
        actor_id=actor_id,
        source_type=source_type,
        source_id=source_id,
        message=message,
        metadata=metadata or {},
        created_at=created_at or _now(),
    )


# ---------------------------------------------------------------------------
# InMemoryFederationHistoryStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInMemoryFederationHistoryStore:
    async def test_append_and_get(self) -> None:
        store = InMemoryFederationHistoryStore()
        event = _make_event()
        result = await store.append(event)

        assert result == event
        assert await store.get("fhe_001") == event

    async def test_get_missing_returns_none(self) -> None:
        store = InMemoryFederationHistoryStore()
        assert await store.get("fhe_missing") is None

    async def test_list_returns_chronological_order(self) -> None:
        store = InMemoryFederationHistoryStore()
        e1 = _make_event(history_event_id="fhe_001", created_at=_now(0))
        e2 = _make_event(history_event_id="fhe_002", created_at=_now(10))
        e3 = _make_event(history_event_id="fhe_003", created_at=_now(5))

        await store.append(e1)
        await store.append(e2)
        await store.append(e3)

        result = await store.list()
        assert result == [e1, e3, e2]

    async def test_list_filters_by_federation_id(self) -> None:
        store = InMemoryFederationHistoryStore()
        e1 = _make_event(history_event_id="fhe_001", federation_id="fed_a")
        e2 = _make_event(history_event_id="fhe_002", federation_id="fed_b")

        await store.append(e1)
        await store.append(e2)

        assert await store.list(federation_id="fed_a") == [e1]
        assert await store.list(federation_id="fed_b") == [e2]
        assert await store.list(federation_id="fed_c") == []

    async def test_list_filters_by_target_id(self) -> None:
        store = InMemoryFederationHistoryStore()
        e1 = _make_event(history_event_id="fhe_001", target_id="frt_a")
        e2 = _make_event(history_event_id="fhe_002", target_id="frt_b")

        await store.append(e1)
        await store.append(e2)

        assert await store.list(target_id="frt_a") == [e1]
        assert await store.list(target_id="frt_b") == [e2]

    async def test_list_filters_by_rollout_id(self) -> None:
        store = InMemoryFederationHistoryStore()
        e1 = _make_event(history_event_id="fhe_001", rollout_id="ro_1")
        e2 = _make_event(history_event_id="fhe_002", rollout_id="ro_2")

        await store.append(e1)
        await store.append(e2)

        assert await store.list(rollout_id="ro_1") == [e1]
        assert await store.list(rollout_id="ro_2") == [e2]

    async def test_list_filters_by_wave_id(self) -> None:
        store = InMemoryFederationHistoryStore()
        e1 = _make_event(history_event_id="fhe_001", wave_id="wave_1")
        e2 = _make_event(history_event_id="fhe_002", wave_id="wave_2")

        await store.append(e1)
        await store.append(e2)

        assert await store.list(wave_id="wave_1") == [e1]
        assert await store.list(wave_id="wave_2") == [e2]

    async def test_list_filters_by_event_type(self) -> None:
        store = InMemoryFederationHistoryStore()
        e1 = _make_event(
            history_event_id="fhe_001",
            event_type=FederationHistoryEventType.FEDERATION_CREATED,
        )
        e2 = _make_event(
            history_event_id="fhe_002",
            event_type=FederationHistoryEventType.FEDERATION_COMPLETED,
        )

        await store.append(e1)
        await store.append(e2)

        assert await store.list(event_type=FederationHistoryEventType.FEDERATION_CREATED) == [e1]
        assert await store.list(event_type=FederationHistoryEventType.FEDERATION_COMPLETED) == [e2]

    async def test_list_with_limit(self) -> None:
        store = InMemoryFederationHistoryStore()
        for i in range(5):
            await store.append(_make_event(history_event_id=f"fhe_{i:03d}", created_at=_now(i)))

        result = await store.list(limit=3)
        assert len(result) == 3
        assert result[0].history_event_id == "fhe_000"
        assert result[1].history_event_id == "fhe_001"
        assert result[2].history_event_id == "fhe_002"

    async def test_list_by_time_window(self) -> None:
        store = InMemoryFederationHistoryStore()
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        e1 = _make_event(history_event_id="fhe_001", created_at=base)
        e2 = _make_event(history_event_id="fhe_002", created_at=base + timedelta(seconds=10))
        e3 = _make_event(history_event_id="fhe_003", created_at=base + timedelta(seconds=20))

        await store.append(e1)
        await store.append(e2)
        await store.append(e3)

        # Window that includes only e2
        result = await store.list(
            window_start=base + timedelta(seconds=5),
            window_end=base + timedelta(seconds=15),
        )
        assert result == [e2]

        # Window from start includes e1 and e2
        result = await store.list(
            window_start=base,
            window_end=base + timedelta(seconds=10),
        )
        assert result == [e1, e2]

        # Window with only start includes e2 and e3
        result = await store.list(window_start=base + timedelta(seconds=10))
        assert result == [e2, e3]


# ---------------------------------------------------------------------------
# SQLiteFederationHistoryStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSQLiteFederationHistoryStore:
    async def test_sqlite_persistence(self, tmp_path) -> None:
        db_path = tmp_path / "history.db"
        store = SQLiteFederationHistoryStore(str(db_path))
        event = _make_event()
        await store.append(event)
        store.close()

        reopened = SQLiteFederationHistoryStore(str(db_path))
        loaded = await reopened.get("fhe_001")

        assert loaded is not None
        assert loaded.history_event_id == "fhe_001"
        assert loaded.federation_id == "fed_a"
        assert loaded.event_type == FederationHistoryEventType.FEDERATION_CREATED
        assert loaded.metadata == {}
        assert loaded.tenant_id == "tenant_a"
        reopened.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateFederationHistoryStore:
    def test_create_memory(self) -> None:
        store = create_federation_history_store("memory")
        assert isinstance(store, InMemoryFederationHistoryStore)
        assert isinstance(store, FederationHistoryStore)

    def test_create_sqlite(self, tmp_path) -> None:
        db_path = tmp_path / "history.db"
        store = create_federation_history_store("sqlite", str(db_path))
        assert isinstance(store, SQLiteFederationHistoryStore)
        assert isinstance(store, FederationHistoryStore)
        store.close()
