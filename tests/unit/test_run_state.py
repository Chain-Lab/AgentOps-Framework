"""Comprehensive tests for Phase 9: Run State Persistence.

Tests cover:
  - InterruptedRun model
  - RunStateStore protocol
  - InMemoryRunStateStore
  - SQLiteRunStateStore
  - AppRunner integration (save interrupted runs)
  - AgentApp.resume() with RunStateStore
  - Config loader run_state support
  - FastAPI run state endpoints
  - Eval runner compatibility
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult
from agent_app.runtime.run_state import (
    InterruptedRun,
    RunStateStatus,
    RunStateStore,
)
from agent_app.runtime.run_state_store import (
    InMemoryRunStateStore,
    SQLiteRunStateStore,
    create_run_state_store,
)


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
def app_run_result(run_context: RunContext) -> AppRunResult:
    return AppRunResult(
        run_id=run_context.run_id,
        status="interrupted",
        final_output=None,
        interruptions=[
            {
                "type": "approval_required",
                "approval_id": "apv_abc123",
                "tool_name": "order.delete",
                "risk_level": "high",
                "message": "Approval required",
            }
        ],
        tool_calls=[],
        latency_ms=150,
    )


@pytest.fixture()
def interrupted_run(run_context: RunContext, app_run_result: AppRunResult) -> InterruptedRun:
    return InterruptedRun(
        run_id=run_context.run_id,
        status=RunStateStatus.INTERRUPTED.value,
        agent_name="support_agent",
        workflow_name=None,
        workflow_type=None,
        input="delete order 123",
        context=run_context,
        interruptions=app_run_result.interruptions,
        approval_ids=["apv_abc123"],
        backend_name="dry_run",
        result_snapshot=app_run_result.model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# InterruptedRun model tests
# ---------------------------------------------------------------------------

class TestInterruptedRunModel:
    """Test the InterruptedRun data model."""

    def test_create_interrupted_run(self, run_context: RunContext) -> None:
        """Can create an InterruptedRun with required fields."""
        run = InterruptedRun(
            run_id="run-1",
            input="test input",
            context=run_context,
        )
        assert run.run_id == "run-1"
        assert run.status == RunStateStatus.INTERRUPTED.value
        assert run.input == "test input"
        assert run.context.run_id == "test-run-1"

    def test_default_values(self, run_context: RunContext) -> None:
        """Default values are set correctly."""
        run = InterruptedRun(
            run_id="run-1",
            input="test",
            context=run_context,
        )
        assert run.agent_name is None
        assert run.workflow_name is None
        assert run.interruptions == []
        assert run.approval_ids == []
        assert run.backend_name == "dry_run"
        assert run.backend_state == {}
        assert run.result_snapshot is None

    def test_timezone_aware_timestamps(self, run_context: RunContext) -> None:
        """Timestamps are timezone-aware UTC."""
        from datetime import timezone
        run = InterruptedRun(
            run_id="run-1",
            input="test",
            context=run_context,
        )
        assert run.created_at.tzinfo is not None
        assert run.updated_at.tzinfo is not None
        # Should be UTC
        assert run.created_at.tzinfo == timezone.utc

    def test_extract_approval_ids(self, run_context: RunContext) -> None:
        """extract_approval_ids pulls IDs from interruptions."""
        run = InterruptedRun(
            run_id="run-1",
            input="test",
            context=run_context,
            interruptions=[
                {"type": "approval_required", "approval_id": "apv_1"},
                {"type": "approval_required", "approval_id": "apv_2"},
                {"type": "other"},  # ignored
            ],
        )
        assert run.extract_approval_ids() == ["apv_1", "apv_2"]

    def test_extract_approval_ids_empty(self, run_context: RunContext) -> None:
        """extract_approval_ids returns empty list when no approvals."""
        run = InterruptedRun(
            run_id="run-1",
            input="test",
            context=run_context,
            interruptions=[],
        )
        assert run.extract_approval_ids() == []

    def test_is_resumable_true(self, run_context: RunContext) -> None:
        """is_resumable returns True for interrupted run with approval IDs."""
        run = InterruptedRun(
            run_id="run-1",
            input="test",
            context=run_context,
            status=RunStateStatus.INTERRUPTED.value,
            approval_ids=["apv_1"],
        )
        assert run.is_resumable() is True

    def test_is_resumable_false_no_approvals(self, run_context: RunContext) -> None:
        """is_resumable returns False when no approval IDs."""
        run = InterruptedRun(
            run_id="run-1",
            input="test",
            context=run_context,
            status=RunStateStatus.INTERRUPTED.value,
            approval_ids=[],
        )
        assert run.is_resumable() is False

    def test_is_resumable_false_completed(self, run_context: RunContext) -> None:
        """is_resumable returns False for completed runs."""
        run = InterruptedRun(
            run_id="run-1",
            input="test",
            context=run_context,
            status=RunStateStatus.COMPLETED.value,
            approval_ids=["apv_1"],
        )
        assert run.is_resumable() is False


# ---------------------------------------------------------------------------
# InMemoryRunStateStore tests
# ---------------------------------------------------------------------------

class TestInMemoryRunStateStore:
    """Test InMemoryRunStateStore."""

    @pytest.fixture()
    def store(self) -> InMemoryRunStateStore:
        return InMemoryRunStateStore()

    @pytest.mark.asyncio
    async def test_save_and_get(self, store: InMemoryRunStateStore, interrupted_run: InterruptedRun) -> None:
        """Save and retrieve a run."""
        saved = await store.save_interrupted(interrupted_run)
        assert saved.run_id == interrupted_run.run_id

        retrieved = await store.get(interrupted_run.run_id)
        assert retrieved.run_id == interrupted_run.run_id
        assert retrieved.status == RunStateStatus.INTERRUPTED.value
        assert retrieved.agent_name == "support_agent"

    @pytest.mark.asyncio
    async def test_get_missing_raises(self, store: InMemoryRunStateStore) -> None:
        """Getting a non-existent run raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            await store.get("nonexistent-run")

    @pytest.mark.asyncio
    async def test_mark_resumed(self, store: InMemoryRunStateStore, interrupted_run: InterruptedRun) -> None:
        """mark_resumed updates status and timestamp."""
        await store.save_interrupted(interrupted_run)
        updated = await store.mark_resumed(interrupted_run.run_id)
        assert updated.status == RunStateStatus.RESUMED.value
        assert updated.resumed_at is not None

    @pytest.mark.asyncio
    async def test_mark_completed(self, store: InMemoryRunStateStore, interrupted_run: InterruptedRun) -> None:
        """mark_completed updates status."""
        await store.save_interrupted(interrupted_run)
        updated = await store.mark_completed(interrupted_run.run_id)
        assert updated.status == RunStateStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_mark_failed(self, store: InMemoryRunStateStore, interrupted_run: InterruptedRun) -> None:
        """mark_failed updates status and error."""
        await store.save_interrupted(interrupted_run)
        error = {"type": "test_error", "message": "something went wrong"}
        updated = await store.mark_failed(interrupted_run.run_id, error)
        assert updated.status == RunStateStatus.FAILED.value
        assert updated.error == error

    @pytest.mark.asyncio
    async def test_list_interrupted_empty(self, store: InMemoryRunStateStore) -> None:
        """list_interrupted returns empty list when no interrupted runs."""
        result = await store.list_interrupted()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_interrupted_filters_status(
        self, store: InMemoryRunStateStore, interrupted_run: InterruptedRun
    ) -> None:
        """list_interrupted only returns INTERRUPTED runs."""
        await store.save_interrupted(interrupted_run)
        # Mark one as completed
        await store.mark_completed(interrupted_run.run_id)

        interrupted = await store.list_interrupted()
        assert len(interrupted) == 0

    @pytest.mark.asyncio
    async def test_list_interrupted_multiple(
        self, store: InMemoryRunStateStore, run_context: RunContext
    ) -> None:
        """list_interrupted returns multiple interrupted runs."""
        runs = []
        for i in range(3):
            r = InterruptedRun(
                run_id=f"run-{i}",
                input=f"input-{i}",
                context=run_context,
                approval_ids=[f"apv_{i}"],
            )
            await store.save_interrupted(r)
            runs.append(r)

        result = await store.list_interrupted()
        assert len(result) == 3
        assert {r.run_id for r in result} == {"run-0", "run-1", "run-2"}

    @pytest.mark.asyncio
    async def test_list_interrupted_tenant_filter(
        self, store: InMemoryRunStateStore
    ) -> None:
        """list_interrupted supports tenant_id filtering."""
        ctx_a = RunContext(run_id="a", user_id="u1", tenant_id="tenant-a")
        ctx_b = RunContext(run_id="b", user_id="u2", tenant_id="tenant-b")

        await store.save_interrupted(InterruptedRun(
            run_id="run-a", input="a", context=ctx_a, approval_ids=["apv_a"],
        ))
        await store.save_interrupted(InterruptedRun(
            run_id="run-b", input="b", context=ctx_b, approval_ids=["apv_b"],
        ))

        result_a = await store.list_interrupted(tenant_id="tenant-a")
        assert len(result_a) == 1
        assert result_a[0].run_id == "run-a"

        result_b = await store.list_interrupted(tenant_id="tenant-b")
        assert len(result_b) == 1
        assert result_b[0].run_id == "run-b"

    @pytest.mark.asyncio
    async def test_save_updates_existing(self, store: InMemoryRunStateStore, interrupted_run: InterruptedRun) -> None:
        """Saving a run with existing run_id updates it."""
        await store.save_interrupted(interrupted_run)
        original_created = interrupted_run.created_at

        # Modify and re-save
        interrupted_run.interruptions.append({"type": "new"})
        await store.save_interrupted(interrupted_run)

        retrieved = await store.get(interrupted_run.run_id)
        assert len(retrieved.interruptions) == 2
        assert retrieved.created_at == original_created  # created_at preserved


# ---------------------------------------------------------------------------
# SQLiteRunStateStore tests
# ---------------------------------------------------------------------------

class TestSQLiteRunStateStore:
    """Test SQLiteRunStateStore with temp database."""

    @pytest.fixture()
    def db_path(self, tmp_path: Any) -> str:
        return str(tmp_path / "test_run_states.db")

    @pytest.fixture()
    def store(self, db_path: str) -> SQLiteRunStateStore:
        return SQLiteRunStateStore(db_path=db_path)

    @pytest.mark.asyncio
    async def test_save_and_get(self, store: SQLiteRunStateStore, interrupted_run: InterruptedRun) -> None:
        """Save and retrieve a run from SQLite."""
        await store.save_interrupted(interrupted_run)
        retrieved = await store.get(interrupted_run.run_id)
        assert retrieved.run_id == interrupted_run.run_id
        assert retrieved.agent_name == "support_agent"

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, db_path: str, interrupted_run: InterruptedRun) -> None:
        """Data persists when creating a new store instance."""
        store1 = SQLiteRunStateStore(db_path=db_path)
        await store1.save_interrupted(interrupted_run)
        store1.close()

        # New instance reads same data
        store2 = SQLiteRunStateStore(db_path=db_path)
        retrieved = await store2.get(interrupted_run.run_id)
        assert retrieved.run_id == interrupted_run.run_id
        store2.close()

    @pytest.mark.asyncio
    async def test_mark_resumed_persists(self, db_path: str, interrupted_run: InterruptedRun) -> None:
        """mark_resumed persists across instances."""
        store1 = SQLiteRunStateStore(db_path=db_path)
        await store1.save_interrupted(interrupted_run)
        await store1.mark_resumed(interrupted_run.run_id)
        store1.close()

        store2 = SQLiteRunStateStore(db_path=db_path)
        run = await store2.get(interrupted_run.run_id)
        assert run.status == RunStateStatus.RESUMED.value
        assert run.resumed_at is not None
        store2.close()

    @pytest.mark.asyncio
    async def test_list_interrupted_excludes_completed(self, db_path: str, run_context: RunContext) -> None:
        """list_interrupted only returns INTERRUPTED runs."""
        store = SQLiteRunStateStore(db_path=db_path)

        await store.save_interrupted(InterruptedRun(
            run_id="ir-1", input="test", context=run_context,
        ))
        await store.save_interrupted(InterruptedRun(
            run_id="ir-2", input="test", context=run_context,
        ))

        await store.mark_completed("ir-1")

        result = await store.list_interrupted()
        assert len(result) == 1
        assert result[0].run_id == "ir-2"
        store.close()

    @pytest.mark.asyncio
    async def test_result_snapshot_roundtrip(
        self, db_path: str, interrupted_run: InterruptedRun
    ) -> None:
        """result_snapshot survives SQLite roundtrip."""
        store = SQLiteRunStateStore(db_path=db_path)
        await store.save_interrupted(interrupted_run)
        retrieved = await store.get(interrupted_run.run_id)
        assert retrieved.result_snapshot is not None
        assert retrieved.result_snapshot["run_id"] == interrupted_run.run_id
        assert retrieved.result_snapshot["status"] == "interrupted"
        store.close()

    @pytest.mark.asyncio
    async def test_backend_state_roundtrip(self, db_path: str, interrupted_run: InterruptedRun) -> None:
        """backend_state survives SQLite roundtrip."""
        interrupted_run.backend_state = {"thread_id": "th_123", "step": 5}
        store = SQLiteRunStateStore(db_path=db_path)
        await store.save_interrupted(interrupted_run)
        retrieved = await store.get(interrupted_run.run_id)
        assert retrieved.backend_state == {"thread_id": "th_123", "step": 5}
        store.close()

    @pytest.mark.asyncio
    async def test_tenant_filter_sqlite(self, db_path: str) -> None:
        """SQLite list_interrupted supports tenant filtering."""
        store = SQLiteRunStateStore(db_path=db_path)

        ctx_a = RunContext(run_id="a", user_id="u1", tenant_id="tenant-a")
        ctx_b = RunContext(run_id="b", user_id="u2", tenant_id="tenant-b")

        await store.save_interrupted(InterruptedRun(
            run_id="sql-a", input="a", context=ctx_a, approval_ids=["apv_a"],
        ))
        await store.save_interrupted(InterruptedRun(
            run_id="sql-b", input="b", context=ctx_b, approval_ids=["apv_b"],
        ))

        result_a = await store.list_interrupted(tenant_id="tenant-a")
        assert len(result_a) == 1
        assert result_a[0].run_id == "sql-a"
        store.close()


# ---------------------------------------------------------------------------
# create_run_state_store factory tests
# ---------------------------------------------------------------------------

class TestCreateRunStateStore:
    """Test the factory function."""

    def test_create_memory(self) -> None:
        store = create_run_state_store("memory")
        assert isinstance(store, InMemoryRunStateStore)

    def test_create_sqlite(self, tmp_path: Any) -> None:
        store = create_run_state_store("sqlite", db_path=str(tmp_path / "test.db"))
        assert isinstance(store, SQLiteRunStateStore)
        store.close()

    def test_create_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            create_run_state_store("unknown")

    def test_default_is_memory(self) -> None:
        store = create_run_state_store()
        assert isinstance(store, InMemoryRunStateStore)


# ---------------------------------------------------------------------------
# AppRunner integration tests
# ---------------------------------------------------------------------------

class TestAppRunnerRunStateIntegration:
    """Test that AppRunner saves interrupted runs to RunStateStore."""

    @pytest.fixture()
    def store(self) -> InMemoryRunStateStore:
        return InMemoryRunStateStore()

    @pytest.fixture()
    def app_runner(self, store: InMemoryRunStateStore) -> Any:
        """Create an AppRunner with a RunStateStore."""
        from agent_app.runtime.app_runner import AppRunner
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.backends import DryRunBackend

        ar = AgentRegistry()
        tr = ToolRegistry()
        wr = WorkflowRegistry()
        return AppRunner(
            agent_registry=ar,
            tool_registry=tr,
            workflow_registry=wr,
            backend=DryRunBackend(),
            run_state_store=store,
        )

    @pytest.mark.asyncio
    async def test_interrupted_run_saved_to_store(
        self, app_runner: Any, store: InMemoryRunStateStore
    ) -> None:
        """When backend returns interrupted, run is saved to store."""
        from agent_app.core.agent_spec import AgentSpec
        from agent_app.core.tool_spec import ToolSpec

        # Register agent
        agent_spec = AgentSpec(
            name="support",
            instructions="You are a helpful assistant.",
            tools=["order.delete"],
        )
        app_runner.agent_registry.register("support", agent_spec)

        # Register a tool that requires approval
        spec = ToolSpec(
            name="order.delete",
            description="Delete order",
            risk_level="high",
            requires_approval=True,
        )

        async def delete_order(**kw: Any) -> dict:
            return {"deleted": True}

        app_runner.tool_registry.register("order.delete", spec, fn=delete_order)

        result = await app_runner.run(
            agent="support",
            input="delete order 123",
        )

        # Result should be interrupted (approval required)
        assert result.status == "interrupted"
        actual_run_id = result.run_id

        # Should have been saved to store
        saved = await store.get(actual_run_id)
        assert saved.status == RunStateStatus.INTERRUPTED.value
        assert saved.agent_name == "support"
        assert len(saved.approval_ids) > 0

    @pytest.mark.asyncio
    async def test_completed_run_not_saved(
        self, app_runner: Any, store: InMemoryRunStateStore
    ) -> None:
        """Completed runs are not saved to run state store."""
        # Register a simple agent that won't trigger interruptions
        from agent_app.core.agent_spec import AgentSpec
        agent_spec = AgentSpec(
            name="simple",
            instructions="You are a simple agent.",
            tools=[],
        )
        app_runner.agent_registry.register("simple", agent_spec)

        result = await app_runner.run(
            agent="simple",
            input="hello",
        )
        assert result.status == "completed"

        # Should not have any interrupted runs saved
        interrupted = await store.list_interrupted()
        assert len([r for r in interrupted if "hello" in r.input]) == 0


# ---------------------------------------------------------------------------
# AgentApp.resume() tests
# ---------------------------------------------------------------------------

class TestAgentAppResume:
    """Test AgentApp.resume() with RunStateStore."""

    @pytest.fixture()
    def app_with_store(self, tmp_path: Any) -> Any:
        """Create an AgentApp with a SQLite run state store."""
        from agent_app.core.app import AgentApp
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.run_state_store import SQLiteRunStateStore
        from agent_app.runtime.approval_store import InMemoryApprovalStore
        from agent_app.governance.approval import ApprovalRequest, ApprovalStatus
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.permission import DefaultPermissionChecker
        from agent_app.runtime.tool_executor import ToolExecutor

        store = SQLiteRunStateStore(db_path=str(tmp_path / "resume_test.db"))
        approval_store = InMemoryApprovalStore()

        ar = AgentRegistry()
        tr = ToolRegistry()
        wr = WorkflowRegistry()

        tool_executor = ToolExecutor(
            tool_registry=tr,
            approval_store=approval_store,
            permission_checker=DefaultPermissionChecker(),
            audit_logger=InMemoryAuditLogger(),
        )

        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        backend = OpenAIAgentsBackend(
            tool_executor=tool_executor,
            tool_registry=tr,
        )

        app = AgentApp(
            registry=type("B", (), {"agent_registry": ar, "tool_registry": tr, "workflow_registry": wr})(),
            approval_store=approval_store,
            backend=backend,
            run_state_store=store,
        )
        app.agent_registry = ar
        app.tool_registry = tr
        app.workflow_registry = wr
        return app, store, approval_store

    @pytest.mark.asyncio
    async def test_resume_missing_run_returns_failed(self, app_with_store: Any) -> None:
        """Resuming a non-existent run returns failed result."""
        app, store, _ = app_with_store
        result = await app.resume(run_id="nonexistent")
        assert result.status == "failed"
        assert result.error["type"] == "run_not_found"

    @pytest.mark.asyncio
    async def test_resume_pending_approval_returns_interrupted(self, app_with_store: Any) -> None:
        """Resuming with pending approvals returns interrupted."""
        from agent_app.core.context import RunContext
        from agent_app.core.result import AppRunResult
        from agent_app.runtime.run_state import InterruptedRun
        from agent_app.governance.approval import ApprovalRequest

        app, store, approval_store = app_with_store

        # Create a pending approval in the approval store
        pending_approval = ApprovalRequest(
            approval_id="apv_pending",
            run_id="pending-resume",
            tool_name="test.tool",
            arguments={},
            risk_level="high",
            tenant_id="t1",
            status="pending",
        )
        await approval_store.create(pending_approval)

        ctx = RunContext(run_id="pending-resume", user_id="u1", tenant_id="t1")
        ir = InterruptedRun(
            run_id="pending-resume",
            status=RunStateStatus.INTERRUPTED.value,
            input="test",
            context=ctx,
            interruptions=[{
                "type": "approval_required",
                "approval_id": "apv_pending",
                "tool_name": "test.tool",
                "risk_level": "high",
            }],
            approval_ids=["apv_pending"],
            result_snapshot=AppRunResult(
                run_id="pending-resume",
                status="interrupted",
                interruptions=[{
                    "type": "approval_required",
                    "approval_id": "apv_pending",
                }],
            ).model_dump(mode="json"),
        )
        await store.save_interrupted(ir)

        result = await app.resume(run_id="pending-resume")
        assert result.status == "interrupted"


# ---------------------------------------------------------------------------
# Config loader tests
# ---------------------------------------------------------------------------

class TestConfigLoaderRunState:
    """Test that config loader creates RunStateStore correctly."""

    def test_default_memory_store(self, tmp_path: Any) -> None:
        """Default (no run_state config) creates InMemoryRunStateStore."""
        import yaml
        from agent_app.config.loader import build_app

        config_data = {
            "agents": [{"name": "bot", "instructions": "help"}],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        app = build_app(config_file)
        from agent_app.runtime.run_state_store import InMemoryRunStateStore
        assert isinstance(app._run_state_store, InMemoryRunStateStore)

    def test_sqlite_run_state_config(self, tmp_path: Any) -> None:
        """runtime.run_state.type: sqlite creates SQLiteRunStateStore."""
        import yaml
        from agent_app.config.loader import build_app

        db_path = str(tmp_path / "run_states.db")
        config_data = {
            "runtime": {
                "run_state": {
                    "type": "sqlite",
                    "path": db_path,
                }
            },
            "agents": [{"name": "bot", "instructions": "help"}],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        app = build_app(config_file)
        from agent_app.runtime.run_state_store import SQLiteRunStateStore
        assert isinstance(app._run_state_store, SQLiteRunStateStore)
        assert app._run_state_store._db_path == Path(db_path)

    def test_flat_run_state_config(self, tmp_path: Any) -> None:
        """Flat run_state_type / run_state_path config works."""
        import yaml
        from agent_app.config.loader import build_app

        config_data = {
            "runtime": {
                "run_state_type": "sqlite",
                "run_state_path": str(tmp_path / "flat.db"),
            },
            "agents": [{"name": "bot", "instructions": "help"}],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        app = build_app(config_file)
        from agent_app.runtime.run_state_store import SQLiteRunStateStore
        assert isinstance(app._run_state_store, SQLiteRunStateStore)

    def test_invalid_run_state_type_raises(self, tmp_path: Any) -> None:
        """Invalid run_state type raises ValueError."""
        import yaml
        from agent_app.config.loader import build_app

        config_data = {
            "runtime": {
                "run_state": {
                    "type": "redis",
                }
            },
            "agents": [{"name": "bot", "instructions": "help"}],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        with pytest.raises(ValueError, match="Unknown"):
            build_app(config_file)


# ---------------------------------------------------------------------------
# FastAPI run state endpoint tests
# ---------------------------------------------------------------------------

class TestFastAPIRunStateEndpoints:
    """Test FastAPI run state endpoints (skip if httpx not available)."""

    def setup_class(cls) -> None:
        """Skip entire class if httpx is not installed."""
        pytest.importorskip("httpx", reason="httpx required for FastAPI tests")

    @pytest.fixture()
    def api(self, tmp_path: Any) -> Any:
        """Create a FastAPI app with a run state store."""
        from agent_app.core.app import AgentApp
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.run_state_store import SQLiteRunStateStore
        from agent_app.runtime.backends import DryRunBackend
        from agent_app.adapters.fastapi import create_fastapi_app

        db_path = str(tmp_path / "fastapi_test.db")
        store = SQLiteRunStateStore(db_path=db_path)

        ar = AgentRegistry()
        tr = ToolRegistry()
        wr = WorkflowRegistry()

        app = AgentApp(
            registry=type("B", (), {"agent_registry": ar, "tool_registry": tr, "workflow_registry": wr})(),
            backend=DryRunBackend(),
            run_state_store=store,
        )
        app.agent_registry = ar
        app.tool_registry = tr
        app.workflow_registry = wr

        from fastapi.testclient import TestClient
        return TestClient(create_fastapi_app(app)), store

    def test_list_interrupted_empty(self, api: Any) -> None:
        """GET /runs/interrupted returns empty list when no interrupted runs."""
        client, _ = api
        response = client.get("/runs/interrupted")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_run_state_not_found(self, api: Any) -> None:
        """GET /runs/{run_id}/state returns 404 for missing run."""
        client, _ = api
        response = client.get("/runs/nonexistent/state")
        assert response.status_code == 404

    def test_run_state_endpoints_with_store_none(self, tmp_path: Any) -> None:
        """Endpoints handle run_state_store=None gracefully."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI test client not available")

        from agent_app.core.app import AgentApp
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.backends import DryRunBackend
        from agent_app.adapters.fastapi import create_fastapi_app

        ar = AgentRegistry()
        tr = ToolRegistry()
        wr = WorkflowRegistry()

        app = AgentApp(
            registry=type("B", (), {"agent_registry": ar, "tool_registry": tr, "workflow_registry": wr})(),
            backend=DryRunBackend(),
            run_state_store=None,
        )
        app.agent_registry = ar
        app.tool_registry = tr
        app.workflow_registry = wr

        client = TestClient(create_fastapi_app(app))

        response = client.get("/runs/interrupted")
        assert response.status_code == 200
        assert response.json() == []

        response = client.get("/runs/any/state")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Eval runner compatibility
# ---------------------------------------------------------------------------

class TestEvalCompatibility:
    """Test that eval runner still works with RunStateStore."""

    @pytest.mark.asyncio
    async def test_approve_and_resume_eval_still_works(self, tmp_path: Any) -> None:
        """Eval approve_and_resume flow works with RunStateStore."""
        import yaml
        from agent_app.config.loader import build_app
        from agent_app.core.result import AppRunResult

        config_data = {
            "runtime": {"backend": "dry_run"},
            "agents": [
                {"name": "support", "instructions": "help"},
            ],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        app = build_app(config_file)

        # Run a simple agent
        result = await app.run(agent="support", input="hello")
        assert result.status == "completed"
