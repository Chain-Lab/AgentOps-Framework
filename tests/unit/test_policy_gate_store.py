"""Tests for PolicyGateStore (InMemory + SQLite)."""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from datetime import datetime, timezone

from agent_app.runtime.policy_gate_store import (
    InMemoryPolicyGateStore,
    PolicyGateStore,
    SQLitePolicyGateStore,
    create_gate_store,
)
from agent_app.governance.policy_gate import (
    PolicyGateResult,
    PolicyGateStatus,
)


def _make_gate_result(
    gate_result_id: str = "gr_1",
    bundle_id: str = "pb_1",
    replay_id: str = "replay_1",
    status: str = PolicyGateStatus.PASSED,
    total_decisions: int = 100,
    changed_decisions: int = 5,
    failed_replays: int = 0,
    changed_ratio: float = 0.05,
    created_by: str | None = None,
) -> PolicyGateResult:
    """Create a test PolicyGateResult."""
    return PolicyGateResult(
        gate_result_id=gate_result_id,
        bundle_id=bundle_id,
        replay_id=replay_id,
        status=status,
        passed=(status != PolicyGateStatus.FAILED),
        total_decisions=total_decisions,
        changed_decisions=changed_decisions,
        failed_replays=failed_replays,
        changed_ratio=changed_ratio,
        rule_results=[
            {"rule_name": "safe_default", "status": "passed", "failures": []}
        ],
        summary={},
        created_at=datetime.now(timezone.utc),
        created_by=created_by,
    )


def _make_db_path(tmp_path):
    """Create a temp db path."""
    return str(tmp_path / "test_gates.db")


class TestInMemoryPolicyGateStore:
    """Tests for InMemoryPolicyGateStore."""

    async def test_save_and_get(self):
        """Save and retrieve a gate result."""
        store = InMemoryPolicyGateStore()
        result = _make_gate_result("gr_1")
        saved = await store.save(result)
        assert saved.gate_result_id == "gr_1"

        fetched = await store.get("gr_1")
        assert fetched is not None
        assert fetched.bundle_id == "pb_1"

    async def test_get_missing_returns_none(self):
        """Getting a non-existent result returns None."""
        store = InMemoryPolicyGateStore()
        result = await store.get("gr_nonexistent")
        assert result is None

    async def test_list_empty(self):
        """List returns empty list for new store."""
        store = InMemoryPolicyGateStore()
        results = await store.list()
        assert results == []

    async def test_list_returns_results(self):
        """List returns saved results."""
        store = InMemoryPolicyGateStore()
        await store.save(_make_gate_result("gr_1"))
        await store.save(_make_gate_result("gr_2"))
        results = await store.list()
        assert len(results) == 2

    async def test_list_by_bundle_id(self):
        """List can filter by bundle_id."""
        store = InMemoryPolicyGateStore()
        await store.save(_make_gate_result("gr_1", bundle_id="pb_a"))
        await store.save(_make_gate_result("gr_2", bundle_id="pb_b"))
        await store.save(_make_gate_result("gr_3", bundle_id="pb_a"))

        results = await store.list(bundle_id="pb_a")
        assert len(results) == 2
        assert all(r.bundle_id == "pb_a" for r in results)

    async def test_list_with_limit(self):
        """List respects limit parameter."""
        store = InMemoryPolicyGateStore()
        for i in range(5):
            await store.save(_make_gate_result(f"gr_{i}"))
        results = await store.list(limit=2)
        assert len(results) == 2


class TestSQLitePolicyGateStore:
    """Tests for SQLitePolicyGateStore."""

    async def test_persists_across_instances(self, tmp_path):
        """Results survive store recreation."""
        db_path = _make_db_path(tmp_path)
        store1 = SQLitePolicyGateStore(db_path)
        result = _make_gate_result("gr_persist")
        await store1.save(result)
        store1.close()

        store2 = SQLitePolicyGateStore(db_path)
        fetched = await store2.get("gr_persist")
        assert fetched is not None
        assert fetched.bundle_id == "pb_1"
        store2.close()

    async def test_list_all(self, tmp_path):
        """List returns all results."""
        db_path = _make_db_path(tmp_path)
        store = SQLitePolicyGateStore(db_path)
        await store.save(_make_gate_result("gr_1"))
        await store.save(_make_gate_result("gr_2"))
        results = await store.list()
        assert len(results) == 2
        store.close()

    async def test_list_by_bundle_id(self, tmp_path):
        """List can filter by bundle_id."""
        db_path = _make_db_path(tmp_path)
        store = SQLitePolicyGateStore(db_path)
        await store.save(_make_gate_result("gr_1", bundle_id="pb_a"))
        await store.save(_make_gate_result("gr_2", bundle_id="pb_b"))
        await store.save(_make_gate_result("gr_3", bundle_id="pb_a"))

        results = await store.list(bundle_id="pb_a")
        assert len(results) == 2
        assert all(r.bundle_id == "pb_a" for r in results)
        store.close()

    async def test_get_missing_returns_none(self, tmp_path):
        """Getting a non-existent result returns None."""
        db_path = _make_db_path(tmp_path)
        store = SQLitePolicyGateStore(db_path)
        result = await store.get("gr_nonexistent")
        assert result is None
        store.close()

    async def test_save_overwrites(self, tmp_path):
        """Saving with same ID overwrites."""
        db_path = _make_db_path(tmp_path)
        store = SQLitePolicyGateStore(db_path)
        r1 = _make_gate_result("gr_1", status=PolicyGateStatus.PASSED)
        await store.save(r1)
        r2 = _make_gate_result("gr_1", status=PolicyGateStatus.FAILED)
        await store.save(r2)

        fetched = await store.get("gr_1")
        assert fetched.status == PolicyGateStatus.FAILED
        store.close()

    async def test_list_with_limit(self, tmp_path):
        """List respects limit parameter."""
        db_path = _make_db_path(tmp_path)
        store = SQLitePolicyGateStore(db_path)
        for i in range(5):
            await store.save(_make_gate_result(f"gr_{i}"))
        results = await store.list(limit=2)
        assert len(results) == 2
        store.close()


class TestCreateGateStoreFactory:
    """Tests for create_gate_store factory function."""

    def test_memory_store(self):
        """Factory creates InMemory store."""
        store = create_gate_store(store_type="memory")
        assert isinstance(store, InMemoryPolicyGateStore)

    def test_sqlite_store(self, tmp_path):
        """Factory creates SQLite store."""
        db_path = str(tmp_path / "test.db")
        store = create_gate_store(store_type="sqlite", db_path=db_path)
        assert isinstance(store, SQLitePolicyGateStore)
        store.close()

    def test_unknown_type_raises(self):
        """Factory raises ValueError for unknown type."""
        with pytest.raises(ValueError, match="Unknown"):
            create_gate_store(store_type="unknown")
