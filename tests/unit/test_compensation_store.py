"""Tests for compensation state store (Phase 16.1)."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_app.runtime.compensation_state import (
    CompensationActionState,
    CompensationActionStatus,
    CompensationExecutionState,
    CompensationRunStatus,
    deserialize_compensation_state,
)
from agent_app.runtime.compensation_store import (
    InMemoryCompensationStateStore,
    SQLiteCompensationStateStore,
    create_compensation_state_store,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(
    run_id: str = "run-1",
    workflow_name: str = "test-workflow",
    status: str = CompensationRunStatus.PENDING.value,
) -> CompensationExecutionState:
    return CompensationExecutionState(
        run_id=run_id,
        workflow_name=workflow_name,
        status=status,
    )


def _add_action(
    state: CompensationExecutionState,
    action_id: str = "action_1",
    node_id: str = "node-1",
    compensating_for_node_id: str = "node-1",
    status: str = CompensationActionStatus.PENDING.value,
) -> None:
    action = CompensationActionState(
        action_id=action_id,
        run_id=state.run_id,
        workflow_name=state.workflow_name,
        node_id=node_id,
        compensating_for_node_id=compensating_for_node_id,
        status=status,
    )
    state.add_action(action)


def _make_action(
    action_id: str = "action_1",
    run_id: str = "run-1",
    node_id: str = "node-1",
    compensating_for_node_id: str = "node-1",
    status: str = CompensationActionStatus.PENDING.value,
    attempts: int = 0,
    max_attempts: int = 1,
) -> CompensationActionState:
    return CompensationActionState(
        action_id=action_id,
        run_id=run_id,
        node_id=node_id,
        compensating_for_node_id=compensating_for_node_id,
        status=status,
        attempts=attempts,
        max_attempts=max_attempts,
    )


# ---------------------------------------------------------------------------
# InMemory CompensationStateStore tests
# ---------------------------------------------------------------------------

class TestInMemoryCompensationStore:
    @pytest.fixture()
    def store(self) -> InMemoryCompensationStateStore:
        return InMemoryCompensationStateStore()

    @pytest.mark.asyncio
    async def test_save_and_get(self, store: InMemoryCompensationStateStore) -> None:
        state = _make_state(run_id="run-1")
        _add_action(state, action_id="a1", node_id="node-1")
        saved = await store.save_compensation_state(state)
        assert saved.compensation_id == state.compensation_id

        retrieved = await store.get_compensation_state("run-1")
        assert retrieved is not None
        assert retrieved.run_id == "run-1"
        assert retrieved.workflow_name == "test-workflow"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store: InMemoryCompensationStateStore) -> None:
        result = await store.get_compensation_state("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_action(self, store: InMemoryCompensationStateStore) -> None:
        state = _make_state(run_id="run-1")
        _add_action(state, action_id="a1", node_id="node-1")
        await store.save_compensation_state(state)

        # Update the action
        action = state.get_action("a1")
        action.mark_completed(output={"result": "ok"})
        updated = await store.update_compensation_action("run-1", action)

        assert updated.actions["a1"].status == "completed"
        assert updated.actions["a1"].output == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_update_action_missing_run(self, store: InMemoryCompensationStateStore) -> None:
        action = _make_action(action_id="a1")
        with pytest.raises(KeyError, match="No compensation state found"):
            await store.update_compensation_action("nonexistent", action)

    @pytest.mark.asyncio
    async def test_update_action_missing_action(self, store: InMemoryCompensationStateStore) -> None:
        state = _make_state(run_id="run-1")
        await store.save_compensation_state(state)
        action = _make_action(action_id="nonexistent", run_id="run-1")
        with pytest.raises(KeyError, match="not found"):
            await store.update_compensation_action("run-1", action)

    @pytest.mark.asyncio
    async def test_list_states(self, store: InMemoryCompensationStateStore) -> None:
        for i in range(3):
            state = _make_state(run_id=f"run-{i}")
            _add_action(state, action_id=f"a{i}", node_id=f"node-{i}")
            await store.save_compensation_state(state)

        all_states = await store.list_compensation_states()
        assert len(all_states) == 3

    @pytest.mark.asyncio
    async def test_list_states_filter_by_workflow(self, store: InMemoryCompensationStateStore) -> None:
        await store.save_compensation_state(_make_state(run_id="run-1", workflow_name="wf-a"))
        await store.save_compensation_state(_make_state(run_id="run-2", workflow_name="wf-b"))
        await store.save_compensation_state(_make_state(run_id="run-3", workflow_name="wf-a"))

        wf_a = await store.list_compensation_states(workflow_name="wf-a")
        assert len(wf_a) == 2
        assert all(s.workflow_name == "wf-a" for s in wf_a)

    @pytest.mark.asyncio
    async def test_different_run_id_isolation(self, store: InMemoryCompensationStateStore) -> None:
        state_a = _make_state(run_id="run-a")
        _add_action(state_a, action_id="a1", node_id="node-1")
        state_b = _make_state(run_id="run-b")
        _add_action(state_b, action_id="b1", node_id="node-2")

        await store.save_compensation_state(state_a)
        await store.save_compensation_state(state_b)

        a = await store.get_compensation_state("run-a")
        b = await store.get_compensation_state("run-b")
        assert a.run_id == "run-a"
        assert b.run_id == "run-b"
        assert a.actions["a1"].node_id == "node-1"
        assert b.actions["b1"].node_id == "node-2"

    @pytest.mark.asyncio
    async def test_overwrite_same_run_id(self, store: InMemoryCompensationStateStore) -> None:
        state = _make_state(run_id="run-1")
        _add_action(state, action_id="a1", node_id="node-1")
        await store.save_compensation_state(state)

        # Overwrite with new status
        state.status = CompensationRunStatus.RUNNING.value
        _add_action(state, action_id="a2", node_id="node-2")
        await store.save_compensation_state(state)

        retrieved = await store.get_compensation_state("run-1")
        assert retrieved.status == CompensationRunStatus.RUNNING.value
        assert len(retrieved.actions) == 2

    @pytest.mark.asyncio
    async def test_delete_compensation_state(self, store: InMemoryCompensationStateStore) -> None:
        state = _make_state(run_id="run-1")
        _add_action(state)
        await store.save_compensation_state(state)

        await store.delete_compensation_state("run-1")
        result = await store.get_compensation_state("run-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_safe(self, store: InMemoryCompensationStateStore) -> None:
        # Should not raise
        await store.delete_compensation_state("nonexistent")


# ---------------------------------------------------------------------------
# SQLite CompensationStateStore tests
# ---------------------------------------------------------------------------

class TestSQLiteCompensationStore:
    @pytest.fixture()
    def db_path(self, tmp_path: Any) -> str:
        return str(tmp_path / "test_compensation.db")

    @pytest.fixture()
    def store(self, db_path: str) -> SQLiteCompensationStateStore:
        return SQLiteCompensationStateStore(db_path=db_path)

    @pytest.mark.asyncio
    async def test_auto_creates_table(self, store: SQLiteCompensationStateStore, db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='dag_compensation_states'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1

    @pytest.mark.asyncio
    async def test_save_and_get(self, store: SQLiteCompensationStateStore) -> None:
        state = _make_state(run_id="run-1")
        _add_action(state, action_id="a1", node_id="node-1")
        saved = await store.save_compensation_state(state)

        retrieved = await store.get_compensation_state("run-1")
        assert retrieved is not None
        assert retrieved.run_id == "run-1"
        assert retrieved.workflow_name == "test-workflow"
        assert "a1" in retrieved.actions

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store: SQLiteCompensationStateStore) -> None:
        result = await store.get_compensation_state("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_action(self, store: SQLiteCompensationStateStore) -> None:
        state = _make_state(run_id="run-1")
        _add_action(state, action_id="a1", node_id="node-1")
        await store.save_compensation_state(state)

        action = state.get_action("a1")
        action.mark_completed(output={"result": "ok"})
        updated = await store.update_compensation_action("run-1", action)

        assert updated.actions["a1"].status == "completed"
        assert updated.actions["a1"].output == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_list_states(self, store: SQLiteCompensationStateStore) -> None:
        for i in range(3):
            state = _make_state(run_id=f"run-{i}")
            _add_action(state, action_id=f"a{i}", node_id=f"node-{i}")
            await store.save_compensation_state(state)

        all_states = await store.list_compensation_states()
        assert len(all_states) == 3

    @pytest.mark.asyncio
    async def test_list_states_filter_by_workflow(self, store: SQLiteCompensationStateStore) -> None:
        await store.save_compensation_state(
            _make_state(run_id="run-1", workflow_name="wf-a")
        )
        await store.save_compensation_state(
            _make_state(run_id="run-2", workflow_name="wf-b")
        )
        await store.save_compensation_state(
            _make_state(run_id="run-3", workflow_name="wf-a")
        )

        wf_a = await store.list_compensation_states(workflow_name="wf-a")
        assert len(wf_a) == 2

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, db_path: str) -> None:
        from agent_app.runtime.compensation_store import SQLiteCompensationStateStore

        state = _make_state(run_id="run-persist")
        _add_action(state, action_id="a1", node_id="node-1")
        store1 = SQLiteCompensationStateStore(db_path=db_path)
        await store1.save_compensation_state(state)

        # New instance should see the state
        store2 = SQLiteCompensationStateStore(db_path=db_path)
        retrieved = await store2.get_compensation_state("run-persist")
        assert retrieved is not None
        assert retrieved.run_id == "run-persist"
        assert "a1" in retrieved.actions

    @pytest.mark.asyncio
    async def test_different_run_id_isolation(self, store: SQLiteCompensationStateStore) -> None:
        state_a = _make_state(run_id="run-a")
        _add_action(state_a, action_id="a1", node_id="node-1")
        state_b = _make_state(run_id="run-b")
        _add_action(state_b, action_id="b1", node_id="node-2")

        await store.save_compensation_state(state_a)
        await store.save_compensation_state(state_b)

        a = await store.get_compensation_state("run-a")
        b = await store.get_compensation_state("run-b")
        assert a.run_id == "run-a"
        assert b.run_id == "run-b"

    @pytest.mark.asyncio
    async def test_corrupted_json_returns_stable_error(
        self, store: SQLiteCompensationStateStore, db_path: str
    ) -> None:
        # Insert corrupted JSON directly
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO dag_compensation_states
                (compensation_id, run_id, workflow_name, status,
                 schema_version, state_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bad-comp",
                "run-bad",
                "wf",
                "running",
                1,
                "NOT VALID JSON {{{",
                "2024-01-01T00:00:00+00:00",
                "2024-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        conn.close()

        # Should skip corrupted entry in list
        states = await store.list_compensation_states()
        assert len(states) == 0

    @pytest.mark.asyncio
    async def test_delete_compensation_state(self, store: SQLiteCompensationStateStore) -> None:
        state = _make_state(run_id="run-1")
        _add_action(state)
        await store.save_compensation_state(state)

        await store.delete_compensation_state("run-1")
        result = await store.get_compensation_state("run-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_overwrite_same_compensation_id(
        self, store: SQLiteCompensationStateStore
    ) -> None:
        state = _make_state(run_id="run-1")
        _add_action(state, action_id="a1", node_id="node-1")
        await store.save_compensation_state(state)

        # Modify and save again (same compensation_id)
        state.status = CompensationRunStatus.RUNNING.value
        _add_action(state, action_id="a2", node_id="node-2")
        await store.save_compensation_state(state)

        retrieved = await store.get_compensation_state("run-1")
        assert retrieved.status == CompensationRunStatus.RUNNING.value
        assert len(retrieved.actions) == 2


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------

class TestCompensationStoreFactory:
    def test_create_memory_store(self) -> None:
        store = create_compensation_state_store("memory")
        assert isinstance(store, InMemoryCompensationStateStore)

    def test_create_sqlite_store(self, tmp_path: Any) -> None:
        db = str(tmp_path / "test.db")
        store = create_compensation_state_store("sqlite", db_path=db)
        assert isinstance(store, SQLiteCompensationStateStore)

    def test_create_sqlite_store_no_path_raises(self) -> None:
        with pytest.raises(ValueError, match="db_path is required"):
            create_compensation_state_store("sqlite")

    def test_create_unknown_store_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown.*store type"):
            create_compensation_state_store("redis")
