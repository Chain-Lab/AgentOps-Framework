"""Tests for DagExecutor snapshot integration (Phase 16.0)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.runtime.dag_snapshot import (
    DagRunSnapshot,
    SnapshotWriteError,
)
from agent_app.workflows.dag import (
    DagExecutor,
    DagWorkflow,
    DagNode,
    NodeType,
    NodeExecutionStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor(
    state_store: Any = None,
    snapshot_config: Any = None,
) -> DagExecutor:
    """Create a minimal DagExecutor for testing."""
    return DagExecutor(
        agent_registry=MagicMock(),
        tool_registry=MagicMock(),
        workflow_registry=MagicMock(),
        state_store=state_store,
        run_id="test-run-1",
        snapshot_config=snapshot_config,
    )


def _make_simple_dag(name: str = "test-dag") -> DagWorkflow:
    """Create a simple 3-node DAG for testing."""
    return DagWorkflow(
        name=name,
        nodes=[
            DagNode(id="n1", type=NodeType.AGENT, ref="agent-a", depends_on=[]),
            DagNode(id="n2", type=NodeType.AGENT, ref="agent-b", depends_on=["n1"]),
            DagNode(id="n3", type=NodeType.AGENT, ref="agent-c", depends_on=["n2"]),
        ],
    )


def _make_context(run_id: str = "test-run-1") -> MagicMock:
    """Create a minimal RunContext mock."""
    ctx = MagicMock()
    ctx.run_id = run_id
    ctx.trace_id = "trace-1"
    ctx.user_id = "user-1"
    ctx.tenant_id = "tenant-1"
    ctx.session_id = "session-1"
    ctx.permissions = []
    return ctx


def _setup_store_mocks(store: MagicMock) -> None:
    """Set up common async mocks on a state store."""
    store.create_run = AsyncMock()
    store.upsert_node = AsyncMock()
    store.save_run_snapshot = AsyncMock()
    store.acquire_run_lease = AsyncMock()
    store.release_run_lease = AsyncMock()
    store.append_event = AsyncMock()
    store.get_run = AsyncMock()
    store.update_run = AsyncMock()
    store.build_resume_plan = AsyncMock()
    store.get_latest_run_snapshot = AsyncMock()
    store.list_run_snapshots = AsyncMock(return_value=[])
    store.delete_run_snapshots = AsyncMock()


# ---------------------------------------------------------------------------
# Tests for snapshot integration in execute()
# ---------------------------------------------------------------------------


class TestExecuteSnapshotIntegration:
    """Tests for snapshot writing during DAG execution."""

    @pytest.mark.asyncio
    async def test_execute_saves_initial_running_snapshot(self) -> None:
        """execute() should save a 'running' snapshot after lease acquire."""
        store = MagicMock()
        _setup_store_mocks(store)
        executor = _make_executor(state_store=store)

        dag = _make_simple_dag()

        async def mock_execute_node(node, *args, **kwargs):
            return (
                f"output-of-{node.id}",
                NodeExecutionStatus.COMPLETED.value,
                None,
            )

        executor._execute_node = mock_execute_node
        executor._record_node_event = AsyncMock()

        ctx = _make_context()
        await executor.execute(dag=dag, input="test", context=ctx)

        # Verify save_run_snapshot was called at least once
        assert store.save_run_snapshot.call_count >= 1
        # First call should be the initial running snapshot
        first_snapshot = store.save_run_snapshot.call_args_list[0].args[0]
        assert isinstance(first_snapshot, DagRunSnapshot)
        assert first_snapshot.status == "running"
        assert first_snapshot.run_id == "test-run-1"

    @pytest.mark.asyncio
    async def test_execute_saves_completion_snapshot(self) -> None:
        """execute() should save a 'completed' snapshot on successful finish."""
        store = MagicMock()
        _setup_store_mocks(store)
        executor = _make_executor(state_store=store)

        dag = _make_simple_dag()

        async def mock_execute_node(node, *args, **kwargs):
            return (
                f"output-of-{node.id}",
                NodeExecutionStatus.COMPLETED.value,
                None,
            )

        executor._execute_node = mock_execute_node
        executor._record_node_event = AsyncMock()

        ctx = _make_context()
        await executor.execute(dag=dag, input="test", context=ctx)

        # Find the completion snapshot (last one with status=completed)
        all_snapshots = [
            c.args[0] for c in store.save_run_snapshot.call_args_list
        ]
        completed_snaps = [s for s in all_snapshots if s.status == "completed"]
        assert len(completed_snaps) >= 1
        assert completed_snaps[-1].completed_node_ids == ["n1", "n2", "n3"]

    @pytest.mark.asyncio
    async def test_execute_saves_failure_snapshot_on_error(self) -> None:
        """execute() should save a 'failed' snapshot when execution raises."""
        store = MagicMock()
        _setup_store_mocks(store)
        executor = _make_executor(state_store=store)

        dag = _make_simple_dag()

        call_count = [0]

        async def mock_execute_node(node, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return (
                    "output-n1",
                    NodeExecutionStatus.COMPLETED.value,
                    None,
                )
            # Second node fails
            return (
                None,
                NodeExecutionStatus.FAILED.value,
                RuntimeError("node failure"),
            )

        executor._execute_node = mock_execute_node
        executor._record_node_event = AsyncMock()

        ctx = _make_context()
        # Node failure should NOT raise — execute() returns failed status
        node_results, status, final_output, _ = await executor.execute(
            dag=dag, input="test", context=ctx
        )

        # Should have saved a failure snapshot via _maybe_save_snapshot
        all_snapshots = [
            c.args[0] for c in store.save_run_snapshot.call_args_list
        ]
        failed_snaps = [s for s in all_snapshots if s.status == "failed"]
        assert len(failed_snaps) >= 1

    @pytest.mark.asyncio
    async def test_no_snapshot_without_state_store(self) -> None:
        """Snapshot should not crash when no state store is configured."""
        executor = _make_executor(state_store=None)

        dag = _make_simple_dag()

        async def mock_execute_node(node, *args, **kwargs):
            return (f"output-{node.id}", "completed", None)

        executor._execute_node = mock_execute_node
        executor._record_node_event = AsyncMock()

        ctx = _make_context()
        # Should run without error even without state store
        await executor.execute(dag=dag, input="test", context=ctx)

    @pytest.mark.asyncio
    async def test_snapshot_disabled_via_config(self) -> None:
        """Snapshot should not be saved when config disabled."""
        from agent_app.config.schema import DagSnapshotConfig

        store = MagicMock()
        _setup_store_mocks(store)
        executor = _make_executor(
            state_store=store,
            snapshot_config=DagSnapshotConfig(enabled=False),
        )

        dag = _make_simple_dag()

        async def mock_execute_node(node, *args, **kwargs):
            return (f"output-{node.id}", "completed", None)

        executor._execute_node = mock_execute_node
        executor._record_node_event = AsyncMock()

        ctx = _make_context()
        await executor.execute(dag=dag, input="test", context=ctx)

        # No snapshots should be saved when disabled
        assert store.save_run_snapshot.call_count == 0


# ---------------------------------------------------------------------------
# Tests for resume with snapshot
# ---------------------------------------------------------------------------


class TestResumeSnapshotIntegration:
    """Tests for snapshot usage during DAG resume."""

    @pytest.mark.asyncio
    async def test_resume_reads_latest_snapshot(self) -> None:
        """resume() should read the latest snapshot from the state store."""
        store = MagicMock()
        _setup_store_mocks(store)
        store.list_nodes = AsyncMock(return_value=[])
        store.list_compensations = AsyncMock(return_value=[])

        run_state = MagicMock()
        run_state.status = "running"
        store.get_run.return_value = run_state

        snap = DagRunSnapshot(
            snapshot_id="snap-1",
            run_id="test-run-1",
            status="running",
            schema_version=1,
            completed_node_ids=["n1"],
            execution_context={"input": "test"},
        )
        store.get_latest_run_snapshot.return_value = snap

        resume_plan = MagicMock()
        resume_plan.resumable = True
        resume_plan.decisions = []
        store.build_resume_plan.return_value = resume_plan

        executor = _make_executor(state_store=store)
        executor._execute_node = AsyncMock(
            return_value=("output-n2", NodeExecutionStatus.COMPLETED.value, None)
        )

        dag = _make_simple_dag()
        ctx = _make_context()

        with patch.object(executor, "_make_renewer", return_value=None):
            with patch.object(executor, "_enforce_idempotency"):
                await executor.resume(dag=dag, input="test", context=ctx)

        # Verify get_latest_run_snapshot was called
        store.get_latest_run_snapshot.assert_called_once_with("test-run-1")

    @pytest.mark.asyncio
    async def test_resume_completed_snapshot_is_idempotent(self) -> None:
        """Resuming a 'completed' snapshot should return idempotent result."""
        store = MagicMock()
        _setup_store_mocks(store)
        store.list_nodes = AsyncMock(return_value=[])
        store.list_compensations = AsyncMock(return_value=[])

        run_state = MagicMock()
        run_state.status = "completed"
        store.get_run.return_value = run_state

        snap = DagRunSnapshot(
            snapshot_id="snap-completed",
            run_id="test-run-1",
            status="completed",
            schema_version=1,
        )
        store.get_latest_run_snapshot.return_value = snap

        executor = _make_executor(state_store=store)
        dag = _make_simple_dag()
        ctx = _make_context()

        with patch.object(executor, "_make_renewer", return_value=None):
            with patch.object(executor, "_enforce_idempotency"):
                result = await executor.resume(dag=dag, input="test", context=ctx)

        # Should return empty results with completed status (idempotent)
        node_results, status, final_output, compensation = result
        assert status == "completed"
        assert len(node_results) == 0

    @pytest.mark.asyncio
    async def test_resume_unsupported_schema_version_error(self) -> None:
        """Resuming a snapshot with unsupported schema_version should raise error."""
        store = MagicMock()
        _setup_store_mocks(store)
        store.list_nodes = AsyncMock(return_value=[])
        store.list_compensations = AsyncMock(return_value=[])

        run_state = MagicMock()
        run_state.status = "running"
        store.get_run.return_value = run_state

        snap = DagRunSnapshot(
            snapshot_id="snap-bad",
            run_id="test-run-1",
            status="running",
            schema_version=99,  # unsupported
        )
        store.get_latest_run_snapshot.return_value = snap

        executor = _make_executor(state_store=store)
        dag = _make_simple_dag()
        ctx = _make_context()

        with pytest.raises(Exception):
            await executor.resume(dag=dag, input="test", context=ctx)

    @pytest.mark.asyncio
    async def test_resume_snapshot_run_id_mismatch_error(self) -> None:
        """Resuming a snapshot with mismatched run_id should raise error."""
        store = MagicMock()
        _setup_store_mocks(store)
        store.list_nodes = AsyncMock(return_value=[])
        store.list_compensations = AsyncMock(return_value=[])

        run_state = MagicMock()
        run_state.status = "running"
        store.get_run.return_value = run_state

        snap = DagRunSnapshot(
            snapshot_id="snap-mismatch",
            run_id="wrong-run-id",
            status="running",
            schema_version=1,
        )
        store.get_latest_run_snapshot.return_value = snap

        executor = _make_executor(state_store=store)
        dag = _make_simple_dag()
        ctx = _make_context()

        with pytest.raises(Exception):
            await executor.resume(dag=dag, input="test", context=ctx)

    @pytest.mark.asyncio
    async def test_resume_no_snapshot_falls_through(self) -> None:
        """Resuming without a snapshot should fall through to existing logic."""
        store = MagicMock()
        _setup_store_mocks(store)
        store.list_nodes = AsyncMock(return_value=[])
        store.list_compensations = AsyncMock(return_value=[])
        store.get_latest_run_snapshot = AsyncMock(return_value=None)

        run_state = MagicMock()
        run_state.status = "running"
        store.get_run.return_value = run_state

        resume_plan = MagicMock()
        resume_plan.resumable = True
        resume_plan.decisions = []
        store.build_resume_plan.return_value = resume_plan

        executor = _make_executor(state_store=store)
        executor._execute_node = AsyncMock(
            return_value=("output-n1", NodeExecutionStatus.COMPLETED.value, None)
        )

        dag = _make_simple_dag()
        ctx = _make_context()

        with patch.object(executor, "_make_renewer", return_value=None):
            with patch.object(executor, "_enforce_idempotency"):
                await executor.resume(dag=dag, input="test", context=ctx)

        # Should have called build_resume_plan (existing logic)
        store.build_resume_plan.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for _is_snapshot_enabled
# ---------------------------------------------------------------------------


class TestIsSnapshotEnabled:
    """Tests for the _is_snapshot_enabled helper."""

    def test_no_state_store_returns_false(self) -> None:
        executor = _make_executor(state_store=None)
        assert executor._is_snapshot_enabled() is False

    def test_no_run_id_returns_false(self) -> None:
        executor = DagExecutor(
            agent_registry=MagicMock(),
            tool_registry=MagicMock(),
            workflow_registry=MagicMock(),
            state_store=MagicMock(),
            run_id=None,
        )
        assert executor._is_snapshot_enabled() is False

    def test_no_config_defaults_to_enabled(self) -> None:
        store = MagicMock()
        executor = _make_executor(state_store=store)
        assert executor._is_snapshot_enabled() is True

    def test_explicit_enabled_true(self) -> None:
        from agent_app.config.schema import DagSnapshotConfig

        store = MagicMock()
        executor = _make_executor(
            state_store=store,
            snapshot_config=DagSnapshotConfig(enabled=True),
        )
        assert executor._is_snapshot_enabled() is True

    def test_explicit_enabled_false(self) -> None:
        from agent_app.config.schema import DagSnapshotConfig

        store = MagicMock()
        executor = _make_executor(
            state_store=store,
            snapshot_config=DagSnapshotConfig(enabled=False),
        )
        assert executor._is_snapshot_enabled() is False


# ---------------------------------------------------------------------------
# Tests for _build_snapshot
# ---------------------------------------------------------------------------


class TestBuildSnapshot:
    """Tests for the _build_snapshot helper."""

    def test_build_snapshot_basic(self) -> None:
        executor = _make_executor()
        executor._workflow_name = "test-wf"

        snap = executor._build_snapshot(
            status="running",
            execution_context={"input": "hello"},
        )
        assert snap.status == "running"
        assert snap.run_id == "test-run-1"
        assert snap.workflow_name == "test-wf"
        assert snap.schema_version == 1
        assert snap.execution_context == {"input": "hello"}

    def test_build_snapshot_with_node_results(self) -> None:
        executor = _make_executor()

        node_results = {
            "n1": MagicMock(
                spec=["node_id", "status", "attempts", "output", "error",
                      "started_at", "completed_at"],
                node_id="n1",
                status=NodeExecutionStatus.COMPLETED,
                output="result1",
                attempts=[],
                error=None,
                started_at=None,
                completed_at=None,
            ),
        }

        snap = executor._build_snapshot(
            status="running",
            execution_context={},
            node_results=node_results,
            completed_node_ids=["n1"],
        )
        assert "n1" in snap.nodes
        assert snap.nodes["n1"].status == "completed"
        assert snap.completed_node_ids == ["n1"]


# ---------------------------------------------------------------------------
# Tests for snapshot write error handling
# ---------------------------------------------------------------------------


class TestSnapshotWriteErrorHandling:
    """Tests for snapshot write error handling."""

    @pytest.mark.asyncio
    async def test_snapshot_write_failure_raises_stable_error(self) -> None:
        """When _save_snapshot fails, it should raise SnapshotWriteError."""
        store = MagicMock()
        store.save_run_snapshot = AsyncMock(
            side_effect=SnapshotWriteError(run_id="run-1", message="disk full")
        )

        executor = _make_executor(state_store=store)

        with pytest.raises(SnapshotWriteError, match="disk full"):
            await executor._save_snapshot(
                DagRunSnapshot(
                    snapshot_id="snap-1",
                    run_id="run-1",
                    status="running",
                )
            )

    @pytest.mark.asyncio
    async def test_snapshot_generic_error_raises_stable_error(self) -> None:
        """Generic exceptions from save_run_snapshot should be wrapped."""
        store = MagicMock()
        store.save_run_snapshot = AsyncMock(
            side_effect=RuntimeError("database locked")
        )

        executor = _make_executor(state_store=store)

        with pytest.raises(SnapshotWriteError, match="Failed to save snapshot"):
            await executor._save_snapshot(
                DagRunSnapshot(
                    snapshot_id="snap-1",
                    run_id="run-1",
                    status="running",
                )
            )
