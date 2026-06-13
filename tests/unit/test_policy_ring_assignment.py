"""Tests for RingActivationAssignment model and store."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_app.governance.policy_ring_assignment import (
    RingActivationAssignment,
    RingActivationAssignmentStatus,
)
from agent_app.runtime.policy_ring_assignment_store import (
    InMemoryRingActivationAssignmentStore,
    SQLiteRingActivationAssignmentStore,
    create_ring_assignment_store,
)


def _make_assignment(
    assignment_id: str = "ra_001",
    environment: str = "production",
    ring_name: str = "canary",
    activation_id: str = "act_100",
    bundle_id: str = "bnd_200",
    config_hash: str = "abc123",
    assigned_by: str = "admin",
    reason: str | None = None,
) -> RingActivationAssignment:
    return RingActivationAssignment(
        assignment_id=assignment_id,
        environment=environment,
        ring_name=ring_name,
        activation_id=activation_id,
        bundle_id=bundle_id,
        config_hash=config_hash,
        assigned_by=assigned_by,
        reason=reason,
    )


# --- InMemory tests ---


class TestInMemoryRingActivationAssignmentStore:
    @pytest.mark.asyncio
    async def test_assign_first(self):
        store = InMemoryRingActivationAssignmentStore()
        assignment = _make_assignment()
        result = await store.assign(assignment)
        assert result.assignment_id == "ra_001"
        assert result.status == RingActivationAssignmentStatus.ACTIVE
        # Retrieve it back
        fetched = await store.get("ra_001")
        assert fetched is not None
        assert fetched.assignment_id == "ra_001"

    @pytest.mark.asyncio
    async def test_assign_second_supersedes_first(self):
        store = InMemoryRingActivationAssignmentStore()
        first = _make_assignment(assignment_id="ra_001", activation_id="act_100")
        await store.assign(first)
        second = _make_assignment(assignment_id="ra_002", activation_id="act_101")
        await store.assign(second)
        # First should be superseded
        fetched_first = await store.get("ra_001")
        assert fetched_first is not None
        assert fetched_first.status == RingActivationAssignmentStatus.SUPERSEDED
        assert fetched_first.superseded_by_assignment_id == "ra_002"
        assert fetched_first.superseded_at is not None
        # Second should be active
        fetched_second = await store.get("ra_002")
        assert fetched_second is not None
        assert fetched_second.status == RingActivationAssignmentStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_get_active(self):
        store = InMemoryRingActivationAssignmentStore()
        await store.assign(_make_assignment(environment="prod", ring_name="canary"))
        await store.assign(
            _make_assignment(
                assignment_id="ra_002",
                environment="prod",
                ring_name="stable",
                activation_id="act_101",
            )
        )
        active = await store.get_active("prod", "canary")
        assert active is not None
        assert active.assignment_id == "ra_001"
        assert active.status == RingActivationAssignmentStatus.ACTIVE
        # No active for unknown ring
        assert await store.get_active("prod", "nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_by_environment_and_ring(self):
        store = InMemoryRingActivationAssignmentStore()
        await store.assign(_make_assignment(environment="prod", ring_name="canary"))
        await store.assign(
            _make_assignment(
                assignment_id="ra_002",
                environment="prod",
                ring_name="stable",
                activation_id="act_101",
            )
        )
        await store.assign(
            _make_assignment(
                assignment_id="ra_003",
                environment="staging",
                ring_name="canary",
                activation_id="act_102",
            )
        )
        # Filter by environment
        prod_assignments = await store.list(environment="prod")
        assert len(prod_assignments) == 2
        # Filter by ring_name
        canary_assignments = await store.list(ring_name="canary")
        assert len(canary_assignments) == 2
        # Filter by both
        prod_canary = await store.list(environment="prod", ring_name="canary")
        assert len(prod_canary) == 1
        # No filters
        all_assignments = await store.list()
        assert len(all_assignments) == 3

    @pytest.mark.asyncio
    async def test_disable_active(self):
        store = InMemoryRingActivationAssignmentStore()
        await store.assign(_make_assignment(environment="prod", ring_name="canary"))
        disabled = await store.disable_active("prod", "canary", disabled_by="ops", reason="rollback")
        assert disabled is not None
        assert disabled.status == RingActivationAssignmentStatus.DISABLED
        # get_active should return None now
        assert await store.get_active("prod", "canary") is None
        # Disabling non-existent returns None
        assert await store.disable_active("prod", "nonexistent", disabled_by="ops") is None


# --- SQLite tests ---


class TestSQLiteRingActivationAssignmentStore:
    @pytest.mark.asyncio
    async def test_assign_and_get_active(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        store = SQLiteRingActivationAssignmentStore(db_path=db_path)
        assignment = _make_assignment()
        result = await store.assign(assignment)
        assert result.assignment_id == "ra_001"
        assert result.status == RingActivationAssignmentStatus.ACTIVE
        active = await store.get_active("production", "canary")
        assert active is not None
        assert active.assignment_id == "ra_001"
        store.close()

    @pytest.mark.asyncio
    async def test_supersede(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        store = SQLiteRingActivationAssignmentStore(db_path=db_path)
        first = _make_assignment(assignment_id="ra_001", activation_id="act_100")
        await store.assign(first)
        second = _make_assignment(assignment_id="ra_002", activation_id="act_101")
        await store.assign(second)
        fetched_first = await store.get("ra_001")
        assert fetched_first is not None
        assert fetched_first.status == RingActivationAssignmentStatus.SUPERSEDED
        assert fetched_first.superseded_by_assignment_id == "ra_002"
        fetched_second = await store.get("ra_002")
        assert fetched_second is not None
        assert fetched_second.status == RingActivationAssignmentStatus.ACTIVE
        store.close()

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        store1 = SQLiteRingActivationAssignmentStore(db_path=db_path)
        await store1.assign(_make_assignment())
        store1.close()
        store2 = SQLiteRingActivationAssignmentStore(db_path=db_path)
        fetched = await store2.get("ra_001")
        assert fetched is not None
        assert fetched.activation_id == "act_100"
        store2.close()

    @pytest.mark.asyncio
    async def test_disable_active(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        store = SQLiteRingActivationAssignmentStore(db_path=db_path)
        await store.assign(_make_assignment())
        disabled = await store.disable_active("production", "canary", disabled_by="ops", reason="rollback")
        assert disabled is not None
        assert disabled.status == RingActivationAssignmentStatus.DISABLED
        assert await store.get_active("production", "canary") is None
        store.close()


# --- Factory tests ---


class TestRingAssignmentStoreFactory:
    def test_memory(self):
        store = create_ring_assignment_store(store_type="memory")
        assert isinstance(store, InMemoryRingActivationAssignmentStore)

    def test_sqlite(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        store = create_ring_assignment_store(store_type="sqlite", db_path=db_path)
        assert isinstance(store, SQLiteRingActivationAssignmentStore)
        store.close()
