"""Tests for Phase 16.5 WorkflowStateStore list_runs filter parameters.

Tests cover:
  - InMemory list_runs by status
  - SQLite list_runs by status
  - SQLite list_runs respects limit
  - SQLite list_runs filters workflow_name
  - SQLite list_runs filters updated_before
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from agent_app.runtime.dag_run_state import WorkflowRunState, WorkflowRunStatus
from agent_app.runtime.dag_state_store import (
    InMemoryWorkflowStateStore,
    SQLiteWorkflowStateStore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(
    run_id: str,
    status: str,
    workflow_name: str = "test_wf",
    updated_at: datetime | None = None,
) -> WorkflowRunState:
    now = updated_at or datetime.now(timezone.utc)
    return WorkflowRunState(
        run_id=run_id,
        workflow_name=workflow_name,
        status=status,
        input="test",
        started_at=now - timedelta(minutes=10),
        updated_at=now,
    )


@pytest.fixture
def memory_store() -> InMemoryWorkflowStateStore:
    return InMemoryWorkflowStateStore()


@pytest.fixture
def sqlite_store(tmp_path: Path) -> SQLiteWorkflowStateStore:
    db_path = str(tmp_path / "test_list_runs.db")
    return SQLiteWorkflowStateStore(db_path=db_path)


async def _populate_store(
    store: InMemoryWorkflowStateStore | SQLiteWorkflowStateStore,
) -> None:
    """Add 10 runs with mixed statuses and workflow names."""
    now = datetime.now(timezone.utc)
    statuses = [
        WorkflowRunStatus.RUNNING.value,
        WorkflowRunStatus.FAILED.value,
        WorkflowRunStatus.COMPLETED.value,
        WorkflowRunStatus.RUNNING.value,
        WorkflowRunStatus.FAILED.value,
        WorkflowRunStatus.RUNNING.value,
        WorkflowRunStatus.COMPLETED.value,
        WorkflowRunStatus.FAILED.value,
        WorkflowRunStatus.RUNNING.value,
        WorkflowRunStatus.COMPLETED.value,
    ]
    workflow_names = [
        "wf_alpha", "wf_beta", "wf_alpha", "wf_beta", "wf_alpha",
        "wf_beta", "wf_alpha", "wf_beta", "wf_alpha", "wf_beta",
    ]
    for i in range(10):
        updated = now - timedelta(minutes=i * 5)
        run = _make_run(
            run_id=f"run-{i:03d}",
            status=statuses[i],
            workflow_name=workflow_names[i],
            updated_at=updated,
        )
        await store.create_run(run)


# ---------------------------------------------------------------------------
# InMemory tests
# ---------------------------------------------------------------------------


class TestInMemoryListRuns:
    @pytest.mark.asyncio
    async def test_list_runs_by_status(self, memory_store):
        await _populate_store(memory_store)
        failed = await memory_store.list_runs(
            statuses=[WorkflowRunStatus.FAILED.value]
        )
        assert len(failed) == 3
        for run in failed:
            assert run.status == WorkflowRunStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_list_runs_limit(self, memory_store):
        await _populate_store(memory_store)
        runs = await memory_store.list_runs(limit=3)
        assert len(runs) == 3

    @pytest.mark.asyncio
    async def test_list_runs_workflow_name(self, memory_store):
        await _populate_store(memory_store)
        alpha = await memory_store.list_runs(workflow_name="wf_alpha")
        assert len(alpha) == 5
        for run in alpha:
            assert run.workflow_name == "wf_alpha"

    @pytest.mark.asyncio
    async def test_list_runs_combined_filters(self, memory_store):
        await _populate_store(memory_store)
        # Failed runs in wf_alpha (only 1 in the test data)
        runs = await memory_store.list_runs(
            statuses=[WorkflowRunStatus.FAILED.value],
            workflow_name="wf_alpha",
        )
        assert len(runs) == 1
        for run in runs:
            assert run.status == WorkflowRunStatus.FAILED.value
            assert run.workflow_name == "wf_alpha"

    @pytest.mark.asyncio
    async def test_list_runs_sorted_by_updated_desc(self, memory_store):
        await _populate_store(memory_store)
        runs = await memory_store.list_runs()
        assert len(runs) == 10
        # Should be sorted by updated_at descending
        for i in range(len(runs) - 1):
            assert runs[i].updated_at >= runs[i + 1].updated_at


# ---------------------------------------------------------------------------
# SQLite tests
# ---------------------------------------------------------------------------


class TestSQLiteListRuns:
    @pytest.mark.asyncio
    async def test_list_runs_by_status(self, sqlite_store):
        await _populate_store(sqlite_store)
        failed = await sqlite_store.list_runs(
            statuses=[WorkflowRunStatus.FAILED.value]
        )
        assert len(failed) == 3
        for run in failed:
            assert run.status == WorkflowRunStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_list_runs_respects_limit(self, sqlite_store):
        await _populate_store(sqlite_store)
        runs = await sqlite_store.list_runs(limit=3)
        assert len(runs) == 3

    @pytest.mark.asyncio
    async def test_list_runs_filters_workflow_name(self, sqlite_store):
        await _populate_store(sqlite_store)
        alpha = await sqlite_store.list_runs(workflow_name="wf_alpha")
        assert len(alpha) == 5
        for run in alpha:
            assert run.workflow_name == "wf_alpha"

    @pytest.mark.asyncio
    async def test_list_runs_filters_updated_before(self, sqlite_store):
        await _populate_store(sqlite_store)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
        runs = await sqlite_store.list_runs(updated_before=cutoff)
        # Only runs updated more than 15 minutes ago
        for run in runs:
            assert run.updated_at < cutoff

    @pytest.mark.asyncio
    async def test_list_runs_cross_instance(self, tmp_path: Path):
        """Verify filtering works across separate SQLite instances."""
        db_path = str(tmp_path / "cross_instance.db")
        store1 = SQLiteWorkflowStateStore(db_path=db_path)
        now = datetime.now(timezone.utc)
        import asyncio
        # Write through store1
        for i in range(5):
            run = _make_run(
                run_id=f"ci-{i}",
                status=WorkflowRunStatus.FAILED.value,
                updated_at=now - timedelta(minutes=i),
            )
            await store1.create_run(run)

        # Read through store2 (same DB file)
        store2 = SQLiteWorkflowStateStore(db_path=db_path)
        runs = await store2.list_runs(
            statuses=[WorkflowRunStatus.FAILED.value],
            limit=3,
        )
        assert len(runs) == 3
        for run in runs:
            assert run.status == WorkflowRunStatus.FAILED.value
