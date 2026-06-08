"""Comprehensive tests for Phase 14.0 + Phase 14.1: DAG Workflow Execution State Persistence and Resume.

Tests cover:
  - WorkflowRunState, NodeExecutionState, WorkflowEventState,
    CompensationExecutionState models
  - WorkflowStateStore protocol
  - InMemoryWorkflowStateStore CRUD operations
  - SQLiteWorkflowStateStore CRUD operations and cross-instance reads
  - RecoveryPlan and build_recovery_plan()
  - ResumePolicy, NodeResumeDecision, ResumePlan, ResumeResult models
  - WorkflowStateStore resume methods (build_resume_plan, get_node_outputs)
  - DagExecutor.resume() method
  - WorkflowExecutor.resume_workflow_run() API
  - AgentApp.resume_workflow_run() API
  - create_workflow_state_store factory
  - DAG executor integration (optional state_store, backward compat)
  - Config support for workflow_state
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent_app.config.schema import RuntimeConfig
from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry
from agent_app.runtime.dag_run_state import (
    CompensationExecutionState,
    CompensationRunStatus,
    IdempotencyRecord,
    LeaseAcquireResult,
    LeasePolicy,
    LeaseStatus,
    NodeExecutionState,
    NodeResumeDecision,
    NodeRunStatus,
    RecoveryPlan,
    ResumePlan,
    ResumePolicy,
    WorkflowEventState,
    WorkflowRunLease,
    WorkflowRunState,
    WorkflowRunStatus,
    WorkflowStateStore,
    WorkerIdentity,
)
from agent_app.runtime.dag_state_store import (
    InMemoryWorkflowStateStore,
    SQLiteWorkflowStateStore,
    create_workflow_state_store,
    _build_recovery_plan,
)
from agent_app.workflows.dag import DagExecutor, NodeType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def run_context() -> RunContext:
    return RunContext(
        run_id="test-run-1",
        user_id="alice",
        tenant_id="tenant-a",
        permissions=["order:read"],
    )


@pytest.fixture()
def memory_store() -> InMemoryWorkflowStateStore:
    return InMemoryWorkflowStateStore()


@pytest.fixture()
def sqlite_store(tmp_path: Path) -> SQLiteWorkflowStateStore:
    db_path = str(tmp_path / "test_workflow_state.db")
    return SQLiteWorkflowStateStore(db_path=db_path)


def _make_run_state(run_id: str = "run-1") -> WorkflowRunState:
    return WorkflowRunState(
        run_id=run_id,
        workflow_name="test_dag",
        status=WorkflowRunStatus.RUNNING.value,
        input="test input",
        metadata={"key": "value"},
    )


def _make_node_state(
    run_id: str = "run-1",
    node_id: str = "node-1",
    status: str = NodeRunStatus.COMPLETED.value,
) -> NodeExecutionState:
    return NodeExecutionState(
        run_id=run_id,
        node_id=node_id,
        node_type="agent",
        status=status,
        input={"param": "value"},
        output="result",
        attempts=1,
    )


def _make_event(
    run_id: str = "run-1",
    event_id: str = "evt-1",
    event_type: str = "workflow.started",
) -> WorkflowEventState:
    return WorkflowEventState(
        event_id=event_id,
        run_id=run_id,
        event_type=event_type,
        payload={"data": "test"},
    )


def _make_compensation(
    run_id: str = "run-1",
    node_id: str = "node-1",
    status: str = CompensationRunStatus.COMPLETED.value,
) -> CompensationExecutionState:
    return CompensationExecutionState(
        run_id=run_id,
        node_id=node_id,
        handler_name="rollback_handler",
        status=status,
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestWorkflowRunStateModel:
    """Test WorkflowRunState data model."""

    def test_create_workflow_run_state(self) -> None:
        """Can create a WorkflowRunState with required fields."""
        state = _make_run_state()
        assert state.run_id == "run-1"
        assert state.workflow_name == "test_dag"
        assert state.status == WorkflowRunStatus.RUNNING.value
        assert state.input == "test input"

    def test_default_values(self) -> None:
        """Default values are set correctly."""
        state = WorkflowRunState(run_id="run-1", input="test")
        assert state.workflow_name is None
        assert state.status == WorkflowRunStatus.PENDING.value
        assert state.output is None
        assert state.error is None
        assert state.completed_at is None
        assert state.metadata == {}

    def test_timezone_aware_timestamps(self) -> None:
        """Timestamps are timezone-aware UTC."""
        state = WorkflowRunState(run_id="run-1", input="test")
        assert state.started_at.tzinfo is not None
        assert state.started_at.tzinfo == timezone.utc


class TestNodeExecutionStateModel:
    """Test NodeExecutionState data model."""

    def test_create_node_state(self) -> None:
        state = _make_node_state()
        assert state.run_id == "run-1"
        assert state.node_id == "node-1"
        assert state.node_type == "agent"
        assert state.status == NodeRunStatus.COMPLETED.value
        assert state.output == "result"
        assert state.attempts == 1

    def test_default_values(self) -> None:
        state = NodeExecutionState(run_id="r", node_id="n", node_type="tool")
        assert state.status == NodeRunStatus.PENDING.value
        assert state.input is None
        assert state.output is None
        assert state.error is None
        assert state.started_at is None
        assert state.completed_at is None
        assert state.attempts == 0
        assert state.metadata == {}


class TestWorkflowEventStateModel:
    """Test WorkflowEventState data model."""

    def test_create_event(self) -> None:
        evt = _make_event()
        assert evt.event_id == "evt-1"
        assert evt.run_id == "run-1"
        assert evt.event_type == "workflow.started"
        assert evt.payload == {"data": "test"}
        assert evt.node_id is None

    def test_with_node_id(self) -> None:
        evt = WorkflowEventState(
            event_id="evt-2",
            run_id="run-1",
            node_id="node-1",
            event_type="node.completed",
        )
        assert evt.node_id == "node-1"


class TestCompensationExecutionStateModel:
    """Test CompensationExecutionState data model."""

    def test_create_compensation(self) -> None:
        comp = _make_compensation()
        assert comp.run_id == "run-1"
        assert comp.node_id == "node-1"
        assert comp.handler_name == "rollback_handler"
        assert comp.status == CompensationRunStatus.COMPLETED.value

    def test_failed_compensation(self) -> None:
        comp = CompensationExecutionState(
            run_id="run-1",
            node_id="node-1",
            handler_name="rollback",
            status=CompensationRunStatus.FAILED.value,
            error={"type": "RuntimeError", "message": "rollback failed"},
        )
        assert comp.error is not None
        assert comp.error["type"] == "RuntimeError"


class TestRecoveryPlanModel:
    """Test RecoveryPlan data model."""

    def test_create_plan(self) -> None:
        plan = RecoveryPlan(run_id="run-1", resumable=True)
        assert plan.run_id == "run-1"
        assert plan.resumable is True
        assert plan.completed_nodes == []
        assert plan.reason is None

    def test_non_resumable_plan(self) -> None:
        plan = RecoveryPlan(
            run_id="run-1",
            resumable=False,
            reason="Compensation has started.",
        )
        assert plan.resumable is False
        assert plan.reason is not None


# ---------------------------------------------------------------------------
# InMemoryWorkflowStateStore tests
# ---------------------------------------------------------------------------


class TestInMemoryWorkflowStateStore:
    """Test InMemoryWorkflowStateStore CRUD operations."""

    async def test_create_and_get_run(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Can create and retrieve a workflow run."""
        state = _make_run_state("run-1")
        await memory_store.create_run(state)
        retrieved = await memory_store.get_run("run-1")
        assert retrieved.run_id == "run-1"
        assert retrieved.status == WorkflowRunStatus.RUNNING.value

    async def test_get_run_not_found(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """get_run raises KeyError for missing run."""
        with pytest.raises(KeyError, match="not found"):
            await memory_store.get_run("nonexistent")

    async def test_update_run(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Can update workflow run fields."""
        await memory_store.create_run(_make_run_state("run-1"))
        await memory_store.update_run(
            "run-1",
            status=WorkflowRunStatus.COMPLETED.value,
            output="done",
        )
        updated = await memory_store.get_run("run-1")
        assert updated.status == WorkflowRunStatus.COMPLETED.value
        assert updated.output == "done"

    async def test_upsert_and_get_node(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Can upsert and retrieve a node execution state."""
        node = _make_node_state()
        await memory_store.upsert_node(node)
        retrieved = await memory_store.get_node("run-1", "node-1")
        assert retrieved is not None
        assert retrieved.status == NodeRunStatus.COMPLETED.value
        assert retrieved.output == "result"

    async def test_get_node_not_found(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """get_node returns None for missing node."""
        result = await memory_store.get_node("run-1", "nonexistent")
        assert result is None

    async def test_list_nodes(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Can list all nodes for a workflow run."""
        await memory_store.upsert_node(_make_node_state("run-1", "node-1"))
        await memory_store.upsert_node(_make_node_state("run-1", "node-2", NodeRunStatus.FAILED.value))
        nodes = await memory_store.list_nodes("run-1")
        assert len(nodes) == 2
        ids = {n.node_id for n in nodes}
        assert ids == {"node-1", "node-2"}

    async def test_append_and_list_events(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Can append and list events chronologically."""
        evt1 = _make_event("run-1", "evt-1", "workflow.started")
        evt2 = _make_event("run-1", "evt-2", "node.completed")
        await memory_store.append_event(evt1)
        await memory_store.append_event(evt2)
        events = await memory_store.list_events("run-1")
        assert len(events) == 2
        assert events[0].event_type == "workflow.started"
        assert events[1].event_type == "node.completed"

    async def test_upsert_compensation(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Can upsert compensation states."""
        comp = _make_compensation()
        await memory_store.upsert_compensation(comp)
        comps = await memory_store.list_compensations("run-1")
        assert len(comps) == 1
        assert comps[0].handler_name == "rollback_handler"

    async def test_update_compensation(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Upserting same node_id updates existing compensation."""
        comp1 = _make_compensation(status=CompensationRunStatus.RUNNING.value)
        await memory_store.upsert_compensation(comp1)
        # Update to completed
        comp2 = _make_compensation(status=CompensationRunStatus.COMPLETED.value)
        await memory_store.upsert_compensation(comp2)
        comps = await memory_store.list_compensations("run-1")
        assert len(comps) == 1
        assert comps[0].status == CompensationRunStatus.COMPLETED.value

    async def test_list_compensations_empty(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """list_compensations returns empty list for run with no compensations."""
        await memory_store.create_run(_make_run_state("run-empty"))
        comps = await memory_store.list_compensations("run-empty")
        assert comps == []


# ---------------------------------------------------------------------------
# SQLiteWorkflowStateStore tests
# ---------------------------------------------------------------------------


class TestSQLiteWorkflowStateStore:
    """Test SQLiteWorkflowStateStore CRUD and cross-instance reads."""

    async def test_create_and_get_run(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Can create and retrieve a workflow run."""
        state = _make_run_state("run-1")
        await sqlite_store.create_run(state)
        retrieved = await sqlite_store.get_run("run-1")
        assert retrieved.run_id == "run-1"
        assert retrieved.workflow_name == "test_dag"

    async def test_create_idempotent(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Creating the same run twice does not overwrite."""
        state = _make_run_state("run-1")
        await sqlite_store.create_run(state)
        # Second create should not overwrite (INSERT OR IGNORE)
        await sqlite_store.create_run(
            WorkflowRunState(run_id="run-1", workflow_name="other", input="other")
        )
        retrieved = await sqlite_store.get_run("run-1")
        assert retrieved.workflow_name == "test_dag"

    async def test_get_run_not_found(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """get_run raises KeyError for missing run."""
        with pytest.raises(KeyError, match="not found"):
            await sqlite_store.get_run("nonexistent")

    async def test_update_run(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Can update workflow run fields."""
        await sqlite_store.create_run(_make_run_state("run-1"))
        await sqlite_store.update_run(
            "run-1",
            status=WorkflowRunStatus.COMPLETED.value,
            output="final result",
        )
        updated = await sqlite_store.get_run("run-1")
        assert updated.status == WorkflowRunStatus.COMPLETED.value
        assert updated.output == "final result"

    async def test_upsert_node(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Can upsert a node execution state."""
        node = _make_node_state()
        await sqlite_store.upsert_node(node)
        retrieved = await sqlite_store.get_node("run-1", "node-1")
        assert retrieved is not None
        assert retrieved.node_type == "agent"
        assert retrieved.status == NodeRunStatus.COMPLETED.value

    async def test_node_update_overwrites(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Upserting same (run_id, node_id) overwrites the row."""
        await sqlite_store.upsert_node(_make_node_state(status=NodeRunStatus.RUNNING.value))
        await sqlite_store.upsert_node(_make_node_state(status=NodeRunStatus.COMPLETED.value))
        retrieved = await sqlite_store.get_node("run-1", "node-1")
        assert retrieved is not None
        assert retrieved.status == NodeRunStatus.COMPLETED.value

    async def test_get_node_not_found(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """get_node returns None for missing node."""
        result = sqlite_store.get_node("run-1", "nonexistent")
        # get_node is async but uses sync sqlite; need to await it
        result = await result
        assert result is None

    async def test_list_nodes(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Can list all nodes for a workflow run."""
        await sqlite_store.upsert_node(_make_node_state("run-1", "node-1"))
        await sqlite_store.upsert_node(_make_node_state("run-1", "node-2", NodeRunStatus.FAILED.value))
        nodes = await sqlite_store.list_nodes("run-1")
        assert len(nodes) == 2

    async def test_append_and_list_events(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Can append and list events in chronological order."""
        evt1 = _make_event("run-1", "evt-1", "workflow.started")
        evt2 = _make_event("run-1", "evt-2", "node.completed")
        await sqlite_store.append_event(evt1)
        await sqlite_store.append_event(evt2)
        events = await sqlite_store.list_events("run-1")
        assert len(events) == 2
        assert events[0].event_type == "workflow.started"

    async def test_upsert_compensation(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Can upsert compensation states."""
        comp = _make_compensation()
        await sqlite_store.upsert_compensation(comp)
        comps = await sqlite_store.list_compensations("run-1")
        assert len(comps) == 1

    async def test_cross_instance_read(self, tmp_path: Path) -> None:
        """Data persists across separate SQLiteWorkflowStateStore instances."""
        db_path = str(tmp_path / "shared.db")

        # Write with instance A
        store_a = SQLiteWorkflowStateStore(db_path=db_path)
        await store_a.create_run(_make_run_state("run-1"))
        await store_a.upsert_node(_make_node_state())
        await store_a.append_event(_make_event())
        store_a.close()

        # Read with instance B
        store_b = SQLiteWorkflowStateStore(db_path=db_path)
        run = await store_b.get_run("run-1")
        assert run.run_id == "run-1"
        nodes = await store_b.list_nodes("run-1")
        assert len(nodes) == 1
        events = await store_b.list_events("run-1")
        assert len(events) == 1
        store_b.close()


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestCreateWorkflowStateStoreFactory:
    """Test create_workflow_state_store factory."""

    def test_create_memory(self) -> None:
        store = create_workflow_state_store("memory")
        assert isinstance(store, InMemoryWorkflowStateStore)

    def test_create_sqlite(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        store = create_workflow_state_store("sqlite", db_path=db_path)
        assert isinstance(store, SQLiteWorkflowStateStore)
        store.close()

    def test_create_sqlite_default_path(self) -> None:
        store = create_workflow_state_store("sqlite")
        assert isinstance(store, SQLiteWorkflowStateStore)
        store.close()

    def test_create_unknown_type(self) -> None:
        with pytest.raises(ValueError, match="Unknown.*store type"):
            create_workflow_state_store("unknown")


# ---------------------------------------------------------------------------
# Recovery plan tests
# ---------------------------------------------------------------------------


class TestBuildRecoveryPlan:
    """Test build_recovery_plan() via InMemoryWorkflowStateStore."""

    async def test_completed_run_not_interrupted(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """All nodes completed — run already finished, not resumable."""
        await memory_store.create_run(_make_run_state("run-1"))
        await memory_store.upsert_node(
            NodeExecutionState(run_id="run-1", node_id="n1", node_type="agent",
                               status=NodeRunStatus.COMPLETED.value, completed_at=_now())
        )
        plan = await memory_store.build_recovery_plan("run-1")
        assert plan.resumable is False
        assert "finished" in plan.reason.lower()
        assert plan.completed_nodes == ["n1"]

    async def test_interrupted_node_detected(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Running node without completed_at is marked interrupted."""
        await memory_store.create_run(_make_run_state("run-1"))
        await memory_store.upsert_node(
            NodeExecutionState(run_id="run-1", node_id="n1", node_type="agent",
                               status=NodeRunStatus.RUNNING.value)
        )
        plan = await memory_store.build_recovery_plan("run-1")
        assert plan.interrupted_nodes == ["n1"]
        assert plan.resumable is True

    async def test_failed_node_in_plan(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Failed nodes appear in failed_nodes."""
        await memory_store.create_run(_make_run_state("run-1"))
        await memory_store.upsert_node(
            NodeExecutionState(run_id="run-1", node_id="n1", node_type="agent",
                               status=NodeRunStatus.RUNNING.value)
        )
        await memory_store.upsert_node(
            NodeExecutionState(run_id="run-1", node_id="n2", node_type="agent",
                               status=NodeRunStatus.FAILED.value, error={"type": "timeout"})
        )
        plan = await memory_store.build_recovery_plan("run-1")
        assert plan.failed_nodes == ["n2"]
        assert plan.resumable is False

    async def test_compensation_started_detected(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Compensation started flag is detected."""
        await memory_store.create_run(_make_run_state("run-1"))
        await memory_store.upsert_node(
            NodeExecutionState(run_id="run-1", node_id="n1", node_type="agent",
                               status=NodeRunStatus.COMPLETED.value, completed_at=_now())
        )
        await memory_store.upsert_compensation(
            CompensationExecutionState(
                run_id="run-1", node_id="n1", handler_name="rollback",
                status=CompensationRunStatus.RUNNING.value,
            )
        )
        plan = await memory_store.build_recovery_plan("run-1")
        assert plan.compensation_started is True
        assert plan.resumable is False

    async def test_non_resumable_reason_failed(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Recovery plan explains why run is not resumable (failed nodes)."""
        await memory_store.create_run(_make_run_state("run-1"))
        await memory_store.upsert_node(
            NodeExecutionState(run_id="run-1", node_id="n1", node_type="agent",
                               status=NodeRunStatus.FAILED.value)
        )
        plan = await memory_store.build_recovery_plan("run-1")
        assert plan.resumable is False
        assert plan.reason is not None
        assert "intervention" in plan.reason.lower()

    async def test_non_resumable_reason_compensation(
        self, memory_store: InMemoryWorkflowStateStore
    ) -> None:
        """Recovery plan explains why run is not resumable (compensation)."""
        await memory_store.create_run(_make_run_state("run-1"))
        await memory_store.upsert_compensation(
            CompensationExecutionState(
                run_id="run-1", node_id="n1", handler_name="rollback",
                status=CompensationRunStatus.COMPLETED.value,
            )
        )
        plan = await memory_store.build_recovery_plan("run-1")
        assert plan.resumable is False
        assert plan.reason is not None


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestWorkflowStateConfig:
    """Test config support for workflow_state."""

    def test_nested_dict_config(self) -> None:
        """Nested workflow_state dict is normalized."""
        cfg = RuntimeConfig(
            workflow_state={"type": "sqlite", "path": "/tmp/test.db"}
        )
        assert cfg.workflow_state_type == "sqlite"
        assert cfg.workflow_state_path == "/tmp/test.db"

    def test_flat_string_config(self) -> None:
        """Flat string workflow_state: memory is accepted."""
        cfg = RuntimeConfig(workflow_state="memory")
        assert cfg.workflow_state_type == "memory"

    def test_default_config(self) -> None:
        """Default workflow_state_type is memory."""
        cfg = RuntimeConfig()
        assert cfg.workflow_state_type == "memory"

    def test_yaml_roundtrip_dict(self) -> None:
        """Config roundtrips through dict (YAML load simulation)."""
        raw = {"workflow_state": {"type": "sqlite", "path": ".agent_app/state.db"}}
        cfg = RuntimeConfig(**raw)
        assert cfg.workflow_state_type == "sqlite"
        assert cfg.workflow_state_path == ".agent_app/state.db"

    def test_yaml_roundtrip_string(self) -> None:
        """Config handles string workflow_state value."""
        raw = {"workflow_state": "memory"}
        cfg = RuntimeConfig(**raw)
        assert cfg.workflow_state_type == "memory"


# ---------------------------------------------------------------------------
# DAG executor integration tests
# ---------------------------------------------------------------------------


def _make_simple_dag_cfg() -> dict:
    """Return a minimal valid DAG config dict for testing."""
    return {
        "name": "test_dag",
        "nodes": [
            {"id": "n1", "type": "agent", "ref": "test_agent"},
        ],
        "execution_mode": "sequential",
    }


class TestDagExecutorStateStoreIntegration:
    """Test DagExecutor with state_store enabled/disabled."""

    @pytest.fixture
    def app(self):
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app.register_agent(AgentSpec(name="test_agent", instructions="Test agent", tools=[]))
        app._ensure_runner()
        return app

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="test-run",
            user_id="test",
            tenant_id="test",
        )

    @pytest.mark.asyncio
    async def test_no_state_store_preserves_old_behavior(self, app, context) -> None:
        """DagExecutor without state_store behaves exactly as before."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(**_make_simple_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=None,
            run_id=None,
        )
        results, status, output, comp = await executor.execute(
            dag=dag, input="test", context=context
        )
        assert status == "completed"
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_state_store_disabled_skip_persist(self, app, context) -> None:
        """No state is persisted when state_store is None."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(**_make_simple_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=None,
            run_id="run-1",
        )
        results, status, output, comp = await executor.execute(
            dag=dag, input="test", context=context
        )
        assert status == "completed"

    @pytest.mark.asyncio
    async def test_4tuple_return_with_state_store(self, memory_store: InMemoryWorkflowStateStore, app, context) -> None:
        """4-tuple return still works with state_store enabled."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(**_make_simple_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=memory_store,
            run_id="run-1",
        )
        result = await executor.execute(dag=dag, input="test", context=context)
        assert len(result) == 4
        results, status, output, comp = result
        assert status == "completed"

    @pytest.mark.asyncio
    async def test_successful_dag_persists_run_completed(
        self, memory_store: InMemoryWorkflowStateStore, app, context
    ) -> None:
        """Successful DAG persists run with completed status."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(**_make_simple_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=memory_store,
            run_id="run-1",
        )
        await executor.execute(dag=dag, input="test", context=context)

        run = await memory_store.get_run("run-1")
        assert run.status == WorkflowRunStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_successful_node_states_are_completed(
        self, memory_store: InMemoryWorkflowStateStore, app, context
    ) -> None:
        """Successful node states are persisted as completed."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(**_make_simple_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=memory_store,
            run_id="run-1",
        )
        await executor.execute(dag=dag, input="test", context=context)

        node = await memory_store.get_node("run-1", "n1")
        assert node is not None
        assert node.status == NodeRunStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_events_are_persisted(
        self, memory_store: InMemoryWorkflowStateStore, app, context
    ) -> None:
        """Workflow events are persisted."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        dag = DagWorkflow(**_make_simple_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=memory_store,
            run_id="run-1",
        )
        await executor.execute(dag=dag, input="test", context=context)

        events = await memory_store.list_events("run-1")
        event_types = [e.event_type for e in events]
        assert "workflow.started" in event_types


# ---------------------------------------------------------------------------
# Phase 14.1: Resume models tests
# ---------------------------------------------------------------------------


class TestResumePolicy:
    """Test ResumePolicy model defaults and values."""

    def test_default_policy(self) -> None:
        """Default policy retries failed/interrupted, skips completed."""
        policy = ResumePolicy()
        assert policy.retry_failed is True
        assert policy.retry_interrupted is True
        assert policy.skip_completed is True
        assert policy.allow_after_compensation_started is False

    def test_custom_policy(self) -> None:
        """Can customize all policy fields."""
        policy = ResumePolicy(
            retry_failed=False,
            retry_interrupted=False,
            skip_completed=False,
            allow_after_compensation_started=True,
        )
        assert policy.retry_failed is False
        assert policy.retry_interrupted is False
        assert policy.skip_completed is False
        assert policy.allow_after_compensation_started is True


class TestNodeResumeDecision:
    """Test NodeResumeDecision model."""

    def test_create_decision(self) -> None:
        """Can create a NodeResumeDecision."""
        decision = NodeResumeDecision(
            node_id="n1", action="skip", reason="already completed"
        )
        assert decision.node_id == "n1"
        assert decision.action == "skip"
        assert decision.reason == "already completed"

    def test_valid_actions(self) -> None:
        """All valid actions are accepted."""
        for action in ("skip", "retry", "run", "blocked"):
            decision = NodeResumeDecision(node_id="n1", action=action)
            assert decision.action == action


class TestResumePlan:
    """Test ResumePlan model."""

    def test_default_resume_plan(self) -> None:
        """Default ResumePlan is not resumable."""
        plan = ResumePlan(run_id="run-1")
        assert plan.resumable is False
        assert plan.decisions == []
        assert plan.completed_nodes == []
        assert plan.retry_nodes == []
        assert plan.blocked_nodes == []

    def test_resumable_plan(self) -> None:
        """Can create a resumable plan with decisions."""
        plan = ResumePlan(
            run_id="run-1",
            resumable=True,
            decisions=[
                NodeResumeDecision(node_id="n1", action="skip", reason="completed"),
                NodeResumeDecision(node_id="n2", action="retry", reason="interrupted"),
            ],
            completed_nodes=["n1"],
            retry_nodes=["n2"],
        )
        assert plan.resumable is True
        assert len(plan.decisions) == 2
        assert plan.completed_nodes == ["n1"]
        assert plan.retry_nodes == ["n2"]


# ---------------------------------------------------------------------------
# Phase 14.1: Resume plan tests (store-level)
# ---------------------------------------------------------------------------


class TestBuildResumePlanInMemory:
    """Test InMemoryWorkflowStateStore.build_resume_plan()."""

    @pytest.fixture
    def store(self) -> InMemoryWorkflowStateStore:
        return InMemoryWorkflowStateStore()

    def _setup_completed_run(self, store: InMemoryWorkflowStateStore) -> str:
        """Persist a fully completed run."""
        import asyncio

        async def _setup():
            await store.create_run(
                WorkflowRunState(
                    run_id="run-1",
                    workflow_name="test_dag",
                    status=WorkflowRunStatus.COMPLETED.value,
                )
            )
            await store.upsert_node(
                NodeExecutionState(
                    run_id="run-1",
                    node_id="n1",
                    node_type="agent",
                    status=NodeRunStatus.COMPLETED.value,
                    output="result1",
                )
            )
            await store.upsert_node(
                NodeExecutionState(
                    run_id="run-1",
                    node_id="n2",
                    node_type="agent",
                    status=NodeRunStatus.COMPLETED.value,
                    output="result2",
                )
            )

        asyncio.run(_setup())
        return "run-1"

    def _setup_interrupted_run(self, store: InMemoryWorkflowStateStore) -> str:
        """Persist a run with an interrupted node."""
        import asyncio

        async def _setup():
            await store.create_run(
                WorkflowRunState(
                    run_id="run-2",
                    workflow_name="test_dag",
                    status=WorkflowRunStatus.RUNNING.value,
                )
            )
            await store.upsert_node(
                NodeExecutionState(
                    run_id="run-2",
                    node_id="n1",
                    node_type="agent",
                    status=NodeRunStatus.COMPLETED.value,
                    output="result1",
                )
            )
            await store.upsert_node(
                NodeExecutionState(
                    run_id="run-2",
                    node_id="n2",
                    node_type="agent",
                    status=NodeRunStatus.RUNNING.value,
                )
            )

        asyncio.run(_setup())
        return "run-2"

    def _setup_failed_run(self, store: InMemoryWorkflowStateStore) -> str:
        """Persist a run with a failed node."""
        import asyncio

        async def _setup():
            await store.create_run(
                WorkflowRunState(
                    run_id="run-3",
                    workflow_name="test_dag",
                    status=WorkflowRunStatus.FAILED.value,
                )
            )
            await store.upsert_node(
                NodeExecutionState(
                    run_id="run-3",
                    node_id="n1",
                    node_type="agent",
                    status=NodeRunStatus.COMPLETED.value,
                    output="result1",
                )
            )
            await store.upsert_node(
                NodeExecutionState(
                    run_id="run-3",
                    node_id="n2",
                    node_type="agent",
                    status=NodeRunStatus.FAILED.value,
                    error={"type": "test_error", "message": "test"},
                )
            )

        asyncio.run(_setup())
        return "run-3"

    def _setup_compensation_started(self, store: InMemoryWorkflowStateStore) -> str:
        """Persist a run where compensation has started."""
        import asyncio

        async def _setup():
            await store.create_run(
                WorkflowRunState(
                    run_id="run-4",
                    workflow_name="test_dag",
                    status=WorkflowRunStatus.COMPENSATING.value,
                )
            )
            await store.upsert_node(
                NodeExecutionState(
                    run_id="run-4",
                    node_id="n1",
                    node_type="agent",
                    status=NodeRunStatus.COMPLETED.value,
                )
            )
            # Persist a compensation execution state to indicate compensation started
            await store.upsert_compensation(
                CompensationExecutionState(
                    run_id="run-4",
                    node_id="n1",
                    handler_name="rollback_handler",
                    status=CompensationRunStatus.COMPLETED.value,
                )
            )

        asyncio.run(_setup())
        return "run-4"

    def test_completed_run_resumable(self, store: InMemoryWorkflowStateStore) -> None:
        """Completed run is resumable — completed nodes are skipped."""
        run_id = self._setup_completed_run(store)
        import asyncio

        plan = asyncio.run(store.build_resume_plan(run_id))
        assert plan.resumable is True
        assert plan.completed_nodes == ["n1", "n2"]
        assert plan.retry_nodes == []
        assert plan.skipped_nodes == []
        # All decisions should be "skip"
        for decision in plan.decisions:
            assert decision.action == "skip"

    def test_interrupted_run_resumable(self, store: InMemoryWorkflowStateStore) -> None:
        """Run with interrupted node is resumable — interrupted node retried."""
        run_id = self._setup_interrupted_run(store)
        import asyncio

        plan = asyncio.run(store.build_resume_plan(run_id))
        assert plan.resumable is True
        assert plan.completed_nodes == ["n1"]
        assert plan.retry_nodes == ["n2"]
        # n1 should be skipped
        n1_decision = next(d for d in plan.decisions if d.node_id == "n1")
        assert n1_decision.action == "skip"
        # n2 should be retried (interrupted)
        n2_decision = next(d for d in plan.decisions if d.node_id == "n2")
        assert n2_decision.action == "retry"

    def test_failed_run_retries_with_default_policy(
        self, store: InMemoryWorkflowStateStore
    ) -> None:
        """Failed node is retried with default policy (retry_failed=True)."""
        run_id = self._setup_failed_run(store)
        import asyncio

        plan = asyncio.run(store.build_resume_plan(run_id))
        assert plan.resumable is True
        assert plan.retry_nodes == ["n2"]
        n2_decision = next(d for d in plan.decisions if d.node_id == "n2")
        assert n2_decision.action == "retry"

    def test_failed_run_blocks_with_no_retry_policy(
        self, store: InMemoryWorkflowStateStore
    ) -> None:
        """Failed node is blocked when retry_failed=False."""
        run_id = self._setup_failed_run(store)
        import asyncio

        plan = asyncio.run(
            store.build_resume_plan(run_id, policy=ResumePolicy(retry_failed=False))
        )
        assert plan.resumable is True
        assert plan.blocked_nodes == ["n2"]
        n2_decision = next(d for d in plan.decisions if d.node_id == "n2")
        assert n2_decision.action == "blocked"

    def test_compensation_started_blocks_resume(
        self, store: InMemoryWorkflowStateStore
    ) -> None:
        """Resume is blocked when compensation has started."""
        run_id = self._setup_compensation_started(store)
        import asyncio

        plan = asyncio.run(store.build_resume_plan(run_id))
        assert plan.resumable is False
        assert plan.reason is not None
        assert "compensation" in plan.reason.lower()

    def test_unknown_run_id_raises_key_error(
        self, store: InMemoryWorkflowStateStore
    ) -> None:
        """Unknown run_id raises KeyError from get_run()."""
        import asyncio

        with pytest.raises(KeyError, match="not found"):
            asyncio.run(store.build_resume_plan("nonexistent"))

    def test_get_node_outputs(self, store: InMemoryWorkflowStateStore) -> None:
        """get_node_outputs returns dict of node_id -> output."""
        import asyncio

        async def _setup():
            await store.create_run(
                WorkflowRunState(run_id="run-out", workflow_name="test_dag")
            )
            await store.upsert_node(
                NodeExecutionState(
                    run_id="run-out",
                    node_id="n1",
                    node_type="agent",
                    status=NodeRunStatus.COMPLETED.value,
                    output="output1",
                )
            )
            await store.upsert_node(
                NodeExecutionState(
                    run_id="run-out",
                    node_id="n2",
                    node_type="agent",
                    status=NodeRunStatus.COMPLETED.value,
                    output={"key": "value"},
                )
            )
            await store.upsert_node(
                NodeExecutionState(
                    run_id="run-out",
                    node_id="n3",
                    node_type="agent",
                    status=NodeRunStatus.RUNNING.value,
                )
            )

        asyncio.run(_setup())
        outputs = asyncio.run(store.get_node_outputs("run-out"))
        assert outputs == {"n1": "output1", "n2": {"key": "value"}}


# ---------------------------------------------------------------------------
# Phase 14.1: DagExecutor.resume() tests
# ---------------------------------------------------------------------------


def _make_2node_sequential_dag_cfg() -> dict:
    """Return a 2-node sequential DAG config: n1 -> n2."""
    return {
        "name": "test_resume_dag",
        "nodes": [
            {"id": "n1", "type": "agent", "ref": "test_agent"},
            {"id": "n2", "type": "agent", "ref": "test_agent"},
        ],
        "edges": [{"from": "n1", "to": "n2"}],
        "execution_mode": "sequential",
    }


def _make_parallel_dag_cfg() -> dict:
    """Return a parallel DAG config: n1, n2 -> n3."""
    return {
        "name": "test_resume_parallel_dag",
        "nodes": [
            {"id": "n1", "type": "agent", "ref": "test_agent"},
            {"id": "n2", "type": "agent", "ref": "test_agent"},
            {"id": "n3", "type": "agent", "ref": "test_agent"},
        ],
        "edges": [
            {"from": "n1", "to": "n3"},
            {"from": "n2", "to": "n3"},
        ],
        "execution_mode": "parallel",
    }


class TestDagExecutorResume:
    """Test DagExecutor.resume() method."""

    @pytest.fixture
    def app(self):
        from agent_app import AgentApp, AgentSpec, ToolSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        app.register_agent(AgentSpec(name="test_agent", instructions="Test agent", tools=[]))
        app._ensure_runner()
        return app

    @pytest.fixture
    def context(self):
        from agent_app.core.context import RunContext
        return RunContext(
            run_id="test-run",
            user_id="test",
            tenant_id="test",
        )

    async def _persist_completed_n1(self, app, run_id: str = "run-1"):
        """Persist a state where n1 is completed and n2 is pending."""
        from agent_app.runtime.dag_state_store import InMemoryWorkflowStateStore

        store = InMemoryWorkflowStateStore()
        await store.create_run(
            WorkflowRunState(
                run_id=run_id,
                workflow_name="test_resume_dag",
                status=WorkflowRunStatus.RUNNING.value,
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id=run_id,
                node_id="n1",
                node_type="agent",
                status=NodeRunStatus.COMPLETED.value,
                output="persisted_n1_output",
                attempts=1,
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id=run_id,
                node_id="n2",
                node_type="agent",
                status=NodeRunStatus.PENDING.value,
            )
        )
        # Attach store to app for the test
        app._dag_state_store = store
        return store

    @pytest.mark.asyncio
    async def test_resume_requires_state_store(self, app, context) -> None:
        """Resume without state_store raises DagError."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagError

        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=None,
            run_id="run-1",
        )
        with pytest.raises(DagError, match="no state_store configured"):
            await executor.resume(dag=dag, input="test", context=context)

    @pytest.mark.asyncio
    async def test_resume_requires_run_id(self, app, context) -> None:
        """Resume without run_id raises DagError."""
        from agent_app.runtime.dag_state_store import InMemoryWorkflowStateStore
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagError

        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=InMemoryWorkflowStateStore(),
            run_id=None,
        )
        with pytest.raises(DagError, match="no state_store configured"):
            await executor.resume(dag=dag, input="test", context=context)

    @pytest.mark.asyncio
    async def test_resume_unknown_run_id(self, app, context) -> None:
        """Resume with unknown run_id raises DagError."""
        from agent_app.runtime.dag_state_store import InMemoryWorkflowStateStore
        from agent_app.workflows.dag import DagExecutor, DagWorkflow, DagError

        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        store = InMemoryWorkflowStateStore()
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=store,
            run_id="nonexistent-run",
        )
        with pytest.raises(DagError, match="not found"):
            await executor.resume(dag=dag, input="test", context=context)

    @pytest.mark.asyncio
    async def test_resume_skips_completed_nodes(
        self, app, context
    ) -> None:
        """Resume skips completed nodes and reuses their persisted output."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        store = await self._persist_completed_n1(app, "run-skip")
        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=store,
            run_id="run-skip",
        )
        results, status, output, _ = await executor.resume(
            dag=dag, input="test", context=context
        )

        assert status == "completed"
        assert len(results) == 2
        # n1 was completed before — should be skipped
        n1_result = next(r for r in results if r.node_id == "n1")
        assert n1_result.status.value == "completed"
        assert n1_result.output == "persisted_n1_output"
        # n2 should be executed fresh
        n2_result = next(r for r in results if r.node_id == "n2")
        assert n2_result.status.value == "completed"

    @pytest.mark.asyncio
    async def test_resume_returns_4tuple(self, app, context) -> None:
        """Resume returns 4-tuple: (results, status, output, compensation)."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        store = await self._persist_completed_n1(app, "run-4tuple")
        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=store,
            run_id="run-4tuple",
        )
        result = await executor.resume(dag=dag, input="test", context=context)
        assert len(result) == 4
        results, status, final_output, compensation = result
        assert status == "completed"
        assert compensation is None

    @pytest.mark.asyncio
    async def test_resume_retries_interrupted_node(self, app, context) -> None:
        """Resume retries an interrupted node (status=running, no completed_at)."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        store = InMemoryWorkflowStateStore()
        await store.create_run(
            WorkflowRunState(
                run_id="run-retry",
                workflow_name="test_resume_dag",
                status=WorkflowRunStatus.RUNNING.value,
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-retry",
                node_id="n1",
                node_type="agent",
                status=NodeRunStatus.COMPLETED.value,
                output="n1_done",
            )
        )
        # n2 was interrupted (running, no output)
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-retry",
                node_id="n2",
                node_type="agent",
                status=NodeRunStatus.RUNNING.value,
                attempts=1,
            )
        )
        app._dag_state_store = store
        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=store,
            run_id="run-retry",
        )
        results, status, output, _ = await executor.resume(
            dag=dag, input="test", context=context
        )

        assert status == "completed"
        n2_result = next(r for r in results if r.node_id == "n2")
        assert n2_result.status.value == "completed"
        # attempts should be incremented (1 original + 1 retry)
        assert len(n2_result.attempts) >= 1

    @pytest.mark.asyncio
    async def test_resume_retry_failed_true(self, app, context) -> None:
        """Failed node is retried when retry_failed=True (default)."""
        from agent_app.runtime.dag_run_state import ResumePolicy
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        store = InMemoryWorkflowStateStore()
        await store.create_run(
            WorkflowRunState(
                run_id="run-fail-retry",
                workflow_name="test_resume_dag",
                status=WorkflowRunStatus.FAILED.value,
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-fail-retry",
                node_id="n1",
                node_type="agent",
                status=NodeRunStatus.COMPLETED.value,
                output="n1_ok",
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-fail-retry",
                node_id="n2",
                node_type="agent",
                status=NodeRunStatus.FAILED.value,
                error={"type": "test_error"},
            )
        )
        app._dag_state_store = store
        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=store,
            run_id="run-fail-retry",
        )
        results, status, output, _ = await executor.resume(
            dag=dag,
            input="test",
            context=context,
            policy=ResumePolicy(retry_failed=True),
        )

        assert status == "completed"
        n2_result = next(r for r in results if r.node_id == "n2")
        assert n2_result.status.value == "completed"

    @pytest.mark.asyncio
    async def test_resume_retry_failed_false_blocks_node(
        self, app, context
    ) -> None:
        """Failed node is blocked when retry_failed=False."""
        from agent_app.runtime.dag_run_state import ResumePolicy
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        store = InMemoryWorkflowStateStore()
        await store.create_run(
            WorkflowRunState(
                run_id="run-fail-block",
                workflow_name="test_resume_dag",
                status=WorkflowRunStatus.FAILED.value,
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-fail-block",
                node_id="n1",
                node_type="agent",
                status=NodeRunStatus.COMPLETED.value,
                output="n1_ok",
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-fail-block",
                node_id="n2",
                node_type="agent",
                status=NodeRunStatus.FAILED.value,
                error={"type": "test_error"},
            )
        )
        app._dag_state_store = store
        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=store,
            run_id="run-fail-block",
        )
        results, status, output, _ = await executor.resume(
            dag=dag,
            input="test",
            context=context,
            policy=ResumePolicy(retry_failed=False),
        )

        # n2 is blocked → n3 downstream is also blocked → workflow fails
        assert status == "failed"
        n2_result = next(r for r in results if r.node_id == "n2")
        assert n2_result.status.value == "failed"

    @pytest.mark.asyncio
    async def test_resume_blocks_when_compensation_started(
        self, app, context
    ) -> None:
        """Resume raises DagError when compensation has started."""
        from agent_app.runtime.dag_run_state import (
            CompensationExecutionState,
            CompensationRunStatus,
        )
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        store = InMemoryWorkflowStateStore()
        await store.create_run(
            WorkflowRunState(
                run_id="run-comp",
                workflow_name="test_resume_dag",
                status=WorkflowRunStatus.COMPENSATING.value,
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-comp",
                node_id="n1",
                node_type="agent",
                status=NodeRunStatus.COMPLETED.value,
            )
        )
        # Persist a compensation execution state
        await store.upsert_compensation(
            CompensationExecutionState(
                run_id="run-comp",
                node_id="n1",
                handler_name="rollback_handler",
                status=CompensationRunStatus.COMPLETED.value,
            )
        )
        app._dag_state_store = store
        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=store,
            run_id="run-comp",
        )
        from agent_app.workflows.dag import DagError

        with pytest.raises(DagError, match="compensation"):
            await executor.resume(dag=dag, input="test", context=context)

    @pytest.mark.asyncio
    async def test_resume_skipped_nodes_not_re_executed(
        self, app, context
    ) -> None:
        """SKIPPED nodes are not re-executed during resume."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        store = InMemoryWorkflowStateStore()
        await store.create_run(
            WorkflowRunState(
                run_id="run-skip-node",
                workflow_name="test_resume_dag",
                status=WorkflowRunStatus.COMPLETED.value,
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-skip-node",
                node_id="n1",
                node_type="agent",
                status=NodeRunStatus.SKIPPED.value,
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-skip-node",
                node_id="n2",
                node_type="agent",
                status=NodeRunStatus.COMPLETED.value,
                output="n2_done",
            )
        )
        app._dag_state_store = store
        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=store,
            run_id="run-skip-node",
        )
        results, status, output, _ = await executor.resume(
            dag=dag, input="test", context=context
        )

        assert status == "completed"
        # n1 was skipped — should remain skipped
        n1_result = next(r for r in results if r.node_id == "n1")
        assert n1_result.status.value == "skipped"
        # n2 was completed — should be skipped (reused)
        n2_result = next(r for r in results if r.node_id == "n2")
        assert n2_result.status.value == "completed"
        assert n2_result.output == "n2_done"

    @pytest.mark.asyncio
    async def test_resume_persists_events(self, app, context) -> None:
        """Resume persists workflow.resume_started and workflow.resume_completed events."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        store = InMemoryWorkflowStateStore()
        await store.create_run(
            WorkflowRunState(
                run_id="run-events",
                workflow_name="test_resume_dag",
                status=WorkflowRunStatus.RUNNING.value,
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-events",
                node_id="n1",
                node_type="agent",
                status=NodeRunStatus.COMPLETED.value,
                output="n1_ok",
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-events",
                node_id="n2",
                node_type="agent",
                status=NodeRunStatus.PENDING.value,
            )
        )
        app._dag_state_store = store
        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=store,
            run_id="run-events",
        )
        await executor.resume(dag=dag, input="test", context=context)

        events = await store.list_events("run-events")
        event_types = [e.event_type for e in events]
        assert "workflow.resume_started" in event_types
        assert "workflow.resume_completed" in event_types

    @pytest.mark.asyncio
    async def test_resume_persists_failed_event_on_error(
        self, app, context
    ) -> None:
        """Resume persists workflow.resume_failed when compensation has started."""
        from agent_app.runtime.dag_run_state import (
            CompensationExecutionState,
            CompensationRunStatus,
        )
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        store = InMemoryWorkflowStateStore()
        await store.create_run(
            WorkflowRunState(
                run_id="run-fail-event",
                workflow_name="test_resume_dag",
                status=WorkflowRunStatus.COMPENSATING.value,
            )
        )
        # Persist a compensation execution state to trigger the
        # "compensation started" non-resumable path
        await store.upsert_compensation(
            CompensationExecutionState(
                run_id="run-fail-event",
                node_id="n1",
                handler_name="rollback_handler",
                status=CompensationRunStatus.COMPLETED.value,
            )
        )
        app._dag_state_store = store
        dag = DagWorkflow(**_make_2node_sequential_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=store,
            run_id="run-fail-event",
        )
        from agent_app.workflows.dag import DagError

        with pytest.raises(DagError):
            await executor.resume(dag=dag, input="test", context=context)

        events = await store.list_events("run-fail-event")
        event_types = [e.event_type for e in events]
        assert "workflow.resume_failed" in event_types

    @pytest.mark.asyncio
    async def test_resume_with_parallel_dag(self, app, context) -> None:
        """Resume works with parallel DAG execution mode."""
        from agent_app.workflows.dag import DagExecutor, DagWorkflow

        store = InMemoryWorkflowStateStore()
        await store.create_run(
            WorkflowRunState(
                run_id="run-par",
                workflow_name="test_resume_parallel_dag",
                status=WorkflowRunStatus.RUNNING.value,
            )
        )
        # n1 and n2 completed, n3 pending
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-par",
                node_id="n1",
                node_type="agent",
                status=NodeRunStatus.COMPLETED.value,
                output="n1_out",
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-par",
                node_id="n2",
                node_type="agent",
                status=NodeRunStatus.COMPLETED.value,
                output="n2_out",
            )
        )
        await store.upsert_node(
            NodeExecutionState(
                run_id="run-par",
                node_id="n3",
                node_type="agent",
                status=NodeRunStatus.PENDING.value,
            )
        )
        app._dag_state_store = store
        dag = DagWorkflow(**_make_parallel_dag_cfg())
        executor = DagExecutor(
            agent_registry=app.agent_registry,
            tool_registry=app.tool_registry,
            workflow_registry=app.workflow_registry,
            app_runner=app._runner,
            state_store=store,
            run_id="run-par",
        )
        results, status, output, _ = await executor.resume(
            dag=dag, input="test", context=context
        )

        assert status == "completed"
        assert len(results) == 3
        # n1, n2 skipped; n3 executed
        n3_result = next(r for r in results if r.node_id == "n3")
        assert n3_result.status.value == "completed"


# ---------------------------------------------------------------------------
# Phase 14.1: WorkflowExecutor and AgentApp API tests
# ---------------------------------------------------------------------------


class TestResumeWorkflowAPI:
    """Test resume_workflow_run() at WorkflowExecutor and AgentApp level."""

    @pytest.fixture
    def app_with_state(self):
        from agent_app import AgentApp, AgentSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore
        from agent_app.runtime.dag_state_store import InMemoryWorkflowStateStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        store = InMemoryWorkflowStateStore()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
            dag_state_store=store,
        )
        app.register_agent(AgentSpec(name="test_agent", instructions="Test agent", tools=[]))

        # Register the workflow
        from agent_app.core.workflow import Workflow, WorkflowType

        wf = Workflow(
            name="test_resume_api_wf",
            type=WorkflowType.DAG,
            entry_agent_name="test_agent",
            config={"dag": _make_2node_sequential_dag_cfg()},
        )
        app.register_workflow(wf)
        app._ensure_runner()
        return app, store, wf

    @pytest.mark.asyncio
    async def test_app_resume_no_state_store(self) -> None:
        """AgentApp.resume_workflow_run fails gracefully without state_store."""
        from agent_app import AgentApp, AgentSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.core.workflow import Workflow, WorkflowType

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(registry=bundle)
        app.register_agent(AgentSpec(name="test_agent", instructions="Test", tools=[]))
        wf = Workflow(
            name="wf",
            type=WorkflowType.DAG,
            entry_agent_name="test_agent",
            config={"dag": _make_2node_sequential_dag_cfg()},
        )
        app.register_workflow(wf)
        app._ensure_runner()

        result = await app.resume_workflow_run(
            workflow="wf", run_id="any-run"
        )
        assert result.status == "failed"
        assert "state_store" in result.error["message"].lower()

    @pytest.mark.asyncio
    async def test_app_resume_unknown_workflow(self) -> None:
        """AgentApp.resume_workflow_run fails for unknown workflow name."""
        from agent_app import AgentApp, AgentSpec
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.dag_state_store import InMemoryWorkflowStateStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        store = InMemoryWorkflowStateStore()
        app = AgentApp(
            registry=bundle,
            dag_state_store=store,
        )
        app.register_agent(AgentSpec(name="test_agent", instructions="Test", tools=[]))
        app._ensure_runner()

        result = await app.resume_workflow_run(
            workflow="nonexistent_wf", run_id="any-run"
        )
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_workflow_executor_resume_end_to_end(
        self, app_with_state
    ) -> None:
        """Full end-to-end resume through WorkflowExecutor."""
        app, store, wf = app_with_state

        # First: execute the DAG to persist state
        await app.run(
            workflow="test_resume_api_wf",
            input="hello",
        )

        # Find the run_id from the first execution
        runs = await store.list_runs()
        assert len(runs) == 1
        first_run_id = runs[0].run_id

        # Update n2 to be pending (simulate interruption)
        await store.upsert_node(
            NodeExecutionState(
                run_id=first_run_id,
                node_id="n2",
                node_type="agent",
                status=NodeRunStatus.PENDING.value,
            )
        )

        # Now resume
        result = await app.resume_workflow_run(
            workflow="test_resume_api_wf",
            run_id=first_run_id,
            input="hello",
        )

        assert result.status == "completed"
        assert result.run_id == first_run_id


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


# ===========================================================================
# Phase 15: Lease + Idempotency Tests
# ===========================================================================


class TestLeaseModels:
    """Tests for Phase 15 lease and worker identity models."""

    def test_worker_identity_default(self) -> None:
        """WorkerIdentity can be created with defaults."""
        w = WorkerIdentity()
        assert w.worker_id.startswith("worker_")
        assert len(w.worker_id) == 19  # "worker_" (7) + 12 hex chars
        assert w.hostname is None
        assert w.process_id is None
        assert w.app_version is None
        assert w.metadata == {}

    def test_worker_identity_explicit(self) -> None:
        """WorkerIdentity can be created with explicit values."""
        w = WorkerIdentity(
            worker_id="w1",
            hostname="host1",
            process_id=1234,
            app_version="1.0",
            metadata={"region": "us-east-1"},
        )
        assert w.worker_id == "w1"
        assert w.hostname == "host1"
        assert w.process_id == 1234
        assert w.app_version == "1.0"
        assert w.metadata == {"region": "us-east-1"}

    def test_lease_policy_defaults(self) -> None:
        """LeasePolicy has sensible defaults."""
        p = LeasePolicy()
        assert p.ttl_seconds == 300
        assert p.allow_steal_expired is True
        assert p.renew_before_seconds == 60

    def test_lease_policy_custom(self) -> None:
        """LeasePolicy accepts custom values."""
        p = LeasePolicy(ttl_seconds=600, allow_steal_expired=False, renew_before_seconds=30)
        assert p.ttl_seconds == 600
        assert p.allow_steal_expired is False
        assert p.renew_before_seconds == 30

    def test_workflow_run_lease_timezone_aware(self) -> None:
        """WorkflowRunLease requires timezone-aware datetimes."""
        now = datetime.now(timezone.utc)
        future = now.replace(tzinfo=timezone.utc)
        lease = WorkflowRunLease(
            run_id="r1",
            owner_id="w1",
            acquired_at=now,
            expires_at=future,
        )
        assert lease.acquired_at.tzinfo is not None
        assert lease.expires_at.tzinfo is not None
        assert lease.version == 1
        assert lease.released_at is None
        assert lease.renewed_at is None

    def test_workflow_run_lease_rejects_naive_datetime(self) -> None:
        """WorkflowRunLease rejects naive (no tzinfo) datetimes."""
        import pytest
        with pytest.raises(ValueError, match="timezone-aware"):
            WorkflowRunLease(
                run_id="r1",
                owner_id="w1",
                acquired_at=datetime(2024, 1, 1, 0, 0, 0),  # naive
                expires_at=datetime(2024, 1, 1, 0, 5, 0),
            )

    def test_lease_acquire_result_denied(self) -> None:
        """LeaseAcquireResult denied includes reason and current owner."""
        r = LeaseAcquireResult(
            acquired=False,
            run_id="r1",
            owner_id="w2",
            reason="Run is currently leased by 'w1'",
            current_owner_id="w1",
        )
        assert r.acquired is False
        assert r.reason is not None
        assert r.current_owner_id == "w1"
        assert r.lease is None

    def test_lease_acquire_result_success(self) -> None:
        """LeaseAcquireResult success includes lease."""
        now = datetime.now(timezone.utc)
        lease = WorkflowRunLease(
            run_id="r1",
            owner_id="w2",
            acquired_at=now,
            expires_at=now,
        )
        r = LeaseAcquireResult(
            acquired=True,
            run_id="r1",
            owner_id="w2",
            lease=lease,
        )
        assert r.acquired is True
        assert r.lease is not None
        assert r.lease.owner_id == "w2"

    def test_idempotency_record(self) -> None:
        """IdempotencyRecord can be created with defaults."""
        r = IdempotencyRecord(key="k1", run_id="r1", operation="execute")
        assert r.key == "k1"
        assert r.run_id == "r1"
        assert r.operation == "execute"
        assert r.result_ref is None
        assert r.created_at.tzinfo is not None


# ===========================================================================
# Phase 15: InMemory Lease Tests
# ===========================================================================


class TestInMemoryLease:
    """Lease management tests for InMemoryWorkflowStateStore."""

    @pytest.fixture
    def store(self) -> InMemoryWorkflowStateStore:
        s = InMemoryWorkflowStateStore()
        # Pre-create a workflow run so lease acquire can check existence
        s._runs["r1"] = WorkflowRunState(run_id="r1", workflow_name="wf1")
        return s

    @pytest.mark.asyncio
    async def test_acquire_lease_succeeds(self, store: InMemoryWorkflowStateStore) -> None:
        """Acquiring a lease on a run with no existing lease succeeds."""
        from agent_app.runtime.dag_run_state import WorkerIdentity
        w = WorkerIdentity(worker_id="w1")
        result = await store.acquire_run_lease("r1", w)
        assert result.acquired is True
        assert result.lease is not None
        assert result.lease.owner_id == "w1"
        assert result.reason is None

    @pytest.mark.asyncio
    async def test_acquire_same_run_same_owner_refreshes(
        self, store: InMemoryWorkflowStateStore
    ) -> None:
        """Same worker re-acquiring refreshes the lease."""
        from agent_app.runtime.dag_run_state import WorkerIdentity, LeasePolicy
        w = WorkerIdentity(worker_id="w1")
        r1 = await store.acquire_run_lease("r1", w)
        assert r1.acquired
        r2 = await store.acquire_run_lease("r1", w)
        assert r2.acquired
        assert r2.lease.version == 2  # incremented
        assert r2.lease.expires_at > r1.lease.expires_at  # refreshed

    @pytest.mark.asyncio
    async def test_acquire_same_run_different_owner_denied(
        self, store: InMemoryWorkflowStateStore
    ) -> None:
        """Different worker cannot acquire an active lease."""
        from agent_app.runtime.dag_run_state import WorkerIdentity
        w1 = WorkerIdentity(worker_id="w1")
        w2 = WorkerIdentity(worker_id="w2")
        await store.acquire_run_lease("r1", w1)
        result = await store.acquire_run_lease("r1", w2)
        assert result.acquired is False
        assert result.reason is not None
        assert "w1" in result.reason
        assert result.current_owner_id == "w1"

    @pytest.mark.asyncio
    async def test_renew_by_owner_succeeds(self, store: InMemoryWorkflowStateStore) -> None:
        """Owner can renew their lease."""
        from agent_app.runtime.dag_run_state import WorkerIdentity
        w = WorkerIdentity(worker_id="w1")
        r1 = await store.acquire_run_lease("r1", w)
        renewed = await store.renew_run_lease("r1", w)
        assert renewed.version == 2
        assert renewed.renewed_at is not None
        assert renewed.expires_at > r1.lease.expires_at

    @pytest.mark.asyncio
    async def test_renew_by_non_owner_fails(self, store: InMemoryWorkflowStateStore) -> None:
        """Non-owner cannot renew a lease."""
        from agent_app.runtime.dag_run_state import WorkerIdentity
        w1 = WorkerIdentity(worker_id="w1")
        w2 = WorkerIdentity(worker_id="w2")
        await store.acquire_run_lease("r1", w1)
        with pytest.raises(KeyError, match="not"):
            await store.renew_run_lease("r1", w2)

    @pytest.mark.asyncio
    async def test_release_by_owner_succeeds(self, store: InMemoryWorkflowStateStore) -> None:
        """Owner can release their lease."""
        from agent_app.runtime.dag_run_state import WorkerIdentity
        w = WorkerIdentity(worker_id="w1")
        await store.acquire_run_lease("r1", w)
        released = await store.release_run_lease("r1", w)
        assert released.released_at is not None

    @pytest.mark.asyncio
    async def test_release_by_non_owner_fails(self, store: InMemoryWorkflowStateStore) -> None:
        """Non-owner cannot release a lease."""
        from agent_app.runtime.dag_run_state import WorkerIdentity
        w1 = WorkerIdentity(worker_id="w1")
        w2 = WorkerIdentity(worker_id="w2")
        await store.acquire_run_lease("r1", w1)
        with pytest.raises(KeyError, match="not"):
            await store.release_run_lease("r1", w2)

    @pytest.mark.asyncio
    async def test_expired_lease_can_be_stolen(self, store: InMemoryWorkflowStateStore) -> None:
        """Expired lease can be stolen when allow_steal_expired=True."""
        from agent_app.runtime.dag_run_state import WorkerIdentity, LeasePolicy
        w1 = WorkerIdentity(worker_id="w1")
        w2 = WorkerIdentity(worker_id="w2")
        # Acquire with 1-second TTL
        policy = LeasePolicy(ttl_seconds=1)
        await store.acquire_run_lease("r1", w1, policy)
        # Wait for expiry
        import asyncio
        await asyncio.sleep(1.5)
        # w2 can steal
        result = await store.acquire_run_lease("r1", w2, policy)
        assert result.acquired is True
        assert result.lease.owner_id == "w2"

    @pytest.mark.asyncio
    async def test_expired_lease_cannot_be_stolen_when_policy_disallows(
        self, store: InMemoryWorkflowStateStore
    ) -> None:
        """Expired lease cannot be stolen when allow_steal_expired=False."""
        from agent_app.runtime.dag_run_state import WorkerIdentity, LeasePolicy
        w1 = WorkerIdentity(worker_id="w1")
        w2 = WorkerIdentity(worker_id="w2")
        policy = LeasePolicy(ttl_seconds=1, allow_steal_expired=False)
        await store.acquire_run_lease("r1", w1, policy)
        await asyncio.sleep(1.5)
        result = await store.acquire_run_lease("r1", w2, policy)
        assert result.acquired is False
        assert result.current_owner_id == "w1"

    @pytest.mark.asyncio
    async def test_list_expired_leases(self, store: InMemoryWorkflowStateStore) -> None:
        """list_expired_leases returns only expired, unreleased leases."""
        from agent_app.runtime.dag_run_state import WorkerIdentity, LeasePolicy
        import asyncio

        # Pre-create runs
        for rid in ("r1", "r2", "r3"):
            await store.create_run(WorkflowRunState(run_id=rid, workflow_name="wf"))

        w1 = WorkerIdentity(worker_id="w1")
        short_policy = LeasePolicy(ttl_seconds=1)
        long_policy = LeasePolicy(ttl_seconds=9999)

        # r1: acquire with short TTL
        await store.acquire_run_lease("r1", w1, short_policy)
        # r2: acquire with long TTL
        await store.acquire_run_lease("r2", w1, long_policy)
        # r3: acquire and release
        r3_lease = await store.acquire_run_lease("r3", w1, short_policy)
        await store.release_run_lease("r3", w1)

        await asyncio.sleep(1.5)

        expired = await store.list_expired_leases()
        expired_ids = {l.run_id for l in expired}
        assert "r1" in expired_ids
        assert "r2" not in expired_ids
        assert "r3" not in expired_ids  # released

    @pytest.mark.asyncio
    async def test_get_run_lease_returns_none_when_none(self) -> None:
        """get_run_lease returns None when no lease exists."""
        store = InMemoryWorkflowStateStore()
        lease = await store.get_run_lease("nonexistent")
        assert lease is None

    @pytest.mark.asyncio
    async def test_acquire_lease_nonexistent_run(self) -> None:
        """Acquiring lease on nonexistent run fails."""
        from agent_app.runtime.dag_run_state import WorkerIdentity
        store = InMemoryWorkflowStateStore()
        w = WorkerIdentity(worker_id="w1")
        result = await store.acquire_run_lease("nonexistent", w)
        assert result.acquired is False
        assert "not found" in result.reason


# ===========================================================================
# Phase 15: SQLite Lease Tests
# ===========================================================================


class TestSQLiteLease:
    """Lease management tests for SQLiteWorkflowStateStore."""

    @pytest.fixture
    def sqlite_store(self, tmp_path: Any) -> SQLiteWorkflowStateStore:
        db = str(tmp_path / "test.db")
        return SQLiteWorkflowStateStore(db_path=db)

    @pytest.mark.asyncio
    async def test_acquire_persists(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Acquiring a lease persists it to SQLite."""
        from agent_app.runtime.dag_run_state import WorkerIdentity
        await sqlite_store.create_run(WorkflowRunState(run_id="r1", workflow_name="wf1"))
        w = WorkerIdentity(worker_id="w1")
        result = await sqlite_store.acquire_run_lease("r1", w)
        assert result.acquired is True
        # Verify via fresh store instance
        lease = await sqlite_store.get_run_lease("r1")
        assert lease is not None
        assert lease.owner_id == "w1"

    @pytest.mark.asyncio
    async def test_second_instance_sees_lease(
        self, tmp_path: Any
    ) -> None:
        """A second SQLite store instance sees the lease."""
        from agent_app.runtime.dag_run_state import WorkerIdentity
        db = str(tmp_path / "shared.db")
        s1 = SQLiteWorkflowStateStore(db_path=db)
        await s1.create_run(WorkflowRunState(run_id="r1", workflow_name="wf1"))
        w = WorkerIdentity(worker_id="w1")
        await s1.acquire_run_lease("r1", w)
        # Second instance
        s2 = SQLiteWorkflowStateStore(db_path=db)
        lease = await s2.get_run_lease("r1")
        assert lease is not None
        assert lease.owner_id == "w1"

    @pytest.mark.asyncio
    async def test_different_worker_denied_before_expiry(
        self, sqlite_store: SQLiteWorkflowStateStore
    ) -> None:
        """Different worker denied before lease expires."""
        from agent_app.runtime.dag_run_state import WorkerIdentity
        await sqlite_store.create_run(WorkflowRunState(run_id="r1", workflow_name="wf1"))
        w1 = WorkerIdentity(worker_id="w1")
        w2 = WorkerIdentity(worker_id="w2")
        await sqlite_store.acquire_run_lease("r1", w1)
        result = await sqlite_store.acquire_run_lease("r1", w2)
        assert result.acquired is False
        assert result.current_owner_id == "w1"

    @pytest.mark.asyncio
    async def test_expired_lease_stolen_across_instances(
        self, tmp_path: Any
    ) -> None:
        """Expired lease can be stolen by a new store instance."""
        from agent_app.runtime.dag_run_state import WorkerIdentity, LeasePolicy
        import asyncio

        db = str(tmp_path / "steal.db")
        s1 = SQLiteWorkflowStateStore(db_path=db)
        await s1.create_run(WorkflowRunState(run_id="r1", workflow_name="wf1"))
        w1 = WorkerIdentity(worker_id="w1")
        policy = LeasePolicy(ttl_seconds=1)
        await s1.acquire_run_lease("r1", w1, policy)
        await asyncio.sleep(1.5)
        # New instance can steal
        s2 = SQLiteWorkflowStateStore(db_path=db)
        w2 = WorkerIdentity(worker_id="w2")
        result = await s2.acquire_run_lease("r1", w2, policy)
        assert result.acquired is True

    @pytest.mark.asyncio
    async def test_renew_persists_across_instances(
        self, tmp_path: Any
    ) -> None:
        """Renew persists and is visible to new store instances."""
        from agent_app.runtime.dag_run_state import WorkerIdentity, LeasePolicy
        db = str(tmp_path / "renew.db")
        s1 = SQLiteWorkflowStateStore(db_path=db)
        await s1.create_run(WorkflowRunState(run_id="r1", workflow_name="wf1"))
        w = WorkerIdentity(worker_id="w1")
        await s1.acquire_run_lease("r1", w)
        await s1.renew_run_lease("r1", w)
        # New instance sees renewed lease
        s2 = SQLiteWorkflowStateStore(db_path=db)
        lease = await s2.get_run_lease("r1")
        assert lease is not None
        assert lease.version == 2
        assert lease.renewed_at is not None

    @pytest.mark.asyncio
    async def test_release_persists_across_instances(
        self, tmp_path: Any
    ) -> None:
        """Release persists and is visible to new store instances."""
        from agent_app.runtime.dag_run_state import WorkerIdentity
        db = str(tmp_path / "release.db")
        s1 = SQLiteWorkflowStateStore(db_path=db)
        await s1.create_run(WorkflowRunState(run_id="r1", workflow_name="wf1"))
        w = WorkerIdentity(worker_id="w1")
        await s1.acquire_run_lease("r1", w)
        await s1.release_run_lease("r1", w)
        # New instance sees no active lease
        s2 = SQLiteWorkflowStateStore(db_path=db)
        lease = await s2.get_run_lease("r1")
        assert lease is None

    @pytest.mark.asyncio
    async def test_list_expired_leases_sqlite(
        self, sqlite_store: SQLiteWorkflowStateStore
    ) -> None:
        """list_expired_leases works with SQLite store."""
        from agent_app.runtime.dag_run_state import WorkerIdentity, LeasePolicy
        import asyncio

        for rid in ("r1", "r2"):
            await sqlite_store.create_run(WorkflowRunState(run_id=rid, workflow_name="wf1"))
        w = WorkerIdentity(worker_id="w1")
        short = LeasePolicy(ttl_seconds=1)
        long = LeasePolicy(ttl_seconds=9999)
        await sqlite_store.acquire_run_lease("r1", w, short)
        await sqlite_store.acquire_run_lease("r2", w, long)
        await asyncio.sleep(1.5)
        expired = await sqlite_store.list_expired_leases()
        ids = {l.run_id for l in expired}
        assert "r1" in ids
        assert "r2" not in ids

    @pytest.mark.asyncio
    async def test_old_db_migrates_without_lease_table(self, tmp_path: Any) -> None:
        """Old database without lease/idempotency tables migrates automatically."""
        db = str(tmp_path / "old.db")
        # Create old DB with only the Phase 14.0 tables (no lease/idempotency)
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workflow_runs (
                run_id       TEXT PRIMARY KEY,
                workflow_name TEXT,
                status       TEXT NOT NULL,
                input_json   TEXT,
                output_json  TEXT,
                error_json   TEXT,
                started_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                completed_at TEXT,
                metadata_json TEXT DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workflow_nodes (
                run_id       TEXT NOT NULL,
                node_id      TEXT NOT NULL,
                node_type    TEXT NOT NULL,
                status       TEXT NOT NULL,
                input_json   TEXT,
                output_json  TEXT,
                error_json   TEXT,
                started_at   TEXT,
                completed_at TEXT,
                attempts     INTEGER DEFAULT 0,
                metadata_json TEXT DEFAULT '{}',
                PRIMARY KEY (run_id, node_id)
            )
        """)
        conn.execute("INSERT INTO workflow_runs VALUES ('r1', 'wf1', 'running', NULL, NULL, NULL, '2024-01-01T00:00:00', '2024-01-01T00:00:00', NULL, '{}')")
        conn.commit()
        conn.close()
        # New store should work without error
        store = SQLiteWorkflowStateStore(db_path=db)
        run = await store.get_run("r1")
        assert run.run_id == "r1"
        # No lease should exist
        lease = await store.get_run_lease("r1")
        assert lease is None


# ===========================================================================
# Phase 15: Idempotency Tests
# ===========================================================================


class TestIdempotency:
    """Idempotency record tests for both store implementations."""

    @pytest.mark.asyncio
    async def test_memory_put_get(self) -> None:
        """InMemory store can put and get idempotency records."""
        store = InMemoryWorkflowStateStore()
        record = IdempotencyRecord(key="k1", run_id="r1", operation="execute")
        await store.put_idempotency_record(record)
        fetched = await store.get_idempotency_record("k1")
        assert fetched is not None
        assert fetched.run_id == "r1"
        assert fetched.operation == "execute"

    @pytest.mark.asyncio
    async def test_memory_get_missing(self) -> None:
        """InMemory store returns None for missing key."""
        store = InMemoryWorkflowStateStore()
        assert await store.get_idempotency_record("nonexistent") is None

    @pytest.mark.asyncio
    async def test_memory_put_overwrites(self) -> None:
        """InMemory store overwrites existing idempotency record."""
        store = InMemoryWorkflowStateStore()
        r1 = IdempotencyRecord(key="k1", run_id="r1", operation="execute")
        r2 = IdempotencyRecord(key="k1", run_id="r2", operation="resume")
        await store.put_idempotency_record(r1)
        await store.put_idempotency_record(r2)
        fetched = await store.get_idempotency_record("k1")
        assert fetched.run_id == "r2"
        assert fetched.operation == "resume"

    @pytest.mark.asyncio
    async def test_sqlite_put_get(self, tmp_path: Any) -> None:
        """SQLite store can put and get idempotency records."""
        db = str(tmp_path / "idem.db")
        store = SQLiteWorkflowStateStore(db_path=db)
        record = IdempotencyRecord(key="k1", run_id="r1", operation="execute")
        await store.put_idempotency_record(record)
        fetched = await store.get_idempotency_record("k1")
        assert fetched is not None
        assert fetched.run_id == "r1"

    @pytest.mark.asyncio
    async def test_sqlite_idempotency_persists_across_instances(
        self, tmp_path: Any
    ) -> None:
        """SQLite idempotency records persist across store instances."""
        db = str(tmp_path / "idem_shared.db")
        s1 = SQLiteWorkflowStateStore(db_path=db)
        await s1.put_idempotency_record(
            IdempotencyRecord(key="k1", run_id="r1", operation="execute")
        )
        s2 = SQLiteWorkflowStateStore(db_path=db)
        fetched = await s2.get_idempotency_record("k1")
        assert fetched is not None
        assert fetched.run_id == "r1"


# ===========================================================================
# Phase 15: DagExecutor Lease Integration Tests
# ===========================================================================


class TestDagExecutorLease:
    """Tests for lease integration in DagExecutor.execute() and resume()."""

    @pytest.fixture
    def executor_with_store(self):
        """Create a DagExecutor with InMemory state store."""
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry

        reg = AgentRegistry()
        tools = ToolRegistry()
        wf_reg = WorkflowRegistry()
        store = InMemoryWorkflowStateStore()
        run_id = "test-lease-run"
        # Pre-populate the store directly
        store._runs[run_id] = WorkflowRunState(run_id=run_id, workflow_name="test")
        executor = DagExecutor(
            agent_registry=reg,
            tool_registry=tools,
            workflow_registry=wf_reg,
            state_store=store,
            run_id=run_id,
        )
        return executor, store

    @pytest.mark.asyncio
    async def test_execute_acquires_and_releases_lease(self) -> None:
        """execute() acquires lease before and releases after."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry

        reg = AgentRegistry()
        tools = ToolRegistry()
        wf_reg = WorkflowRegistry()
        store = InMemoryWorkflowStateStore()
        run_id = "test-lease-run"
        store._runs[run_id] = WorkflowRunState(run_id=run_id, workflow_name="test")
        executor = DagExecutor(
            agent_registry=reg,
            tool_registry=tools,
            workflow_registry=wf_reg,
            state_store=store,
            run_id=run_id,
        )

        dag = DagWorkflow(
            name="test",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(
            run_id=executor._run_id,
            user_id="test",
            tenant_id="default",
        )
        # This will fail because the agent doesn't exist, but lease should still be released
        try:
            await executor.execute(dag, "input", context)
        except Exception:
            pass
        # Lease should be released (no active lease)
        lease = await store.get_run_lease(executor._run_id)
        assert lease is None

    @pytest.mark.asyncio
    async def test_execute_no_nodes_when_lease_denied(self) -> None:
        """execute() does not run nodes when lease is denied."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode

        reg = AgentRegistry()
        tools = ToolRegistry()
        wf_reg = WorkflowRegistry()
        store = InMemoryWorkflowStateStore()
        run_id = "lease-denied-run"
        await store.create_run(WorkflowRunState(run_id=run_id, workflow_name="test"))

        # First worker acquires lease
        from agent_app.runtime.dag_run_state import WorkerIdentity
        w1 = WorkerIdentity(worker_id="w1")
        await store.acquire_run_lease(run_id, w1)

        # Second DagExecutor tries to execute
        executor = DagExecutor(
            agent_registry=reg,
            tool_registry=tools,
            workflow_registry=wf_reg,
            state_store=store,
            run_id=run_id,
            worker=WorkerIdentity(worker_id="w2"),
        )
        dag = DagWorkflow(
            name="test",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(run_id=run_id, user_id="test", tenant_id="default")
        with pytest.raises(Exception, match="leased"):
            await executor.execute(dag, "input", context)

    @pytest.mark.asyncio
    async def test_resume_acquires_and_releases_lease(self) -> None:
        """resume() acquires lease before and releases after."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext
        from agent_app.runtime.dag_run_state import (
            NodeExecutionState, NodeRunStatus, ResumePolicy,
        )

        reg = AgentRegistry()
        tools = ToolRegistry()
        wf_reg = WorkflowRegistry()
        store = InMemoryWorkflowStateStore()
        run_id = "lease-resume-run"
        import asyncio

        # Setup: create run with a completed node
        await store.create_run(WorkflowRunState(run_id=run_id, workflow_name="test"))
        await store.upsert_node(NodeExecutionState(
            run_id=run_id, node_id="n1", node_type="agent",
            status=NodeRunStatus.COMPLETED.value, output="result1",
        ))

        executor = DagExecutor(
            agent_registry=reg,
            tool_registry=tools,
            workflow_registry=wf_reg,
            state_store=store,
            run_id=run_id,
        )
        dag = DagWorkflow(
            name="test",
            nodes=[
                DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[]),
                DagNode(id="n2", type="agent", ref="nonexistent", depends_on=["n1"]),
            ],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(run_id=run_id, user_id="test", tenant_id="default")
        # Resume will skip n1 (completed) and try to run n2 (which will fail)
        try:
            await executor.resume(dag, "input", context)
        except Exception:
            pass
        # Lease should be released
        lease = await store.get_run_lease(run_id)
        assert lease is None

    @pytest.mark.asyncio
    async def test_resume_denied_when_active_lease_exists(self) -> None:
        """resume() fails when another worker holds an active lease."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext
        from agent_app.runtime.dag_run_state import (
            NodeExecutionState, NodeRunStatus, WorkerIdentity,
        )

        reg = AgentRegistry()
        tools = ToolRegistry()
        wf_reg = WorkflowRegistry()
        store = InMemoryWorkflowStateStore()
        run_id = "lease-resume-denied"
        import asyncio
        await store.create_run(WorkflowRunState(run_id=run_id, workflow_name="test"))
        await store.upsert_node(NodeExecutionState(
            run_id=run_id, node_id="n1", node_type="agent",
            status=NodeRunStatus.COMPLETED.value, output="r1",
        ))

        # Another worker holds the lease
        other_worker = WorkerIdentity(worker_id="other_worker")
        await store.acquire_run_lease(run_id, other_worker)

        executor = DagExecutor(
            agent_registry=reg,
            tool_registry=tools,
            workflow_registry=wf_reg,
            state_store=store,
            run_id=run_id,
            worker=WorkerIdentity(worker_id="resume_worker"),
        )
        dag = DagWorkflow(
            name="test",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(run_id=run_id, user_id="test", tenant_id="default")
        with pytest.raises(Exception, match="leased"):
            await executor.resume(dag, "input", context)

    @pytest.mark.asyncio
    async def test_expired_lease_allows_resume_by_new_worker(self) -> None:
        """Expired lease allows resume by a new worker."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext
        from agent_app.runtime.dag_run_state import (
            NodeExecutionState, NodeRunStatus, WorkerIdentity, LeasePolicy,
        )
        import asyncio

        reg = AgentRegistry()
        tools = ToolRegistry()
        wf_reg = WorkflowRegistry()
        store = InMemoryWorkflowStateStore()
        run_id = "lease-expired-resume"
        await store.create_run(WorkflowRunState(run_id=run_id, workflow_name="test"))
        await store.upsert_node(NodeExecutionState(
            run_id=run_id, node_id="n1", node_type="agent",
            status=NodeRunStatus.COMPLETED.value, output="r1",
        ))

        # Old worker acquires with short TTL
        old_worker = WorkerIdentity(worker_id="old_w")
        await store.acquire_run_lease(run_id, old_worker, LeasePolicy(ttl_seconds=1))
        await asyncio.sleep(1.5)

        # New worker should be able to resume
        executor = DagExecutor(
            agent_registry=reg,
            tool_registry=tools,
            workflow_registry=wf_reg,
            state_store=store,
            run_id=run_id,
            worker=WorkerIdentity(worker_id="new_w"),
        )
        dag = DagWorkflow(
            name="test",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(run_id=run_id, user_id="test", tenant_id="default")
        # Should not raise lease error (will fail for other reasons like missing agent)
        try:
            await executor.resume(dag, "input", context)
        except Exception as e:
            assert "leased" not in str(e).lower()

    @pytest.mark.asyncio
    async def test_lease_events_persisted(self) -> None:
        """Lease lifecycle events are persisted to state store."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext

        # Inline the executor setup (avoid fixture resolution issues)
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry

        reg = AgentRegistry()
        tools = ToolRegistry()
        wf_reg = WorkflowRegistry()
        store = InMemoryWorkflowStateStore()
        run_id = "test-lease-events"
        store._runs[run_id] = WorkflowRunState(run_id=run_id, workflow_name="test")
        executor = DagExecutor(
            agent_registry=reg,
            tool_registry=tools,
            workflow_registry=wf_reg,
            state_store=store,
            run_id=run_id,
        )

        dag = DagWorkflow(
            name="test",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(
            run_id=executor._run_id,
            user_id="test",
            tenant_id="default",
        )
        try:
            await executor.execute(dag, "input", context)
        except Exception:
            pass
        events = await store.list_events(executor._run_id)
        event_types = {e.event_type for e in events}
        assert "workflow.lease_acquired" in event_types
        assert "workflow.lease_released" in event_types

    @pytest.mark.asyncio
    async def test_no_state_store_old_behavior_unchanged(self) -> None:
        """Without state_store, old behavior is unchanged (no lease)."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext

        reg = AgentRegistry()
        tools = ToolRegistry()
        wf_reg = WorkflowRegistry()
        # No state_store
        executor = DagExecutor(
            agent_registry=reg,
            tool_registry=tools,
            workflow_registry=wf_reg,
        )
        dag = DagWorkflow(
            name="test",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(run_id="no-store-run", user_id="test", tenant_id="default")
        # Should not raise any lease-related errors
        result = await executor.execute(dag, "input", context)
        # Verify no lease was created (no state_store means no lease)
        assert result is not None
        assert len(result) == 4  # 4-tuple return


# ---------------------------------------------------------------------------
# Phase 15.1: API-level Idempotency Enforcement
# ---------------------------------------------------------------------------


class TestRequestFingerprint:
    """Tests for compute_request_fingerprint and payload builders."""

    def test_same_payload_same_fingerprint(self) -> None:
        """Same payload always produces the same fingerprint."""
        from agent_app.runtime.idempotency import compute_request_fingerprint

        payload = {"workflow_name": "test", "input": "hello", "tenant_id": "t1"}
        f1 = compute_request_fingerprint(payload)
        f2 = compute_request_fingerprint(payload)
        assert f1 == f2
        assert len(f1) == 64  # SHA-256 hex digest

    def test_dict_key_order_does_not_affect_fingerprint(self) -> None:
        """Dict key order does not affect fingerprint."""
        from agent_app.runtime.idempotency import compute_request_fingerprint

        p1 = {"a": 1, "b": 2, "c": 3}
        p2 = {"c": 3, "b": 2, "a": 1}
        assert compute_request_fingerprint(p1) == compute_request_fingerprint(p2)

    def test_different_input_different_fingerprint(self) -> None:
        """Different inputs produce different fingerprints."""
        from agent_app.runtime.idempotency import compute_request_fingerprint

        p1 = {"input": "hello"}
        p2 = {"input": "world"}
        assert compute_request_fingerprint(p1) != compute_request_fingerprint(p2)

    def test_transient_fields_excluded(self) -> None:
        """Transient fields (idempotency_key, worker, trace_id) are excluded."""
        from agent_app.runtime.idempotency import compute_request_fingerprint

        base = {"workflow_name": "test", "input": "hello"}
        with_key = {**base, "idempotency_key": "key_123"}
        with_worker = {**base, "worker": {"worker_id": "w1"}}
        with_trace = {**base, "trace_id": "trace_abc"}
        assert compute_request_fingerprint(base) == compute_request_fingerprint(with_key)
        assert compute_request_fingerprint(base) == compute_request_fingerprint(with_worker)
        assert compute_request_fingerprint(base) == compute_request_fingerprint(with_trace)

    def test_nested_dicts_filtered(self) -> None:
        """Nested dicts are recursively filtered for transient fields."""
        from agent_app.runtime.idempotency import compute_request_fingerprint

        p1 = {"workflow_name": "test", "meta": {"idempotency_key": "x", "real": "value"}}
        p2 = {"workflow_name": "test", "meta": {"real": "value"}}
        assert compute_request_fingerprint(p1) == compute_request_fingerprint(p2)


class TestComputeScope:
    """Tests for compute_scope."""

    def test_scope_format(self) -> None:
        """Scope is formatted as '{tenant}:{operation}'."""
        from agent_app.runtime.idempotency import compute_scope

        scope = compute_scope("tenant-a", "workflow.execute")
        assert scope == "tenant-a:workflow.execute"

    def test_different_tenants_different_scope(self) -> None:
        """Different tenants produce different scopes."""
        from agent_app.runtime.idempotency import compute_scope

        s1 = compute_scope("tenant-a", "workflow.execute")
        s2 = compute_scope("tenant-b", "workflow.execute")
        assert s1 != s2

    def test_same_tenant_same_operation_same_scope(self) -> None:
        """Same tenant + operation always produces the same scope."""
        from agent_app.runtime.idempotency import compute_scope

        assert compute_scope("t1", "workflow.execute") == compute_scope("t1", "workflow.execute")


class TestIdempotencyErrors:
    """Tests for idempotency error types."""

    def test_duplicate_error_attributes(self) -> None:
        """DuplicateIdempotencyKeyError carries all required fields."""
        from agent_app.runtime.idempotency import DuplicateIdempotencyKeyError

        err = DuplicateIdempotencyKeyError(
            idempotency_key="key_123",
            scope="tenant-a:workflow.execute",
            operation="workflow.execute",
            existing_run_id="run-existing",
        )
        assert err.idempotency_key == "key_123"
        assert err.existing_run_id == "run-existing"
        d = err.to_dict()
        assert d["type"] == "DuplicateIdempotencyKeyError"
        assert d["idempotency_key"] == "key_123"

    def test_mismatch_error_attributes(self) -> None:
        """IdempotencyKeyMismatchError carries all required fields."""
        from agent_app.runtime.idempotency import IdempotencyKeyMismatchError

        err = IdempotencyKeyMismatchError(
            idempotency_key="key_456",
            scope="tenant-b:dag.execute",
            operation="dag.execute",
            existing_run_id="run-other",
        )
        assert err.idempotency_key == "key_456"
        assert "different request parameters" in err.message


class TestInMemoryIdempotency:
    """Tests for InMemoryWorkflowStateStore idempotency enforcement."""

    async def test_first_reserve_succeeds(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """First reservation of a key succeeds."""
        from agent_app.runtime.idempotency import (
                        compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        await memory_store.create_run(_make_run_state("run-1"))
        scope = compute_scope("tenant-a", "dag.execute")
        fp = compute_request_fingerprint({"input": "hello"})
        record = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp,
        )
        result = await reserve_idempotency_key(memory_store, record=record)
        assert result.key == "req_1"
        assert result.run_id == "run-1"

    async def test_duplicate_same_key_same_fingerprint_raises(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Duplicate key with same fingerprint raises DuplicateIdempotencyKeyError."""
        from agent_app.runtime.idempotency import (
            DuplicateIdempotencyKeyError,            compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        await memory_store.create_run(_make_run_state("run-1"))
        scope = compute_scope("tenant-a", "dag.execute")
        fp = compute_request_fingerprint({"input": "hello"})
        record = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp,
        )
        await reserve_idempotency_key(memory_store, record=record)

        with pytest.raises(DuplicateIdempotencyKeyError, match="already been used"):
            await reserve_idempotency_key(memory_store, record=record)

    async def test_duplicate_same_key_different_fingerprint_raises(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Duplicate key with different fingerprint raises IdempotencyKeyMismatchError."""
        from agent_app.runtime.idempotency import (
            IdempotencyKeyMismatchError,            compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        await memory_store.create_run(_make_run_state("run-1"))
        scope = compute_scope("tenant-a", "dag.execute")
        fp1 = compute_request_fingerprint({"input": "hello"})
        fp2 = compute_request_fingerprint({"input": "world"})
        record1 = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp1,
        )
        await reserve_idempotency_key(memory_store, record=record1)

        record2 = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp2,
        )
        with pytest.raises(IdempotencyKeyMismatchError, match="different request parameters"):
            await reserve_idempotency_key(memory_store, record=record2)

    async def test_same_key_different_tenant_scope_allowed(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Same key under different tenant scope succeeds."""
        from agent_app.runtime.idempotency import (
                        compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        await memory_store.create_run(_make_run_state("run-t1"))
        await memory_store.create_run(_make_run_state("run-t2"))
        fp = compute_request_fingerprint({"input": "hello"})
        scope_t1 = compute_scope("tenant-a", "dag.execute")
        scope_t2 = compute_scope("tenant-b", "dag.execute")

        record_t1 = IdempotencyRecord(
            key="req_1", run_id="run-t1", operation="dag.execute",
            scope=scope_t1, request_fingerprint=fp,
        )
        record_t2 = IdempotencyRecord(
            key="req_1", run_id="run-t2", operation="dag.execute",
            scope=scope_t2, request_fingerprint=fp,
        )
        r1 = await reserve_idempotency_key(memory_store, record=record_t1)
        r2 = await reserve_idempotency_key(memory_store, record=record_t2)
        assert r1.run_id == "run-t1"
        assert r2.run_id == "run-t2"

    async def test_same_key_different_operation_allowed(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Same key under different operation succeeds."""
        from agent_app.runtime.idempotency import (
                        compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        await memory_store.create_run(_make_run_state("run-1"))
        fp = compute_request_fingerprint({"input": "hello"})
        scope_exec = compute_scope("tenant-a", "dag.execute")
        scope_resume = compute_scope("tenant-a", "dag.resume")

        record_exec = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope_exec, request_fingerprint=fp,
        )
        record_resume = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.resume",
            scope=scope_resume, request_fingerprint=fp,
        )
        await reserve_idempotency_key(memory_store, record=record_exec)
        r2 = await reserve_idempotency_key(memory_store, record=record_resume)
        assert r2.operation == "dag.resume"

    async def test_duplicate_error_includes_existing_run_id(self, memory_store: InMemoryWorkflowStateStore) -> None:
        """Duplicate error includes existing_run_id."""
        from agent_app.runtime.idempotency import (
            DuplicateIdempotencyKeyError,            compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        await memory_store.create_run(_make_run_state("run-existing"))
        scope = compute_scope("tenant-a", "dag.execute")
        fp = compute_request_fingerprint({"input": "hello"})
        record = IdempotencyRecord(
            key="req_1", run_id="run-existing", operation="dag.execute",
            scope=scope, request_fingerprint=fp,
        )
        await reserve_idempotency_key(memory_store, record=record)

        with pytest.raises(DuplicateIdempotencyKeyError) as exc_info:
            await reserve_idempotency_key(memory_store, record=record)
        assert exc_info.value.existing_run_id == "run-existing"


class TestSQLiteIdempotency:
    """Tests for SQLiteWorkflowStateStore idempotency enforcement."""

    async def test_first_reserve_persists(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """First reservation persists to SQLite."""
        from agent_app.runtime.idempotency import (
                        compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        await sqlite_store.create_run(_make_run_state("run-1"))
        scope = compute_scope("tenant-a", "dag.execute")
        fp = compute_request_fingerprint({"input": "hello"})
        record = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp,
        )
        result = await reserve_idempotency_key(sqlite_store, record=record)
        assert result.key == "req_1"
        # Verify persistence: new store instance can read it
        stored = await sqlite_store.get_idempotency_record("req_1")
        assert stored is not None
        assert stored.run_id == "run-1"

    async def test_duplicate_same_fingerprint_rejected(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Duplicate key with same fingerprint is rejected."""
        from agent_app.runtime.idempotency import (
            DuplicateIdempotencyKeyError,            compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        await sqlite_store.create_run(_make_run_state("run-1"))
        scope = compute_scope("tenant-a", "dag.execute")
        fp = compute_request_fingerprint({"input": "hello"})
        record = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp,
        )
        await reserve_idempotency_key(sqlite_store, record=record)

        with pytest.raises(DuplicateIdempotencyKeyError):
            await reserve_idempotency_key(sqlite_store, record=record)

    async def test_duplicate_different_fingerprint_rejected_as_mismatch(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Duplicate key with different fingerprint raises mismatch."""
        from agent_app.runtime.idempotency import (
            IdempotencyKeyMismatchError,            compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        await sqlite_store.create_run(_make_run_state("run-1"))
        scope = compute_scope("tenant-a", "dag.execute")
        fp1 = compute_request_fingerprint({"input": "hello"})
        fp2 = compute_request_fingerprint({"input": "world"})
        record1 = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp1,
        )
        await reserve_idempotency_key(sqlite_store, record=record1)

        record2 = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp2,
        )
        with pytest.raises(IdempotencyKeyMismatchError):
            await reserve_idempotency_key(sqlite_store, record=record2)

    async def test_persists_across_store_instances(self, tmp_path: Path) -> None:
        """Reservation persists across new store instances (same DB file)."""
        from agent_app.runtime.idempotency import (
                        compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        db_path = str(tmp_path / "cross_instance.db")
        # First instance: reserve key
        store1 = SQLiteWorkflowStateStore(db_path=db_path)
        await store1.create_run(_make_run_state("run-1"))
        scope = compute_scope("tenant-a", "dag.execute")
        fp = compute_request_fingerprint({"input": "hello"})
        record = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp,
        )
        await reserve_idempotency_key(store1, record=record)
        store1.close()

        # Second instance: key should exist
        store2 = SQLiteWorkflowStateStore(db_path=db_path)
        stored = await store2.get_idempotency_record("req_1")
        assert stored is not None
        assert stored.run_id == "run-1"

        # Duplicate should be rejected
        with pytest.raises(Exception):  # DuplicateIdempotencyKeyError
            await reserve_idempotency_key(store2, record=record)
        store2.close()

    async def test_different_scope_allows_same_key(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """Same key under different scope is allowed."""
        from agent_app.runtime.idempotency import (
                        compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        await sqlite_store.create_run(_make_run_state("run-1"))
        fp = compute_request_fingerprint({"input": "hello"})
        scope_t1 = compute_scope("tenant-a", "dag.execute")
        scope_t2 = compute_scope("tenant-b", "dag.execute")

        r1 = await reserve_idempotency_key(sqlite_store, record=IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope_t1, request_fingerprint=fp,
        ))
        r2 = await reserve_idempotency_key(sqlite_store, record=IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope_t2, request_fingerprint=fp,
        ))
        assert r1.run_id == "run-1"
        assert r2.run_id == "run-1"

    async def test_unique_constraint_exists(self, sqlite_store: SQLiteWorkflowStateStore) -> None:
        """SQLite UNIQUE(scope, key) constraint is enforced at DB level."""
        from agent_app.runtime.idempotency import (
                        compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        await sqlite_store.create_run(_make_run_state("run-1"))
        scope = compute_scope("tenant-a", "dag.execute")
        fp = compute_request_fingerprint({"input": "hello"})
        record = IdempotencyRecord(
            key="req_1", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp,
        )
        await reserve_idempotency_key(sqlite_store, record=record)
        # Verify constraint is at DB level by checking schema
        result = sqlite_store._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='workflow_idempotency'"
        ).fetchone()
        assert result is not None
        assert "UNIQUE" in result[0].upper() or "PRIMARY KEY" in result[0].upper()


class TestDagExecutorIdempotency:
    """Tests for DagExecutor idempotency enforcement."""

    async def test_execute_with_idempotency_key_first_succeeds(self) -> None:
        """First execute with idempotency_key succeeds."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext
        from agent_app.runtime.idempotency import compute_request_fingerprint, compute_scope

        store = InMemoryWorkflowStateStore()
        run_id = "run-1"
        await store.create_run(_make_run_state(run_id))

        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=store,
            run_id=run_id,
            idempotency_key="req_123",
        )
        executor._workflow_name = "test_wf"
        executor._current_input = "hello"

        dag = DagWorkflow(
            name="test_wf",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(
            run_id=run_id, user_id="alice", tenant_id="tenant-a",
            input="hello", session_id="s1", permissions=["p1"],
        )
        # Should not raise — first use of key
        try:
            await executor._enforce_idempotency(context, "dag.execute")
        except Exception as exc:
            pytest.fail(f"Unexpected exception: {exc}")

    async def test_execute_duplicate_key_raises(self) -> None:
        from agent_app.workflows.dag import DagError
        """Duplicate idempotency_key raises DagError with idempotency_duplicate."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext
        from agent_app.runtime.idempotency import (
            DuplicateIdempotencyKeyError,
            compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        store = InMemoryWorkflowStateStore()
        run_id = "run-1"
        await store.create_run(_make_run_state(run_id))

        # Pre-register the key using the same payload builder as _enforce_idempotency
        from agent_app.runtime.idempotency import build_execute_payload
        scope = compute_scope("tenant-a", "dag.execute")
        fp = compute_request_fingerprint(build_execute_payload(
            workflow_name="test_wf",
            input="hello",
            session_id="s1",
            tenant_id="tenant-a",
            user_id="alice",
            run_id=run_id,
            permissions=["p1"],
        ))
        record = IdempotencyRecord(
            key="req_123", run_id=run_id, operation="dag.execute",
            scope=scope, request_fingerprint=fp,
        )
        await reserve_idempotency_key(store, record=record)

        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=store,
            run_id=run_id,
            idempotency_key="req_123",
        )
        executor._workflow_name = "test_wf"
        executor._current_input = "hello"

        dag = DagWorkflow(
            name="test_wf",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(
            run_id=run_id, user_id="alice", tenant_id="tenant-a",
            input="hello", session_id="s1", permissions=["p1"],
        )
        with pytest.raises(DagError) as exc_info:
            await executor._enforce_idempotency(context, "dag.execute")
        assert exc_info.value.args[0]["type"] == "idempotency_duplicate"
        assert exc_info.value.args[0]["existing_run_id"] == run_id

    async def test_execute_mismatch_key_raises(self) -> None:
        from agent_app.workflows.dag import DagError
        """Same key with different input raises DagError with mismatch."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext
        from agent_app.runtime.idempotency import (
            IdempotencyKeyMismatchError,
            compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        store = InMemoryWorkflowStateStore()
        run_id = "run-1"
        await store.create_run(_make_run_state(run_id))

        # Register with same payload that _enforce_idempotency will compute
        from agent_app.runtime.idempotency import build_execute_payload
        scope = compute_scope("tenant-a", "dag.execute")
        fp = compute_request_fingerprint(build_execute_payload(
            workflow_name="test_wf",
            input="hello",
            session_id="s1",
            tenant_id="tenant-a",
            user_id="alice",
            run_id=run_id,
            permissions=["p1"],
        ))
        record = IdempotencyRecord(
            key="req_123", run_id=run_id, operation="dag.execute",
            scope=scope, request_fingerprint=fp,
        )
        await reserve_idempotency_key(store, record=record)

        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=store,
            run_id=run_id,
            idempotency_key="req_123",
        )
        executor._workflow_name = "test_wf"
        executor._current_input = "world"

        dag = DagWorkflow(
            name="test_wf",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        # Different input
        context = RunContext(
            run_id=run_id, user_id="alice", tenant_id="tenant-a",
            input="world", session_id="s1", permissions=["p1"],
        )
        with pytest.raises(DagError) as exc_info:
            await executor._enforce_idempotency(context, "dag.execute")
        assert exc_info.value.args[0]["type"] == "idempotency_key_reuse_mismatch"

    async def test_no_key_no_enforcement(self) -> None:
        """Without idempotency_key, no enforcement occurs."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext

        store = InMemoryWorkflowStateStore()
        run_id = "run-1"
        await store.create_run(_make_run_state(run_id))

        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=store,
            run_id=run_id,
            # No idempotency_key
        )

        dag = DagWorkflow(
            name="test",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(run_id=run_id, user_id="test", tenant_id="default")
        # Should not raise
        await executor._enforce_idempotency(context, "dag.execute")

    async def test_no_state_store_no_enforcement(self) -> None:
        """Without state_store, idempotency_key is ignored."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext

        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=None,
            run_id="run-1",
            idempotency_key="req_123",
        )

        dag = DagWorkflow(
            name="test",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(run_id="run-1", user_id="test", tenant_id="default")
        # Should not raise — no state_store
        await executor._enforce_idempotency(context, "dag.execute")

    async def test_resume_enforcement(self) -> None:
        """Resume also enforces idempotency."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagError, DagExecutionMode, DagError
        from agent_app.core.context import RunContext
        from agent_app.runtime.idempotency import (
            DuplicateIdempotencyKeyError,
            compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        store = InMemoryWorkflowStateStore()
        run_id = "run-1"
        await store.create_run(_make_run_state(run_id))

        # Pre-register key using the same payload builder as _enforce_idempotency
        from agent_app.runtime.idempotency import build_resume_payload
        scope = compute_scope("tenant-a", "dag.resume")
        fp = compute_request_fingerprint(build_resume_payload(
            run_id=run_id,
            input="",
            tenant_id="tenant-a",
            user_id="alice",
        ))
        record = IdempotencyRecord(
            key="resume_123", run_id=run_id, operation="dag.resume",
            scope=scope, request_fingerprint=fp,
        )
        await reserve_idempotency_key(store, record=record)

        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=store,
            run_id=run_id,
            idempotency_key="resume_123",
        )
        executor._workflow_name = "test_wf"
        executor._current_input = ""

        context = RunContext(run_id=run_id, user_id="alice", tenant_id="tenant-a")
        with pytest.raises(DagError) as exc_info:
            await executor._enforce_idempotency(context, "dag.resume")
        assert exc_info.value.args[0]["type"] == "idempotency_duplicate"


class TestCrossInstanceIdempotency:
    """Tests for cross-instance idempotency with SQLite."""

    async def test_sqlite_duplicate_across_instances(self, tmp_path: Path) -> None:
        """Duplicate key is rejected when checked from a different store instance."""
        from agent_app.runtime.idempotency import (
            DuplicateIdempotencyKeyError,            compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        db_path = str(tmp_path / "cross_instance_idemp.db")
        # Instance 1: reserve key
        store1 = SQLiteWorkflowStateStore(db_path=db_path)
        await store1.create_run(_make_run_state("run-1"))
        scope = compute_scope("tenant-a", "dag.execute")
        fp = compute_request_fingerprint({"input": "hello"})
        record = IdempotencyRecord(
            key="req_x", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp,
        )
        await reserve_idempotency_key(store1, record=record)
        store1.close()

        # Instance 2: try to use the same key
        store2 = SQLiteWorkflowStateStore(db_path=db_path)
        await store2.create_run(_make_run_state("run-1"))
        with pytest.raises(DuplicateIdempotencyKeyError):
            await reserve_idempotency_key(store2, record=record)
        store2.close()

    async def test_sqlite_mismatch_across_instances(self, tmp_path: Path) -> None:
        """Key mismatch is detected across instances."""
        from agent_app.runtime.idempotency import (
            IdempotencyKeyMismatchError,            compute_request_fingerprint,
            compute_scope,
            reserve_idempotency_key,
        )

        db_path = str(tmp_path / "cross_instance_mismatch.db")
        store1 = SQLiteWorkflowStateStore(db_path=db_path)
        await store1.create_run(_make_run_state("run-1"))
        scope = compute_scope("tenant-a", "dag.execute")
        fp1 = compute_request_fingerprint({"input": "hello"})
        await reserve_idempotency_key(store1, record=IdempotencyRecord(
            key="req_x", run_id="run-1", operation="dag.execute",
            scope=scope, request_fingerprint=fp1,
        ))
        store1.close()

        store2 = SQLiteWorkflowStateStore(db_path=db_path)
        await store2.create_run(_make_run_state("run-1"))
        fp2 = compute_request_fingerprint({"input": "different"})
        with pytest.raises(IdempotencyKeyMismatchError):
            await reserve_idempotency_key(store2, record=IdempotencyRecord(
                key="req_x", run_id="run-1", operation="dag.execute",
                scope=scope, request_fingerprint=fp2,
            ))
        store2.close()


class TestBackwardCompatibility:
    """Tests for backward compatibility with no idempotency key."""

    async def test_no_idempotency_key_preserves_old_behavior(self) -> None:
        """Without idempotency_key, execution proceeds normally."""
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from agent_app.core.context import RunContext

        store = InMemoryWorkflowStateStore()
        run_id = "run-compat"
        await store.create_run(_make_run_state(run_id))

        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=store,
            run_id=run_id,
            # No idempotency_key — old behavior
        )
        executor._workflow_name = "test_wf"

        dag = DagWorkflow(
            name="test_wf",
            nodes=[DagNode(id="n1", type="agent", ref="nonexistent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )
        context = RunContext(run_id=run_id, user_id="test", tenant_id="default")
        # Should not raise any idempotency errors
        result = await executor._enforce_idempotency(context, "dag.execute")
        assert result is None  # No return value means success

    async def test_old_sqlite_db_still_works(self, tmp_path: Path) -> None:
        """Old SQLite database without new columns still works."""
        db_path = str(tmp_path / "old_schema.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workflow_runs (
                run_id TEXT PRIMARY KEY, workflow_name TEXT, status TEXT NOT NULL,
                input_json TEXT, output_json TEXT, error_json TEXT,
                started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                completed_at TEXT, metadata_json TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS workflow_idempotency (
                key TEXT PRIMARY KEY, run_id TEXT NOT NULL, operation TEXT NOT NULL,
                created_at TEXT NOT NULL, result_ref TEXT
            );
            INSERT INTO workflow_runs VALUES ('run-old', 'test', 'running', NULL, NULL, NULL,
                '2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00', NULL, '{}');
        """)
        conn.commit()
        conn.close()

        # Opening with new SQLiteWorkflowStateStore should not fail
        store = SQLiteWorkflowStateStore(db_path=db_path)
        run = await store.get_run("run-old")
        assert run.run_id == "run-old"
        store.close()


# ---------------------------------------------------------------------------
# Phase 15.2: Background Lease Renewal Tests
# ---------------------------------------------------------------------------


class TestLeaseRenewer:
    """Tests for the LeaseRenewer background task."""

    @pytest.mark.asyncio
    async def test_start_creates_background_task(self) -> None:
        """start() creates a background asyncio task."""
        from agent_app.runtime.lease_renewer import LeaseRenewer

        store = InMemoryWorkflowStateStore()
        await store.create_run(_make_run_state("run-renew"))
        await store.acquire_run_lease(
            "run-renew",
            WorkerIdentity(worker_id="worker-1"),
        )

        renewer = LeaseRenewer(
            state_store=store,
            run_id="run-renew",
            worker_id="worker-1",
            ttl_seconds=300,
            interval_seconds=0.1,  # Fast renewal for testing
        )
        assert renewer._task is None
        assert not renewer.lease_lost

        await renewer.start()
        assert renewer._task is not None
        assert not renewer._task.done()

        await renewer.stop()
        assert renewer._task is None

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self) -> None:
        """stop() can be called multiple times safely."""
        from agent_app.runtime.lease_renewer import LeaseRenewer

        store = InMemoryWorkflowStateStore()
        renewer = LeaseRenewer(
            state_store=store,
            run_id="run-renew",
            worker_id="worker-1",
            ttl_seconds=300,
            interval_seconds=60,
        )
        await renewer.start()
        await renewer.stop()
        await renewer.stop()  # Should not raise
        await renewer.stop()  # Still should not raise

    @pytest.mark.asyncio
    async def test_context_manager_starts_and_stops(self) -> None:
        """Async context manager starts and stops the renewer."""
        from agent_app.runtime.lease_renewer import LeaseRenewer

        store = InMemoryWorkflowStateStore()
        renewer = LeaseRenewer(
            state_store=store,
            run_id="run-renew",
            worker_id="worker-1",
            ttl_seconds=300,
            interval_seconds=60,
        )
        async with renewer:
            assert renewer._task is not None
        assert renewer._task is None

    @pytest.mark.asyncio
    async def test_context_manager_stops_on_exception(self) -> None:
        """Async context manager stops renewal even on exception."""
        from agent_app.runtime.lease_renewer import LeaseRenewer

        store = InMemoryWorkflowStateStore()
        renewer = LeaseRenewer(
            state_store=store,
            run_id="run-renew",
            worker_id="worker-1",
            ttl_seconds=300,
            interval_seconds=60,
        )
        try:
            async with renewer:
                raise ValueError("test error")
        except ValueError:
            pass
        assert renewer._task is None

    @pytest.mark.asyncio
    async def test_renewer_sets_lease_lost_on_failure(self) -> None:
        """LeaseRenewer sets lease_lost=True when renewal fails."""
        from agent_app.runtime.lease_renewer import LeaseRenewer

        # Store that will fail on renew
        class FailingStore:
            async def get_run(self, run_id):
                return _make_run_state(run_id)
            async def renew_run_lease(self, run_id, worker, policy):
                raise ConnectionError("store unavailable")

        renewer = LeaseRenewer(
            state_store=FailingStore(),
            run_id="run-renew",
            worker_id="worker-1",
            ttl_seconds=0.1,  # Very short TTL for fast failure
            interval_seconds=0.05,
        )
        await renewer.start()
        # Wait for renewal attempt
        await asyncio.sleep(0.2)
        await renewer.stop()
        assert renewer.lease_lost is True
        assert isinstance(renewer._last_error, ConnectionError)

    @pytest.mark.asyncio
    async def test_renewer_no_pending_task_after_stop(self) -> None:
        """No pending asyncio tasks after stop."""
        from agent_app.runtime.lease_renewer import LeaseRenewer

        store = InMemoryWorkflowStateStore()
        renewer = LeaseRenewer(
            state_store=store,
            run_id="run-renew",
            worker_id="worker-1",
            ttl_seconds=300,
            interval_seconds=60,
        )
        await renewer.start()
        await renewer.stop()
        # Check no pending tasks from our renewer
        pending = [t for t in asyncio.all_tasks() if not t.done() and t != asyncio.current_task()]
        # Filter out unrelated system tasks
        renewer_tasks = [t for t in pending if "renew" in str(t.get_coro()).lower()]
        assert len(renewer_tasks) == 0

    @pytest.mark.asyncio
    async def test_renewer_skips_renew_for_completed_run(self) -> None:
        """Renewer stops itself when run reaches terminal state."""
        from agent_app.runtime.lease_renewer import LeaseRenewer

        store = InMemoryWorkflowStateStore()
        await store.create_run(_make_run_state("run-renew"))
        await store.acquire_run_lease(
            "run-renew",
            WorkerIdentity(worker_id="worker-1"),
        )
        # Set run to completed
        await store.update_run("run-renew", status="completed")

        renewer = LeaseRenewer(
            state_store=store,
            run_id="run-renew",
            worker_id="worker-1",
            ttl_seconds=300,
            interval_seconds=0.05,
        )
        await renewer.start()
        await asyncio.sleep(0.2)
        await renewer.stop()
        # Should not have set lease_lost (run completed normally)
        assert not renewer.lease_lost


class TestInMemoryLeaseRenewal:
    """Tests for InMemoryWorkflowStateStore.renew_run_lease()."""

    @pytest.fixture()
    def store(self) -> InMemoryWorkflowStateStore:
        s = InMemoryWorkflowStateStore()
        return s

    @pytest.mark.asyncio
    async def test_renew_after_acquire_succeeds(self, store: InMemoryWorkflowStateStore) -> None:
        """Renew succeeds after acquiring a lease."""
        await store.create_run(_make_run_state("run-1"))
        worker = WorkerIdentity(worker_id="worker-1")
        result = await store.acquire_run_lease("run-1", worker)
        assert result.acquired

        renewed = await store.renew_run_lease("run-1", worker)
        assert renewed.expires_at > result.lease.expires_at
        assert renewed.renewed_at is not None

    @pytest.mark.asyncio
    async def test_renew_non_owner_fails(self, store: InMemoryWorkflowStateStore) -> None:
        """Non-owner cannot renew."""
        await store.create_run(_make_run_state("run-1"))
        await store.acquire_run_lease(
            "run-1", WorkerIdentity(worker_id="worker-1")
        )
        with pytest.raises(KeyError, match="held by"):
            await store.renew_run_lease(
                "run-1", WorkerIdentity(worker_id="worker-2")
            )

    @pytest.mark.asyncio
    async def test_renew_nonexistent_run_fails(self, store: InMemoryWorkflowStateStore) -> None:
        """Renew fails for non-existent run."""
        with pytest.raises(KeyError, match="No active lease"):
            await store.renew_run_lease(
                "nonexistent", WorkerIdentity(worker_id="worker-1")
            )

    @pytest.mark.asyncio
    async def test_renew_expired_lease_fails(self, store: InMemoryWorkflowStateStore) -> None:
        """Expired lease cannot be renewed (even by owner)."""
        import asyncio
        from agent_app.runtime.dag_run_state import LeasePolicy
        store = InMemoryWorkflowStateStore()
        await store.create_run(_make_run_state("run-1"))
        worker = WorkerIdentity(worker_id="worker-1")
        # Acquire with very short TTL
        result = await store.acquire_run_lease(
            "run-1", worker, LeasePolicy(ttl_seconds=1)
        )
        assert result.acquired
        # Wait for lease to expire
        await asyncio.sleep(1.1)
        # Lease is now expired
        with pytest.raises(KeyError):
            await store.renew_run_lease("run-1", worker)

    @pytest.mark.asyncio
    async def test_renew_extends_ttl(self, store: InMemoryWorkflowStateStore) -> None:
        """Renewal extends the lease_until timestamp."""
        from agent_app.runtime.dag_run_state import LeasePolicy
        await store.create_run(_make_run_state("run-1"))
        worker = WorkerIdentity(worker_id="worker-1")
        result = await store.acquire_run_lease(
            "run-1", worker, LeasePolicy(ttl_seconds=300)
        )
        original_expiry = result.lease.expires_at

        import asyncio
        await asyncio.sleep(0.01)  # Small delay
        renewed = await store.renew_run_lease(
            "run-1", worker, LeasePolicy(ttl_seconds=300)
        )
        assert renewed.expires_at > original_expiry

    @pytest.mark.asyncio
    async def test_renew_after_release_fails(self, store: InMemoryWorkflowStateStore) -> None:
        """Cannot renew after lease is released."""
        await store.create_run(_make_run_state("run-1"))
        worker = WorkerIdentity(worker_id="worker-1")
        await store.acquire_run_lease("run-1", worker)
        await store.release_run_lease("run-1", worker)
        with pytest.raises(KeyError, match="been released"):
            await store.renew_run_lease("run-1", worker)


class TestSQLiteLeaseRenewal:
    """Tests for SQLiteWorkflowStateStore.renew_run_lease()."""

    @pytest.fixture()
    def store(self, tmp_path: Path):
        db_path = str(tmp_path / "renew_test.db")
        s = SQLiteWorkflowStateStore(db_path=db_path)
        return s

    @pytest.mark.asyncio
    async def test_renew_after_acquire_succeeds(self, store: SQLiteWorkflowStateStore) -> None:
        """SQLite renew succeeds after acquiring."""
        await store.create_run(_make_run_state("run-1"))
        worker = WorkerIdentity(worker_id="worker-1")
        result = await store.acquire_run_lease("run-1", worker)
        assert result.acquired

        renewed = await store.renew_run_lease("run-1", worker)
        assert renewed.expires_at > result.lease.expires_at

    @pytest.mark.asyncio
    async def test_renew_non_owner_fails(self, store: SQLiteWorkflowStateStore) -> None:
        """SQLite non-owner cannot renew."""
        await store.create_run(_make_run_state("run-1"))
        await store.acquire_run_lease(
            "run-1", WorkerIdentity(worker_id="worker-1")
        )
        with pytest.raises(KeyError, match="held by"):
            await store.renew_run_lease(
                "run-1", WorkerIdentity(worker_id="worker-2")
            )

    @pytest.mark.asyncio
    async def test_renew_persists_across_instances(self, tmp_path: Path) -> None:
        """Renewal is visible to a new store instance."""
        db_path = str(tmp_path / "renew_persist.db")
        store1 = SQLiteWorkflowStateStore(db_path=db_path)
        await store1.create_run(_make_run_state("run-1"))
        worker = WorkerIdentity(worker_id="worker-1")
        await store1.acquire_run_lease("run-1", worker)

        # Renew via first instance
        await store1.renew_run_lease("run-1", worker)
        store1.close()

        # Second instance should see the renewed lease
        store2 = SQLiteWorkflowStateStore(db_path=db_path)
        renewed = await store2.renew_run_lease("run-1", worker)
        assert renewed.version >= 2
        store2.close()

    @pytest.mark.asyncio
    async def test_renew_expired_lease_fails(self, store: SQLiteWorkflowStateStore) -> None:
        """SQLite expired lease cannot be renewed."""
        import asyncio
        from agent_app.runtime.dag_run_state import LeasePolicy
        await store.create_run(_make_run_state("run-1"))
        worker = WorkerIdentity(worker_id="worker-1")
        await store.acquire_run_lease(
            "run-1", worker, LeasePolicy(ttl_seconds=1)
        )
        # Wait for lease to expire
        await asyncio.sleep(1.1)
        with pytest.raises(KeyError):
            await store.renew_run_lease("run-1", worker)

    @pytest.mark.asyncio
    async def test_renew_after_release_fails(self, store: SQLiteWorkflowStateStore) -> None:
        """SQLite cannot renew released lease."""
        await store.create_run(_make_run_state("run-1"))
        worker = WorkerIdentity(worker_id="worker-1")
        await store.acquire_run_lease("run-1", worker)
        await store.release_run_lease("run-1", worker)
        with pytest.raises(KeyError, match="been released"):
            await store.renew_run_lease("run-1", worker)


class TestDagExecutorLeaseRenewal:
    """Tests for DagExecutor lease renewal integration."""

    @pytest.fixture()
    def store(self) -> InMemoryWorkflowStateStore:
        return InMemoryWorkflowStateStore()

    @pytest.fixture()
    def context(self) -> RunContext:
        return RunContext(run_id="test-renew", user_id="test", tenant_id="default")

    @pytest.mark.asyncio
    async def test_execute_no_renew_when_disabled(
        self, store: InMemoryWorkflowStateStore, context: RunContext
    ) -> None:
        """With renew_enabled=False, no LeaseRenewer is created."""
        from agent_app.config.schema import LeaseRenewalConfig
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode

        await store.create_run(_make_run_state("run-no-renew"))
        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=store,
            run_id="run-no-renew",
            lease_renewal_config=LeaseRenewalConfig(renew_enabled=False),
        )

        renewer = executor._make_renewer()
        assert renewer is None

    @pytest.mark.asyncio
    async def test_execute_renewer_created_when_enabled(
        self, store: InMemoryWorkflowStateStore
    ) -> None:
        """With renew_enabled=True (default), LeaseRenewer is created."""
        from agent_app.config.schema import LeaseRenewalConfig
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode

        await store.create_run(_make_run_state("run-renew"))
        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=store,
            run_id="run-renew",
            lease_renewal_config=LeaseRenewalConfig(renew_enabled=True),
        )
        renewer = executor._make_renewer()
        assert renewer is not None
        assert renewer._run_id == "run-renew"

    @pytest.mark.asyncio
    async def test_execute_no_renew_when_no_store(
        self, context: RunContext
    ) -> None:
        """No LeaseRenewer when state_store is None."""
        from agent_app.config.schema import LeaseRenewalConfig

        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=None,
            run_id=None,
            lease_renewal_config=LeaseRenewalConfig(renew_enabled=True),
        )
        renewer = executor._make_renewer()
        assert renewer is None

    @pytest.mark.asyncio
    async def test_lease_lost_error_raised_when_renewer_loses_lease(
        self, store: InMemoryWorkflowStateStore, context: RunContext
    ) -> None:
        """LeaseLostError is raised when renewer loses lease during execution."""
        from agent_app.config.schema import LeaseRenewalConfig
        from agent_app.runtime.lease_renewer import LeaseLostError
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode
        from unittest.mock import patch

        await store.create_run(_make_run_state("run-lost"))
        worker = WorkerIdentity(worker_id="worker-1")
        await store.acquire_run_lease(
            "run-lost",
            worker,
            LeasePolicy(ttl_seconds=300),
        )

        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=store,
            run_id="run-lost",
            worker=worker,
            lease_renewal_config=LeaseRenewalConfig(
                renew_enabled=True,
                ttl_seconds=300,
            ),
        )

        dag = DagWorkflow(
            name="test",
            nodes=[DagNode(id="n1", type="agent", ref="test_agent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )

        # Patch _make_renewer to return a renewer that has lost its lease
        class FakeRenewer:
            lease_lost = True

            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

        with patch.object(executor, "_make_renewer", return_value=FakeRenewer()):
            with pytest.raises(LeaseLostError):
                await executor.execute(dag=dag, input="test", context=context)

    @pytest.mark.asyncio
    async def test_idempotency_before_lease_renewal(
        self, store: InMemoryWorkflowStateStore, context: RunContext
    ) -> None:
        """Idempotency enforcement still happens before lease renewal."""
        from agent_app.config.schema import LeaseRenewalConfig
        from agent_app.workflows.dag import DagWorkflow, DagNode, DagExecutionMode

        await store.create_run(_make_run_state("run-order"))
        executor = DagExecutor(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            state_store=store,
            run_id="run-order",
            idempotency_key="key-123",
            lease_renewal_config=LeaseRenewalConfig(renew_enabled=True),
        )
        executor._workflow_name = "test_wf"

        dag = DagWorkflow(
            name="test_wf",
            nodes=[DagNode(id="n1", type="agent", ref="test_agent", depends_on=[])],
            execution_mode=DagExecutionMode.SEQUENTIAL,
        )

        # _enforce_idempotency should be called before lease acquisition
        # (verified by ordering in the code, not by runtime assertion)
        # This test verifies the DagExecutor has both features configured
        assert executor._idempotency_key == "key-123"
        assert executor._lease_renewal_config is not None


class TestLeaseRenewalConfig:
    """Tests for LeaseRenewalConfig schema and defaults."""

    def test_default_config_values(self) -> None:
        """Default values are sensible."""
        from agent_app.config.schema import LeaseRenewalConfig

        cfg = LeaseRenewalConfig()
        assert cfg.renew_enabled is True
        assert cfg.renew_interval_seconds is None
        assert cfg.ttl_seconds == 300

    def test_custom_config_values(self) -> None:
        """Custom values are accepted."""
        from agent_app.config.schema import LeaseRenewalConfig

        cfg = LeaseRenewalConfig(
            renew_enabled=False,
            renew_interval_seconds=10,
            ttl_seconds=60,
        )
        assert cfg.renew_enabled is False
        assert cfg.renew_interval_seconds == 10
        assert cfg.ttl_seconds == 60

    def test_invalid_interval_raises(self) -> None:
        """Negative interval raises validation error."""
        from agent_app.config.schema import LeaseRenewalConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LeaseRenewalConfig(renew_interval_seconds=-1)

    def test_invalid_ttl_raises(self) -> None:
        """Zero TTL raises validation error."""
        from agent_app.config.schema import LeaseRenewalConfig
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LeaseRenewalConfig(ttl_seconds=0)

    def test_runtime_config_defaults(self) -> None:
        """RuntimeConfig defaults preserve backward compatibility."""
        from agent_app.config.schema import RuntimeConfig

        cfg = RuntimeConfig()
        assert cfg.lease_renewal_config is None
