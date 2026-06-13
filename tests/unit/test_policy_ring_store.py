"""Tests for ReleaseRingStore -- Protocol, InMemory, SQLite, factory."""
import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_ring import ReleaseRing, ReleaseRingStatus
from agent_app.runtime.policy_ring_store import (
    InMemoryReleaseRingStore,
    SQLiteReleaseRingStore,
    create_release_ring_store,
)


def _make_ring(
    ring_id: str = "ring_001",
    environment: str = "prod",
    name: str = "stable",
    status: ReleaseRingStatus = ReleaseRingStatus.ENABLED,
    is_default: bool = False,
) -> ReleaseRing:
    return ReleaseRing(
        ring_id=ring_id,
        environment=environment,
        name=name,
        status=status,
        is_default=is_default,
    )


# ── InMemory tests ──────────────────────────────────────────────────


class TestInMemoryReleaseRingStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        store = InMemoryReleaseRingStore()
        ring = _make_ring()
        created = await store.create(ring)
        assert created.ring_id == "ring_001"
        fetched = await store.get("ring_001")
        assert fetched is not None
        assert fetched.ring_id == "ring_001"
        assert fetched.environment == "prod"

    @pytest.mark.asyncio
    async def test_get_by_name(self):
        store = InMemoryReleaseRingStore()
        await store.create(_make_ring(environment="prod", name="stable"))
        result = await store.get_by_name("prod", "stable")
        assert result is not None
        assert result.name == "stable"
        assert result.environment == "prod"
        # Missing combination returns None
        assert await store.get_by_name("prod", "canary") is None

    @pytest.mark.asyncio
    async def test_list_by_environment(self):
        store = InMemoryReleaseRingStore()
        await store.create(_make_ring(ring_id="r1", environment="prod", name="stable"))
        await store.create(_make_ring(ring_id="r2", environment="prod", name="canary"))
        await store.create(_make_ring(ring_id="r3", environment="dev", name="stable"))
        # Filter by environment
        prod_rings = await store.list(environment="prod")
        assert len(prod_rings) == 2
        # No filter
        all_rings = await store.list()
        assert len(all_rings) == 3

    @pytest.mark.asyncio
    async def test_set_default_clears_previous(self):
        store = InMemoryReleaseRingStore()
        await store.create(_make_ring(ring_id="r1", environment="prod", name="stable", is_default=True))
        await store.create(_make_ring(ring_id="r2", environment="prod", name="canary", is_default=False))
        result = await store.set_default("prod", "canary")
        assert result.is_default is True
        # Previous default should be cleared
        stable = await store.get_by_name("prod", "stable")
        assert stable is not None
        assert stable.is_default is False

    @pytest.mark.asyncio
    async def test_disable_and_enable(self):
        store = InMemoryReleaseRingStore()
        await store.create(_make_ring(ring_id="r1", environment="prod", name="stable"))
        disabled = await store.disable("prod", "stable")
        assert disabled.status == ReleaseRingStatus.DISABLED
        enabled = await store.enable("prod", "stable")
        assert enabled.status == ReleaseRingStatus.ENABLED


# ── SQLite tests ─────────────────────────────────────────────────────


class TestSQLiteReleaseRingStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self, tmp_path):
        db = tmp_path / "rings.db"
        store = SQLiteReleaseRingStore(str(db))
        ring = _make_ring()
        created = await store.create(ring)
        assert created.ring_id == "ring_001"
        fetched = await store.get("ring_001")
        assert fetched is not None
        assert fetched.ring_id == "ring_001"

    @pytest.mark.asyncio
    async def test_get_by_name(self, tmp_path):
        db = tmp_path / "rings.db"
        store = SQLiteReleaseRingStore(str(db))
        await store.create(_make_ring(environment="prod", name="stable"))
        result = await store.get_by_name("prod", "stable")
        assert result is not None
        assert result.name == "stable"
        assert await store.get_by_name("prod", "canary") is None

    @pytest.mark.asyncio
    async def test_list_by_environment(self, tmp_path):
        db = tmp_path / "rings.db"
        store = SQLiteReleaseRingStore(str(db))
        await store.create(_make_ring(ring_id="r1", environment="prod", name="stable"))
        await store.create(_make_ring(ring_id="r2", environment="prod", name="canary"))
        await store.create(_make_ring(ring_id="r3", environment="dev", name="stable"))
        prod_rings = await store.list(environment="prod")
        assert len(prod_rings) == 2
        all_rings = await store.list()
        assert len(all_rings) == 3

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, tmp_path):
        db = tmp_path / "rings.db"
        s1 = SQLiteReleaseRingStore(str(db))
        await s1.create(_make_ring(ring_id="r1", environment="prod", name="stable"))
        s2 = SQLiteReleaseRingStore(str(db))
        fetched = await s2.get("r1")
        assert fetched is not None
        assert fetched.ring_id == "r1"
        assert fetched.environment == "prod"

    @pytest.mark.asyncio
    async def test_set_default_clears_previous(self, tmp_path):
        db = tmp_path / "rings.db"
        store = SQLiteReleaseRingStore(str(db))
        await store.create(_make_ring(ring_id="r1", environment="prod", name="stable", is_default=True))
        await store.create(_make_ring(ring_id="r2", environment="prod", name="canary", is_default=False))
        result = await store.set_default("prod", "canary")
        assert result.is_default is True
        stable = await store.get_by_name("prod", "stable")
        assert stable is not None
        assert stable.is_default is False


# ── Factory tests ────────────────────────────────────────────────────


def test_create_release_ring_store_memory():
    assert isinstance(create_release_ring_store("memory"), InMemoryReleaseRingStore)


def test_create_release_ring_store_sqlite(tmp_path):
    assert isinstance(
        create_release_ring_store("sqlite", str(tmp_path / "rings.db")),
        SQLiteReleaseRingStore,
    )


def test_create_release_ring_store_unknown():
    with pytest.raises(ValueError, match="Unknown ring store type"):
        create_release_ring_store("redis")
