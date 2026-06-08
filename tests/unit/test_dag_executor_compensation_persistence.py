"""Tests for DagExecutor compensation state persistence (Phase 16.1)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.config.schema import DagCompensationConfig
from agent_app.runtime.compensation_state import (
    CompensationActionStatus,
    CompensationRunStatus,
    CompensationExecutionState,
    CompensationActionState,
)
from agent_app.runtime.compensation_store import (
    InMemoryCompensationStateStore,
    SQLiteCompensationStateStore,
    create_compensation_state_store,
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
    compensation_config: Any = None,
) -> DagExecutor:
    """Create a minimal DagExecutor for testing."""
    return DagExecutor(
        agent_registry=MagicMock(),
        tool_registry=MagicMock(),
        workflow_registry=MagicMock(),
        state_store=state_store,
        run_id="test-run-1",
        snapshot_config=snapshot_config,
        compensation_config=compensation_config,
    )


def _make_compensation_dag(name: str = "test-dag") -> DagWorkflow:
    """Create a DAG with compensation handlers on all nodes."""
    return DagWorkflow(
        name=name,
        nodes=[
            DagNode(
                id="n1", type=NodeType.AGENT, ref="agent-a",
                depends_on=[],
                compensate={"function": "compensate_n1"},
            ),
            DagNode(
                id="n2", type=NodeType.AGENT, ref="agent-b",
                depends_on=["n1"],
                compensate={"function": "compensate_n2"},
            ),
            DagNode(
                id="n3", type=NodeType.AGENT, ref="agent-c",
                depends_on=["n2"],
                compensate={"function": "compensate_n3"},
            ),
        ],
        compensation={
            "enabled": True,
            "trigger_on": ["workflow_failed"],
            "continue_on_failure": True,
        },
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


def _setup_state_store_mocks(store: MagicMock) -> None:
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
    store.list_nodes = AsyncMock(return_value=[])
    store.list_compensations = AsyncMock(return_value=[])


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


# ---------------------------------------------------------------------------
# Tests for compensation store initialization
# ---------------------------------------------------------------------------

class TestCompensationStoreInit:
    def test_no_config_creates_memory_store(self) -> None:
        executor = _make_executor()
        executor._init_compensation_store()
        assert executor._compensation_store is not None
        assert isinstance(executor._compensation_store, InMemoryCompensationStateStore)

    def test_enabled_true_creates_store(self) -> None:
        executor = _make_executor(
            compensation_config=DagCompensationConfig(enabled=True),
        )
        executor._init_compensation_store()
        assert executor._compensation_store is not None

    def test_enabled_false_returns_none(self) -> None:
        executor = _make_executor(
            compensation_config=DagCompensationConfig(enabled=False),
        )
        executor._init_compensation_store()
        assert executor._compensation_store is None

    def test_sqlite_store_creation(self, tmp_path: Any) -> None:
        db = str(tmp_path / "comp.db")
        executor = _make_executor(
            compensation_config=DagCompensationConfig(
                enabled=True, store="sqlite", path=db,
            ),
        )
        executor._init_compensation_store()
        assert executor._compensation_store is not None

    def test_is_compensation_persistence_enabled_no_store(self) -> None:
        executor = _make_executor()
        assert executor._is_compensation_persistence_enabled() is True  # default enabled

    def test_is_compensation_persistence_enabled_disabled(self) -> None:
        executor = _make_executor(
            compensation_config=DagCompensationConfig(enabled=False),
        )
        assert executor._is_compensation_persistence_enabled() is False

    def test_get_max_attempts_default(self) -> None:
        executor = _make_executor()
        assert executor._get_max_compensation_attempts() == 1

    def test_get_max_attempts_custom(self) -> None:
        executor = _make_executor(
            compensation_config=DagCompensationConfig(max_attempts=3),
        )
        assert executor._get_max_compensation_attempts() == 3

    def test_resume_incomplete_default(self) -> None:
        executor = _make_executor()
        assert executor._is_resume_incomplete_compensation() is True

    def test_resume_incomplete_false(self) -> None:
        executor = _make_executor(
            compensation_config=DagCompensationConfig(resume_incomplete=False),
        )
        assert executor._is_resume_incomplete_compensation() is False


# ---------------------------------------------------------------------------
# Tests for compensation state creation
# ---------------------------------------------------------------------------

class TestCreateCompensationState:
    def test_create_state_basic(self) -> None:
        executor = _make_executor()
        executor._workflow_name = "test-wf"
        dag = _make_compensation_dag()
        candidates = ["n1", "n2", "n3"]

        state = executor._create_compensation_state(
            dag=dag, candidates=candidates, original_failure_type="test_failure",
        )

        assert state.run_id == "test-run-1"
        assert state.workflow_name == "test-wf"
        assert state.status == CompensationRunStatus.PENDING.value
        assert len(state.actions) == 3
        assert len(state.action_order) == 3

    def test_create_state_skips_nodes_without_compensate(self) -> None:
        dag = DagWorkflow(
            name="test-dag",
            nodes=[
                DagNode(id="n1", type=NodeType.AGENT, ref="agent-a", depends_on=[]),
                DagNode(
                    id="n2", type=NodeType.AGENT, ref="agent-b",
                    depends_on=["n1"], compensate={"function": "comp_n2"},
                ),
            ],
            compensation={"enabled": True},
        )
        executor = _make_executor()
        executor._workflow_name = "test-wf"
        candidates = ["n1", "n2"]

        state = executor._create_compensation_state(
            dag=dag, candidates=candidates, original_failure_type="test_failure",
        )

        # n1 has no compensate, so only 1 action
        assert len(state.actions) == 1
        action = next(iter(state.actions.values()))
        assert action.compensating_for_node_id == "n2"

    def test_create_state_uses_max_attempts_from_config(self) -> None:
        executor = _make_executor(
            compensation_config=DagCompensationConfig(max_attempts=3),
        )
        executor._workflow_name = "test-wf"
        dag = _make_compensation_dag()
        candidates = ["n1"]

        state = executor._create_compensation_state(
            dag=dag, candidates=candidates, original_failure_type="test_failure",
        )

        action = next(iter(state.actions.values()))
        assert action.max_attempts == 3


# ---------------------------------------------------------------------------
# Tests for compensation during execute()
# ---------------------------------------------------------------------------

class TestCompensationDuringExecute:
    @pytest.mark.asyncio
    async def test_compensation_state_created_on_failure(self) -> None:
        """When DAG fails and compensation is triggered, state should be created."""
        store = MagicMock()
        _setup_state_store_mocks(store)
        store.get_run.return_value = MagicMock(status="failed")

        # Create a compensation store
        comp_store = InMemoryCompensationStateStore()

        executor = _make_executor(
            state_store=store,
            compensation_config=DagCompensationConfig(enabled=True),
        )
        # Inject the compensation store directly
        executor._compensation_store = comp_store

        dag = _make_compensation_dag()

        async def mock_execute_node(node, *args, **kwargs):
            return (f"output-{node.id}", "completed", None)

        async def mock_execute_compensation_handler(*args, **kwargs):
            return ("comp-output", "completed", None)

        executor._execute_node = mock_execute_node
        executor._execute_compensation_handler = mock_execute_compensation_handler
        executor._record_node_event = AsyncMock()
        executor._record_workflow_compensation_started = MagicMock()
        executor._record_node_compensation_started = MagicMock()
        executor._record_node_compensation_completed = MagicMock()
        executor._make_renewer = MagicMock(return_value=None)
        executor._enforce_idempotency = AsyncMock()
        executor._acquire_lease = AsyncMock()
        executor._release_lease = AsyncMock()

        ctx = _make_context()

        # Manually trigger compensation state creation
        candidates = executor._get_compensation_candidates(
            {n.id: MagicMock(status=NodeExecutionStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
             for n in dag.nodes},
            dag,
        )
        comp_state = executor._create_compensation_state(
            dag=dag, candidates=candidates, original_failure_type="test_failure",
        )
        await comp_store.save_compensation_state(comp_state)

        # Verify compensation state was created
        comp_state = await comp_store.get_compensation_state("test-run-1")
        assert comp_state is not None
        assert comp_state.workflow_name == "test-dag"
        assert len(comp_state.actions) > 0

    @pytest.mark.asyncio
    async def test_compensation_action_updated_to_completed(self) -> None:
        """Compensation actions should be updated to completed after execution."""
        store = MagicMock()
        _setup_state_store_mocks(store)

        comp_store = InMemoryCompensationStateStore()
        executor = _make_executor(
            state_store=store,
            compensation_config=DagCompensationConfig(enabled=True),
        )
        executor._compensation_store = comp_store

        dag = _make_compensation_dag()

        async def mock_execute_node(node, *args, **kwargs):
            return (f"output-{node.id}", "completed", None)

        async def mock_comp_handler(*args, **kwargs):
            return ("comp-result", "completed", None)

        executor._execute_node = mock_execute_node
        executor._execute_compensation_handler = mock_comp_handler
        executor._record_node_event = AsyncMock()
        executor._record_workflow_compensation_started = MagicMock()
        executor._record_node_compensation_started = MagicMock()
        executor._record_node_compensation_completed = MagicMock()
        executor._make_renewer = MagicMock(return_value=None)
        executor._enforce_idempotency = AsyncMock()
        executor._acquire_lease = AsyncMock()
        executor._release_lease = AsyncMock()

        ctx = _make_context()

        # Manually create and persist compensation state
        candidates = ["n1", "n2", "n3"]
        comp_state = executor._create_compensation_state(
            dag=dag, candidates=candidates, original_failure_type="test_failure",
        )
        comp_state.mark_running()
        await comp_store.save_compensation_state(comp_state)

        # Simulate executing compensation handlers and updating actions
        for action in comp_state.get_pending_actions():
            action.mark_running()
            action.mark_completed("comp-result")
            await comp_store.update_compensation_action("test-run-1", action)

        # Check that actions are completed
        comp_state = await comp_store.get_compensation_state("test-run-1")
        assert comp_state is not None
        completed_actions = comp_state.get_completed_actions()
        assert len(completed_actions) == 3  # All 3 actions completed

    @pytest.mark.asyncio
    async def test_no_compensation_state_when_not_triggered(self) -> None:
        """No compensation state when DAG succeeds."""
        store = MagicMock()
        _setup_state_store_mocks(store)

        comp_store = InMemoryCompensationStateStore()
        executor = _make_executor(
            state_store=store,
            compensation_config=DagCompensationConfig(enabled=True),
        )
        executor._compensation_store = comp_store

        dag = DagWorkflow(
            name="test-dag",
            nodes=[
                DagNode(id="n1", type=NodeType.AGENT, ref="agent-a", depends_on=[]),
                DagNode(id="n2", type=NodeType.AGENT, ref="agent-b", depends_on=["n1"]),
            ],
            compensation={
                "enabled": True,
                "trigger_on": ["workflow_failed"],
            },
        )

        async def mock_execute_node(node, *args, **kwargs):
            return (f"output-{node.id}", "completed", None)

        executor._execute_node = mock_execute_node
        executor._record_node_event = AsyncMock()
        executor._make_renewer = MagicMock(return_value=None)
        executor._enforce_idempotency = AsyncMock()
        executor._acquire_lease = AsyncMock()
        executor._release_lease = AsyncMock()

        ctx = _make_context()
        await executor.execute(dag=dag, input="test", context=ctx)

        # No compensation state should be created
        comp_state = await comp_store.get_compensation_state("test-run-1")
        assert comp_state is None

    @pytest.mark.asyncio
    async def test_compensation_persistence_disabled(self) -> None:
        """No compensation state when persistence is disabled."""
        store = MagicMock()
        _setup_state_store_mocks(store)

        comp_store = InMemoryCompensationStateStore()
        executor = _make_executor(
            state_store=store,
            compensation_config=DagCompensationConfig(enabled=False),
        )
        executor._compensation_store = None  # disabled

        dag = _make_compensation_dag()

        async def mock_execute_node(node, *args, **kwargs):
            return (f"output-{node.id}", "completed", None)

        executor._execute_node = mock_execute_node
        executor._record_node_event = AsyncMock()
        executor._make_renewer = MagicMock(return_value=None)
        executor._enforce_idempotency = AsyncMock()
        executor._acquire_lease = AsyncMock()
        executor._release_lease = AsyncMock()

        ctx = _make_context()
        await executor.execute(dag=dag, input="test", context=ctx)

        # comp_store should remain empty (not used)
        assert len(comp_store._states) == 0


# ---------------------------------------------------------------------------
# Tests for _resume_compensation
# ---------------------------------------------------------------------------

class TestResumeCompensation:
    @pytest.mark.asyncio
    async def test_resume_skips_completed_actions(self) -> None:
        """Resume should skip already-completed actions."""
        comp_store = InMemoryCompensationStateStore()

        # Pre-populate: action_1 completed, action_2 pending
        state = CompensationExecutionState(run_id="test-run-1", workflow_name="test-dag")
        a1 = CompensationActionState(
            action_id="a1",
            run_id="test-run-1",
            node_id="n1",
            status=CompensationActionStatus.COMPLETED.value,
            output="comp-output-n1",
        )
        a1.completed_at = datetime.now(timezone.utc)
        a2 = CompensationActionState(
            action_id="a2",
            run_id="test-run-1",
            node_id="n2",
            status=CompensationActionStatus.PENDING.value,
        )
        state.add_action(a1)
        state.add_action(a2)
        state.mark_running()
        await comp_store.save_compensation_state(state)

        executor = _make_executor()
        executor._compensation_store = comp_store
        executor._compensation_config = DagCompensationConfig(
            enabled=True, resume_incomplete=True,
        )

        dag = DagWorkflow(
            name="test-dag",
            nodes=[
                DagNode(
                    id="n2", type=NodeType.AGENT, ref="agent-b",
                    compensate={"function": "comp_n2"},
                ),
            ],
            compensation={"enabled": True},
        )

        handler_calls = []

        async def mock_comp_handler(node, *args, **kwargs):
            handler_calls.append(node.id)
            return ("comp-n2", "completed", None)

        executor._execute_compensation_handler = mock_comp_handler
        executor._save_compensation_state = AsyncMock()

        ctx = _make_context()
        result = await executor._resume_compensation(
            dag=dag,
            input="test",
            context=ctx,
            permissions=[],
            execution_context={},
            existing_state=state,
            original_failure_type="test_failure",
        )

        # Only n2 handler should be called (n1 was already completed)
        assert "n2" in handler_calls
        assert "n1" not in handler_calls
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_resume_retries_failed_actions(self) -> None:
        """Resume should retry failed actions within max_attempts."""
        comp_store = InMemoryCompensationStateStore()

        state = CompensationExecutionState(run_id="test-run-1", workflow_name="test-dag")
        a1 = CompensationActionState(
            action_id="a1",
            run_id="test-run-1",
            node_id="n1",
            status=CompensationActionStatus.FAILED.value,
            attempts=1,
            max_attempts=3,
            error={"type": "timeout", "message": "timed out"},
        )
        state.add_action(a1)
        state.mark_running()
        await comp_store.save_compensation_state(state)

        executor = _make_executor()
        executor._compensation_store = comp_store
        executor._compensation_config = DagCompensationConfig(
            enabled=True, resume_incomplete=True,
        )

        dag = DagWorkflow(
            name="test-dag",
            nodes=[
                DagNode(
                    id="n1", type=NodeType.AGENT, ref="agent-a",
                    compensate={"function": "comp_n1"},
                ),
            ],
            compensation={"enabled": True},
        )

        async def mock_comp_handler(node, *args, **kwargs):
            return ("comp-n1-retry", "completed", None)

        executor._execute_compensation_handler = mock_comp_handler
        executor._save_compensation_state = AsyncMock()

        ctx = _make_context()
        result = await executor._resume_compensation(
            dag=dag,
            input="test",
            context=ctx,
            permissions=[],
            execution_context={},
            existing_state=state,
            original_failure_type="timeout",
        )

        assert result.status == "completed"
        assert "n1" in result.compensated_nodes

    @pytest.mark.asyncio
    async def test_resume_does_not_retry_exhausted(self) -> None:
        """Resume should not retry actions that have exhausted max_attempts."""
        comp_store = InMemoryCompensationStateStore()

        state = CompensationExecutionState(run_id="test-run-1", workflow_name="test-dag")
        a1 = CompensationActionState(
            action_id="a1",
            run_id="test-run-1",
            node_id="n1",
            status=CompensationActionStatus.FAILED.value,
            attempts=3,
            max_attempts=3,
            error={"type": "timeout", "message": "timed out"},
        )
        state.add_action(a1)
        state.mark_running()
        await comp_store.save_compensation_state(state)

        executor = _make_executor()
        executor._compensation_store = comp_store
        executor._compensation_config = DagCompensationConfig(
            enabled=True, resume_incomplete=True,
        )

        dag = DagWorkflow(
            name="test-dag",
            nodes=[
                DagNode(
                    id="n1", type=NodeType.AGENT, ref="agent-a",
                    compensate={"function": "comp_n1"},
                ),
            ],
            compensation={"enabled": True},
        )

        handler_calls = []

        async def mock_comp_handler(node, *args, **kwargs):
            handler_calls.append(node.id)
            return ("comp-n1", "completed", None)

        executor._execute_compensation_handler = mock_comp_handler
        executor._save_compensation_state = AsyncMock()

        ctx = _make_context()
        result = await executor._resume_compensation(
            dag=dag,
            input="test",
            context=ctx,
            permissions=[],
            execution_context={},
            existing_state=state,
            original_failure_type="timeout",
        )

        # Handler should NOT be called (action exhausted)
        assert len(handler_calls) == 0
        assert result.status == "failed"
        assert "n1" in result.failed_nodes


# ---------------------------------------------------------------------------
# Tests for _save_compensation_state error handling
# ---------------------------------------------------------------------------

class TestSaveCompensationStateError:
    @pytest.mark.asyncio
    async def test_save_raises_stable_error(self) -> None:
        """Failed compensation state save should raise SnapshotWriteError."""
        from agent_app.runtime.dag_snapshot import SnapshotWriteError

        bad_store = MagicMock()
        bad_store.save_compensation_state = AsyncMock(
            side_effect=RuntimeError("database full")
        )

        executor = _make_executor(
            compensation_config=DagCompensationConfig(enabled=True),
        )
        executor._compensation_store = bad_store

        state = _make_state()
        _add_action(state)

        with pytest.raises(SnapshotWriteError, match="Failed to save compensation state"):
            await executor._save_compensation_state(state)

    @pytest.mark.asyncio
    async def test_save_skipped_when_disabled(self) -> None:
        """No save when compensation persistence is disabled."""
        store = MagicMock()
        store.save_compensation_state = AsyncMock()

        executor = _make_executor(
            compensation_config=DagCompensationConfig(enabled=False),
        )
        executor._compensation_store = None

        state = _make_state()
        await executor._save_compensation_state(state)

        # Should not call save
        store.save_compensation_state.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for compensation with config plumbing
# ---------------------------------------------------------------------------

class TestCompensationConfigPlumbing:
    def test_default_config_works(self) -> None:
        executor = _make_executor()
        executor._init_compensation_store()
        assert executor._compensation_store is not None
        assert executor._is_compensation_persistence_enabled() is True

    def test_explicit_enabled_true(self) -> None:
        executor = _make_executor(
            compensation_config=DagCompensationConfig(enabled=True),
        )
        executor._init_compensation_store()
        assert executor._compensation_store is not None

    def test_explicit_enabled_false(self) -> None:
        executor = _make_executor(
            compensation_config=DagCompensationConfig(enabled=False),
        )
        executor._init_compensation_store()
        assert executor._compensation_store is None

    def test_sqlite_config(self, tmp_path: Any) -> None:
        db = str(tmp_path / "comp.db")
        executor = _make_executor(
            compensation_config=DagCompensationConfig(
                enabled=True, store="sqlite", path=db,
            ),
        )
        executor._init_compensation_store()
        assert executor._compensation_store is not None

    def test_invalid_store_raises(self) -> None:
        with pytest.raises(Exception):  # ValidationError from Pydantic
            _make_executor(
                compensation_config=DagCompensationConfig(
                    enabled=True, store="redis",
                ),
            )

    def test_invalid_max_attempts_raises(self) -> None:
        with pytest.raises(Exception):  # ValidationError
            DagCompensationConfig(max_attempts=0)

    def test_resume_incomplete_config(self) -> None:
        executor = _make_executor(
            compensation_config=DagCompensationConfig(
                resume_incomplete=False,
            ),
        )
        assert executor._is_resume_incomplete_compensation() is False
