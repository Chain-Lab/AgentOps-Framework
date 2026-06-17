"""Tests for ReleaseGateRequirementStore — InMemory, SQLite, and factory.

Phase 42 Task 2: Policy Release Automation and Simulation Gate Enforcement.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_app.governance.policy_release_gate import (
    ReleaseGateRequirement,
    ReleaseGateRequirementStatus,
)
from agent_app.runtime.policy_release_gate_store import (
    InMemoryReleaseGateRequirementStore,
    ReleaseGateRequirementStore,
    SQLiteReleaseGateRequirementStore,
    create_release_gate_requirement_store,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_requirement(
    requirement_id: str = "rgr_001",
    source_type: str = "promotion",
    source_id: str = "promo_100",
    status: ReleaseGateRequirementStatus = ReleaseGateRequirementStatus.REQUIRED,
    **overrides,
) -> ReleaseGateRequirement:
    """Create a ReleaseGateRequirement with sensible defaults."""
    data = dict(
        requirement_id=requirement_id,
        source_type=source_type,
        source_id=source_id,
        status=status,
    )
    data.update(overrides)
    return ReleaseGateRequirement(**data)


# ===========================================================================
# InMemoryReleaseGateRequirementStore
# ===========================================================================

class TestInMemoryReleaseGateRequirementStore:

    @pytest.fixture()
    def store(self) -> InMemoryReleaseGateRequirementStore:
        return InMemoryReleaseGateRequirementStore()

    @pytest.mark.asyncio
    async def test_create_and_get(self, store: InMemoryReleaseGateRequirementStore):
        req = _make_requirement()
        created = await store.create(req)
        assert created.requirement_id == "rgr_001"

        fetched = await store.get("rgr_001")
        assert fetched is not None
        assert fetched.requirement_id == "rgr_001"
        assert fetched.source_type == "promotion"
        assert fetched.source_id == "promo_100"

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self, store: InMemoryReleaseGateRequirementStore):
        assert await store.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_for_source(self, store: InMemoryReleaseGateRequirementStore):
        req = _make_requirement(source_type="rollout_step", source_id="step_42")
        await store.create(req)

        found = await store.get_for_source("rollout_step", "step_42")
        assert found is not None
        assert found.requirement_id == "rgr_001"

        assert await store.get_for_source("promotion", "step_42") is None
        assert await store.get_for_source("rollout_step", "other") is None

    @pytest.mark.asyncio
    async def test_update(self, store: InMemoryReleaseGateRequirementStore):
        req = _make_requirement()
        await store.create(req)

        req.status = ReleaseGateRequirementStatus.SATISFIED
        req.gate_result_id = "gr_999"
        updated = await store.update(req)
        assert updated.status == ReleaseGateRequirementStatus.SATISFIED
        assert updated.gate_result_id == "gr_999"

        fetched = await store.get("rgr_001")
        assert fetched is not None
        assert fetched.status == ReleaseGateRequirementStatus.SATISFIED

    @pytest.mark.asyncio
    async def test_update_raises_for_missing(self, store: InMemoryReleaseGateRequirementStore):
        req = _make_requirement()
        with pytest.raises(KeyError):
            await store.update(req)

    @pytest.mark.asyncio
    async def test_list_by_source_type(self, store: InMemoryReleaseGateRequirementStore):
        await store.create(_make_requirement(requirement_id="rgr_1", source_type="promotion", source_id="p1"))
        await store.create(_make_requirement(requirement_id="rgr_2", source_type="rollout_step", source_id="s1"))
        await store.create(_make_requirement(requirement_id="rgr_3", source_type="promotion", source_id="p2"))

        promos = await store.list(source_type="promotion")
        assert len(promos) == 2
        assert {r.requirement_id for r in promos} == {"rgr_1", "rgr_3"}

        steps = await store.list(source_type="rollout_step")
        assert len(steps) == 1
        assert steps[0].requirement_id == "rgr_2"

    @pytest.mark.asyncio
    async def test_list_by_status(self, store: InMemoryReleaseGateRequirementStore):
        await store.create(_make_requirement(requirement_id="rgr_1", status=ReleaseGateRequirementStatus.REQUIRED))
        await store.create(_make_requirement(requirement_id="rgr_2", status=ReleaseGateRequirementStatus.SATISFIED, source_id="s2"))
        await store.create(_make_requirement(requirement_id="rgr_3", status=ReleaseGateRequirementStatus.REQUIRED, source_id="s3"))

        required = await store.list(status=ReleaseGateRequirementStatus.REQUIRED)
        assert len(required) == 2

        satisfied = await store.list(status=ReleaseGateRequirementStatus.SATISFIED)
        assert len(satisfied) == 1

    @pytest.mark.asyncio
    async def test_list_no_filter(self, store: InMemoryReleaseGateRequirementStore):
        await store.create(_make_requirement(requirement_id="rgr_1", source_id="s1"))
        await store.create(_make_requirement(requirement_id="rgr_2", source_id="s2"))

        all_reqs = await store.list()
        assert len(all_reqs) == 2

    @pytest.mark.asyncio
    async def test_unique_source_overwrites(self, store: InMemoryReleaseGateRequirementStore):
        """Creating a requirement with the same source_type+source_id overwrites the old one."""
        req1 = _make_requirement(requirement_id="rgr_old", source_type="promotion", source_id="promo_1")
        await store.create(req1)

        req2 = _make_requirement(requirement_id="rgr_new", source_type="promotion", source_id="promo_1",
                                 status=ReleaseGateRequirementStatus.SATISFIED)
        await store.create(req2)

        # Old ID should be gone
        assert await store.get("rgr_old") is None

        # New ID should be accessible
        fetched = await store.get("rgr_new")
        assert fetched is not None
        assert fetched.status == ReleaseGateRequirementStatus.SATISFIED

        # get_for_source should return the new one
        by_source = await store.get_for_source("promotion", "promo_1")
        assert by_source is not None
        assert by_source.requirement_id == "rgr_new"


# ===========================================================================
# SQLiteReleaseGateRequirementStore
# ===========================================================================

class TestSQLiteReleaseGateRequirementStore:

    @pytest.fixture()
    def store(self, tmp_path: Path) -> SQLiteReleaseGateRequirementStore:
        db_path = str(tmp_path / "test_release_gate.db")
        return SQLiteReleaseGateRequirementStore(db_path=db_path)

    @pytest.mark.asyncio
    async def test_create_and_get(self, store: SQLiteReleaseGateRequirementStore):
        req = _make_requirement()
        created = await store.create(req)
        assert created.requirement_id == "rgr_001"

        fetched = await store.get("rgr_001")
        assert fetched is not None
        assert fetched.requirement_id == "rgr_001"
        assert fetched.source_type == "promotion"
        assert fetched.source_id == "promo_100"
        assert fetched.status == ReleaseGateRequirementStatus.REQUIRED
        assert fetched.required is True

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self, store: SQLiteReleaseGateRequirementStore):
        assert await store.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_for_source(self, store: SQLiteReleaseGateRequirementStore):
        req = _make_requirement(source_type="rollout_step", source_id="step_42")
        await store.create(req)

        found = await store.get_for_source("rollout_step", "step_42")
        assert found is not None
        assert found.requirement_id == "rgr_001"

        assert await store.get_for_source("promotion", "step_42") is None

    @pytest.mark.asyncio
    async def test_update(self, store: SQLiteReleaseGateRequirementStore):
        req = _make_requirement()
        await store.create(req)

        req.status = ReleaseGateRequirementStatus.FAILED
        req.gate_result_id = "gr_fail"
        updated = await store.update(req)
        assert updated.status == ReleaseGateRequirementStatus.FAILED

        fetched = await store.get("rgr_001")
        assert fetched is not None
        assert fetched.status == ReleaseGateRequirementStatus.FAILED
        assert fetched.gate_result_id == "gr_fail"

    @pytest.mark.asyncio
    async def test_update_raises_for_missing(self, store: SQLiteReleaseGateRequirementStore):
        req = _make_requirement()
        with pytest.raises(KeyError):
            await store.update(req)

    @pytest.mark.asyncio
    async def test_list_by_source_type(self, store: SQLiteReleaseGateRequirementStore):
        await store.create(_make_requirement(requirement_id="rgr_1", source_type="promotion", source_id="p1"))
        await store.create(_make_requirement(requirement_id="rgr_2", source_type="rollout_step", source_id="s1"))
        await store.create(_make_requirement(requirement_id="rgr_3", source_type="promotion", source_id="p2"))

        promos = await store.list(source_type="promotion")
        assert len(promos) == 2

        steps = await store.list(source_type="rollout_step")
        assert len(steps) == 1

    @pytest.mark.asyncio
    async def test_list_by_status(self, store: SQLiteReleaseGateRequirementStore):
        await store.create(_make_requirement(requirement_id="rgr_1", status=ReleaseGateRequirementStatus.REQUIRED))
        await store.create(_make_requirement(requirement_id="rgr_2", status=ReleaseGateRequirementStatus.SATISFIED, source_id="s2"))
        await store.create(_make_requirement(requirement_id="rgr_3", status=ReleaseGateRequirementStatus.EXPIRED, source_id="s3"))

        required = await store.list(status=ReleaseGateRequirementStatus.REQUIRED)
        assert len(required) == 1
        assert required[0].requirement_id == "rgr_1"

        satisfied = await store.list(status=ReleaseGateRequirementStatus.SATISFIED)
        assert len(satisfied) == 1

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, tmp_path: Path):
        db_path = str(tmp_path / "persist_test.db")
        store1 = SQLiteReleaseGateRequirementStore(db_path=db_path)
        req = _make_requirement(requirement_id="rgr_persist", metadata={"key": "value"})
        await store1.create(req)
        store1.close()

        store2 = SQLiteReleaseGateRequirementStore(db_path=db_path)
        fetched = await store2.get("rgr_persist")
        assert fetched is not None
        assert fetched.requirement_id == "rgr_persist"
        assert fetched.metadata == {"key": "value"}
        store2.close()

    @pytest.mark.asyncio
    async def test_unique_source_overwrites(self, store: SQLiteReleaseGateRequirementStore):
        """INSERT OR REPLACE on same source_type+source_id overwrites the old row."""
        req1 = _make_requirement(requirement_id="rgr_old", source_type="promotion", source_id="promo_1")
        await store.create(req1)

        req2 = _make_requirement(requirement_id="rgr_new", source_type="promotion", source_id="promo_1",
                                 status=ReleaseGateRequirementStatus.SATISFIED)
        await store.create(req2)

        # Old ID should be gone (replaced)
        assert await store.get("rgr_old") is None

        # New ID should be accessible
        fetched = await store.get("rgr_new")
        assert fetched is not None
        assert fetched.status == ReleaseGateRequirementStatus.SATISFIED

        # get_for_source should return the new one
        by_source = await store.get_for_source("promotion", "promo_1")
        assert by_source is not None
        assert by_source.requirement_id == "rgr_new"

    @pytest.mark.asyncio
    async def test_metadata_roundtrip(self, store: SQLiteReleaseGateRequirementStore):
        req = _make_requirement(metadata={"env": "staging", "tags": ["a", "b"]})
        await store.create(req)

        fetched = await store.get("rgr_001")
        assert fetched is not None
        assert fetched.metadata == {"env": "staging", "tags": ["a", "b"]}

    @pytest.mark.asyncio
    async def test_datetime_fields_roundtrip(self, store: SQLiteReleaseGateRequirementStore):
        now = datetime(2026, 6, 16, 12, 30, 0, tzinfo=timezone.utc)
        req = _make_requirement(
            created_at=now,
            satisfied_at=datetime(2026, 6, 16, 13, 0, 0, tzinfo=timezone.utc),
            status=ReleaseGateRequirementStatus.SATISFIED,
        )
        await store.create(req)

        fetched = await store.get("rgr_001")
        assert fetched is not None
        assert fetched.created_at == now
        assert fetched.satisfied_at is not None
        assert fetched.satisfied_at.year == 2026

    @pytest.mark.asyncio
    async def test_optional_fields_null(self, store: SQLiteReleaseGateRequirementStore):
        req = _make_requirement(
            gate_result_id=None,
            simulation_id=None,
            max_age_seconds=None,
            satisfied_at=None,
        )
        await store.create(req)

        fetched = await store.get("rgr_001")
        assert fetched is not None
        assert fetched.gate_result_id is None
        assert fetched.simulation_id is None
        assert fetched.max_age_seconds is None
        assert fetched.satisfied_at is None


# ===========================================================================
# Factory function
# ===========================================================================

class TestCreateReleaseGateRequirementStore:

    def test_create_memory(self):
        store = create_release_gate_requirement_store("memory")
        assert isinstance(store, InMemoryReleaseGateRequirementStore)

    def test_create_sqlite(self, tmp_path: Path):
        db_path = str(tmp_path / "factory_test.db")
        store = create_release_gate_requirement_store("sqlite", path=db_path)
        assert isinstance(store, SQLiteReleaseGateRequirementStore)
        store.close()

    def test_default_is_memory(self):
        store = create_release_gate_requirement_store()
        assert isinstance(store, InMemoryReleaseGateRequirementStore)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown release gate requirement store type"):
            create_release_gate_requirement_store("redis")
