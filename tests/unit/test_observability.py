"""Tests for Phase 12 Step 1: observability events, collectors, config."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agent_app.config.loader import load_config
from agent_app.config.loader import _bundle
from agent_app.core.context import RunContext
from agent_app.core.result import AppRunResult
from agent_app.core.tool_spec import ToolSpec
from agent_app.evals.assertions import run_assertions
from agent_app.evals.schema import EvalCase, EvalExpect
from agent_app.observability.events import RunEvent, RunEventType, _uid
from agent_app.observability.collector import (
    InMemoryTraceCollector,
    NoOpTraceCollector,
    TraceCollector,
)
from agent_app.observability.exporters import JSONLTraceCollector
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry


# ---------------------------------------------------------------------------
# RunEvent model
# ---------------------------------------------------------------------------

class TestRunEvent:
    def test_create_event(self) -> None:
        """RunEvent can be created with required fields."""
        event = RunEvent(
            event_type=RunEventType.RUN_STARTED,
            trace_id="trace-001",
        )
        assert event.event_type == RunEventType.RUN_STARTED
        assert event.trace_id == "trace-001"
        assert event.event_id  # auto-generated

    def test_event_id_unique(self) -> None:
        """Each event gets a unique event_id."""
        e1 = RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1")
        e2 = RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1")
        assert e1.event_id != e2.event_id

    def test_timestamp_timezone_aware(self) -> None:
        """Default timestamp is timezone-aware (UTC)."""
        event = RunEvent(
            event_type=RunEventType.RUN_STARTED,
            trace_id="t1",
        )
        assert event.timestamp.tzinfo is not None

    def test_custom_timestamp(self) -> None:
        """Explicit timezone-aware timestamp is accepted."""
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        event = RunEvent(
            event_type=RunEventType.RUN_STARTED,
            trace_id="t1",
            timestamp=ts,
        )
        assert event.timestamp == ts

    def test_json_serialization(self) -> None:
        """RunEvent serializes to JSON with ISO timestamp."""
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        event = RunEvent(
            event_type=RunEventType.TOOL_STARTED,
            trace_id="t1",
            run_id="r1",
            timestamp=ts,
            agent_name="support",
            tool_name="order.query",
            data={"key": "value"},
        )
        raw = event.model_dump(mode="json")
        assert raw["event_type"] == "tool.started"
        assert raw["trace_id"] == "t1"
        assert raw["run_id"] == "r1"
        assert raw["agent_name"] == "support"
        assert raw["tool_name"] == "order.query"
        assert raw["data"] == {"key": "value"}
        # timestamp should be ISO format string
        assert isinstance(raw["timestamp"], str)

    def test_custom_event_type(self) -> None:
        """Custom (non-enum) event types are accepted."""
        event = RunEvent(
            event_type="my.custom.event",
            trace_id="t1",
        )
        assert event.event_type == "my.custom.event"

    def test_optional_fields_default_none(self) -> None:
        """Optional fields default to None."""
        event = RunEvent(
            event_type=RunEventType.RUN_STARTED,
            trace_id="t1",
        )
        assert event.run_id is None
        assert event.user_id is None
        assert event.tenant_id is None
        assert event.agent_name is None
        assert event.tool_name is None
        assert event.duration_ms is None
        assert event.error is None
        assert event.data == {}

    def test_error_field(self) -> None:
        """Error field stores structured error info."""
        event = RunEvent(
            event_type=RunEventType.RUN_FAILED,
            trace_id="t1",
            error={"type": "ValueError", "message": "something went wrong"},
        )
        assert event.error["type"] == "ValueError"
        assert event.error["message"] == "something went wrong"


# ---------------------------------------------------------------------------
# NoOpTraceCollector
# ---------------------------------------------------------------------------

class TestNoOpTraceCollector:
    @pytest.fixture
    def collector(self):
        return NoOpTraceCollector()

    @pytest.mark.asyncio
    async def test_record_discards(self, collector):
        """record() accepts events but discards them."""
        from agent_app.observability.events import RunEvent
        event = RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1")
        await collector.record(event)  # no error

    @pytest.mark.asyncio
    async def test_get_events_empty(self, collector):
        """get_events always returns empty list."""
        result = await collector.get_events("t1")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_traces_empty(self, collector):
        """list_traces always returns empty list."""
        result = await collector.list_traces()
        assert result == []


# ---------------------------------------------------------------------------
# InMemoryTraceCollector
# ---------------------------------------------------------------------------

class TestInMemoryTraceCollector:
    @pytest.fixture
    def collector(self):
        return InMemoryTraceCollector()

    @pytest.mark.asyncio
    async def test_record_and_get(self, collector):
        """record stores events, get_events retrieves them."""
        from agent_app.observability.events import RunEvent
        e1 = RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1")
        e2 = RunEvent(event_type=RunEventType.RUN_COMPLETED, trace_id="t1")
        await collector.record(e1)
        await collector.record(e2)
        events = await collector.get_events("t1")
        assert len(events) == 2
        assert events[0].event_type == RunEventType.RUN_STARTED
        assert events[1].event_type == RunEventType.RUN_COMPLETED

    @pytest.mark.asyncio
    async def test_get_events_ordered_by_timestamp(self, collector):
        """Events are returned ordered by timestamp ascending."""
        from agent_app.observability.events import RunEvent
        import time
        e_early = RunEvent(
            event_type=RunEventType.RUN_STARTED,
            trace_id="t1",
            timestamp=datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        e_late = RunEvent(
            event_type=RunEventType.RUN_COMPLETED,
            trace_id="t1",
            timestamp=datetime(2024, 1, 1, 10, 1, 0, tzinfo=timezone.utc),
        )
        await collector.record(e_late)  # insert out of order
        await collector.record(e_early)
        events = await collector.get_events("t1")
        assert events[0].event_type == RunEventType.RUN_STARTED
        assert events[1].event_type == RunEventType.RUN_COMPLETED

    @pytest.mark.asyncio
    async def test_get_events_unknown_trace(self, collector):
        """get_events for unknown trace returns empty list."""
        result = await collector.get_events("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_traces(self, collector):
        """list_traces returns all trace IDs."""
        from agent_app.observability.events import RunEvent
        await collector.record(RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1"))
        await collector.record(RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t2"))
        traces = await collector.list_traces()
        assert set(traces) == {"t1", "t2"}

    @pytest.mark.asyncio
    async def test_list_traces_limit(self, collector):
        """list_traces respects limit."""
        from agent_app.observability.events import RunEvent
        for i in range(10):
            await collector.record(RunEvent(
                event_type=RunEventType.RUN_STARTED,
                trace_id=f"trace-{i:03d}",
            ))
        traces = await collector.list_traces(limit=5)
        assert len(traces) == 5

    @pytest.mark.asyncio
    async def test_list_traces_tenant_filter(self, collector):
        """list_traces filters by tenant_id."""
        from agent_app.observability.events import RunEvent
        await collector.record(RunEvent(
            event_type=RunEventType.RUN_STARTED, trace_id="t1", tenant_id="tenant-a"
        ))
        await collector.record(RunEvent(
            event_type=RunEventType.RUN_STARTED, trace_id="t2", tenant_id="tenant-b"
        ))
        traces_a = await collector.list_traces(tenant_id="tenant-a")
        assert traces_a == ["t1"]
        traces_b = await collector.list_traces(tenant_id="tenant-b")
        assert traces_b == ["t2"]

    @pytest.mark.asyncio
    async def test_list_traces_run_id_filter(self, collector):
        """list_traces filters by run_id."""
        from agent_app.observability.events import RunEvent
        await collector.record(RunEvent(
            event_type=RunEventType.RUN_STARTED, trace_id="t1", run_id="run-1"
        ))
        await collector.record(RunEvent(
            event_type=RunEventType.RUN_STARTED, trace_id="t2", run_id="run-2"
        ))
        traces = await collector.list_traces(run_id="run-1")
        assert traces == ["t1"]

    @pytest.mark.asyncio
    async def test_isolated_traces(self, collector):
        """Events for different traces are stored separately."""
        from agent_app.observability.events import RunEvent
        await collector.record(RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1"))
        await collector.record(RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t2"))
        assert len(await collector.get_events("t1")) == 1
        assert len(await collector.get_events("t2")) == 1


# ---------------------------------------------------------------------------
# JSONLTraceCollector
# ---------------------------------------------------------------------------

class TestJSONLTraceCollector:
    @pytest.mark.asyncio
    async def test_writes_jsonl(self, tmp_path):
        """Events are written as JSON lines."""
        path = tmp_path / "traces.jsonl"
        collector = JSONLTraceCollector(path)
        from agent_app.observability.events import RunEvent
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        event = RunEvent(
            event_type=RunEventType.RUN_STARTED,
            trace_id="t1",
            run_id="r1",
            timestamp=ts,
        )
        await collector.record(event)
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        raw = json.loads(lines[0])
        assert raw["trace_id"] == "t1"
        assert raw["run_id"] == "r1"

    @pytest.mark.asyncio
    async def test_creates_parent_dirs(self, tmp_path):
        """Parent directories are created automatically."""
        path = tmp_path / "deep" / "nested" / "traces.jsonl"
        collector = JSONLTraceCollector(path)
        from agent_app.observability.events import RunEvent
        event = RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1")
        await collector.record(event)
        assert path.exists()

    @pytest.mark.asyncio
    async def test_get_events_reads_jsonl(self, tmp_path):
        """get_events reads and deserializes from JSONL file."""
        path = tmp_path / "traces.jsonl"
        collector = JSONLTraceCollector(path)
        from agent_app.observability.events import RunEvent
        ts = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        e1 = RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1", timestamp=ts)
        await collector.record(e1)
        # Re-open to read back
        collector2 = JSONLTraceCollector(path)
        events = await collector2.get_events("t1")
        assert len(events) == 1
        assert events[0].event_type == RunEventType.RUN_STARTED

    @pytest.mark.asyncio
    async def test_list_traces_from_jsonl(self, tmp_path):
        """list_traces reads trace IDs from JSONL file."""
        path = tmp_path / "traces.jsonl"
        collector = JSONLTraceCollector(path)
        from agent_app.observability.events import RunEvent
        await collector.record(RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1"))
        await collector.record(RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t2"))
        collector2 = JSONLTraceCollector(path)
        traces = await collector2.list_traces()
        assert set(traces) == {"t1", "t2"}

    @pytest.mark.asyncio
    async def test_multiple_events_same_trace(self, tmp_path):
        """Multiple events for the same trace are stored."""
        path = tmp_path / "traces.jsonl"
        collector = JSONLTraceCollector(path)
        from agent_app.observability.events import RunEvent
        await collector.record(RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1"))
        await collector.record(RunEvent(event_type=RunEventType.RUN_COMPLETED, trace_id="t1"))
        collector2 = JSONLTraceCollector(path)
        events = await collector2.get_events("t1")
        assert len(events) == 2


# ---------------------------------------------------------------------------
# Config loader integration
# ---------------------------------------------------------------------------

class TestObservabilityConfig:
    def test_default_no_observability(self, tmp_path):
        """Config without observability section loads successfully."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(
            "app:\n  name: test\nmodels:\n  default: gpt-4o\n", encoding="utf-8"
        )
        config = load_config(config_file)
        assert config.observability is None

    def test_observability_defaults(self, tmp_path):
        """Observability config defaults to memory tracing."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(
            "app:\n  name: test\nmodels:\n  default: gpt-4o\n"
            "observability:\n  tracing:\n    type: memory\n",
            encoding="utf-8",
        )
        config = load_config(config_file)
        assert config.observability is not None
        assert config.observability.tracing.type == "memory"

    def test_observability_jsonl_config(self, tmp_path):
        """JSONL tracing config is parsed correctly."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(
            "app:\n  name: test\nmodels:\n  default: gpt-4o\n"
            "observability:\n  tracing:\n    type: jsonl\n    path: /tmp/traces.jsonl\n",
            encoding="utf-8",
        )
        config = load_config(config_file)
        assert config.observability.tracing.type == "jsonl"
        assert config.observability.tracing.path == "/tmp/traces.jsonl"

    def test_observability_noop_config(self, tmp_path):
        """Noop tracing config disables collection."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(
            "app:\n  name: test\nmodels:\n  default: gpt-4o\n"
            "observability:\n  tracing:\n    type: noop\n",
            encoding="utf-8",
        )
        config = load_config(config_file)
        assert config.observability.tracing.type == "noop"

    def test_observability_include_flags(self, tmp_path):
        """include_inputs / include_outputs flags are parsed."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(
            "app:\n  name: test\nmodels:\n  default: gpt-4o\n"
            "observability:\n  tracing:\n    type: memory\n"
            "    include_inputs: true\n    include_outputs: true\n",
            encoding="utf-8",
        )
        config = load_config(config_file)
        assert config.observability.tracing.include_inputs is True
        assert config.observability.tracing.include_outputs is True


# ---------------------------------------------------------------------------
# Phase 12 Step 2: ToolExecutor + WorkflowExecutor + AgentApp tracing
# ---------------------------------------------------------------------------

class TestToolExecutorTracing:
    """ToolExecutor emits trace events for each execution path."""

    @pytest.fixture
    def collector(self):
        return InMemoryTraceCollector()

    @pytest.fixture
    def executor(self, collector, tool_registry):
        from agent_app.runtime.tool_executor import ToolExecutor
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.permission import DefaultPermissionChecker
        from agent_app.runtime.approval_store import InMemoryApprovalStore
        return ToolExecutor(
            tool_registry=tool_registry,
            approval_store=InMemoryApprovalStore(),
            permission_checker=DefaultPermissionChecker(),
            audit_logger=InMemoryAuditLogger(),
            trace_collector=collector,
        )

    @pytest.mark.asyncio
    async def test_low_risk_emits_started_and_completed(self, collector, executor, tool_registry):
        """Low risk tool emits tool.started and tool.completed."""
        spec = ToolSpec(name="order.query", description="Query orders", risk_level="low")
        async def fn(order_id: str):
            return {"order": order_id}
        tool_registry.register("order.query", spec, fn=fn)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", trace_id="trace-1")
        result = await executor.execute("order.query", {"order_id": "123"}, ctx)
        assert result.status == "completed"
        events = await collector.get_events("trace-1")
        types = [e.event_type for e in events]
        assert RunEventType.TOOL_STARTED in types
        assert RunEventType.TOOL_COMPLETED in types

    @pytest.mark.asyncio
    async def test_permission_denied_emits_events(self, collector, executor, tool_registry):
        """Permission denied emits tool.started and tool.permission_denied."""
        spec = ToolSpec(
            name="refund.request",
            description="Refund",
            risk_level="high",
            permissions=["refund:create"],
        )
        async def fn(**kwargs):
            return {}
        tool_registry.register("refund.request", spec, fn=fn)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", trace_id="trace-2")
        result = await executor.execute("refund.request", {"order_id": "123"}, ctx)
        assert result.status == "failed"
        events = await collector.get_events("trace-2")
        types = [e.event_type for e in events]
        assert RunEventType.TOOL_STARTED in types
        assert RunEventType.TOOL_PERMISSION_DENIED in types

    @pytest.mark.asyncio
    async def test_approval_required_emits_events(self, collector, executor, tool_registry):
        """Approval required emits tool.started, tool.approval_required, approval.created."""
        spec = ToolSpec(
            name="refund.request",
            description="Refund",
            risk_level="high",
            requires_approval=True,
        )
        async def fn(**kwargs):
            return {}
        tool_registry.register("refund.request", spec, fn=fn)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", trace_id="trace-3")
        result = await executor.execute("refund.request", {"order_id": "123"}, ctx)
        assert result.status == "interrupted"
        events = await collector.get_events("trace-3")
        types = [e.event_type for e in events]
        assert RunEventType.TOOL_STARTED in types
        assert RunEventType.TOOL_APPROVAL_REQUIRED in types
        assert RunEventType.APPROVAL_CREATED in types

    @pytest.mark.asyncio
    async def test_tool_not_found_emits_failed(self, collector, executor):
        """Tool not found emits tool.failed."""
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", trace_id="trace-4")
        result = await executor.execute("nonexistent.tool", {}, ctx)
        assert result.status == "failed"
        events = await collector.get_events("trace-4")
        types = [e.event_type for e in events]
        assert RunEventType.TOOL_FAILED in types

    @pytest.mark.asyncio
    async def test_tool_events_have_correct_fields(self, collector, executor, tool_registry):
        """Tool events contain run_id, user_id, tenant_id, tool_name."""
        spec = ToolSpec(name="order.query", description="Query orders", risk_level="low")
        async def fn(order_id: str):
            return {}
        tool_registry.register("order.query", spec, fn=fn)
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", trace_id="trace-5")
        await executor.execute("order.query", {"order_id": "123"}, ctx)
        events = await collector.get_events("trace-5")
        for ev in events:
            assert ev.run_id == "r1"
            assert ev.user_id == "u1"
            assert ev.tenant_id == "t1"
            assert ev.tool_name == "order.query"


class TestWorkflowExecutorTracing:
    """WorkflowExecutor emits workflow/agent/routing events."""

    @pytest.fixture
    def collector(self):
        return InMemoryTraceCollector()

    @pytest.mark.asyncio
    async def test_handoff_emits_workflow_events(self, collector, agent_registry, tool_registry, workflow_registry):
        """Handoff workflow emits workflow.started, routing.decision, workflow.completed."""
        from agent_app.core.agent_spec import AgentSpec
        from agent_app.core.workflow import Workflow
        from agent_app.runtime.workflow_executor import WorkflowExecutor
        from agent_app.runtime.app_runner import AppRunner
        from agent_app.runtime.backends import DryRunBackend
        from agent_app.core.context import RunContext

        # Setup: triage agent + refund agent
        agent_registry.register("triage", AgentSpec(name="triage", instructions="Route to refund", model="gpt-4o", tools=[]))
        agent_registry.register("refund", AgentSpec(name="refund", instructions="Handle refunds", model="gpt-4o", tools=[]))
        wf = Workflow.handoff(entry="triage", agents=["refund"], name="support")
        workflow_registry.register(wf.name, wf)

        executor = WorkflowExecutor(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
            workflow_registry=workflow_registry,
            backend=DryRunBackend(),
            trace_collector=collector,
        )
        # Create a minimal AppRunner
        runner = AppRunner(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
            workflow_registry=workflow_registry,
            backend=DryRunBackend(),
            trace_collector=collector,
        )
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", trace_id="trace-h1")
        result = await executor.run_workflow(
            workflow=wf, input="I want a refund", context=ctx, app_runner=runner
        )
        events = await collector.get_events("trace-h1")
        types = [e.event_type for e in events]
        assert RunEventType.WORKFLOW_STARTED in types
        assert RunEventType.ROUTING_DECISION in types
        assert RunEventType.HANDOFF_OCCURRED in types
        assert RunEventType.WORKFLOW_COMPLETED in types

    @pytest.mark.asyncio
    async def test_orchestrator_emits_agent_events(self, collector, agent_registry, tool_registry, workflow_registry):
        """Orchestrator workflow emits agent.started and agent.completed for specialists."""
        from agent_app.core.agent_spec import AgentSpec
        from agent_app.core.workflow import Workflow
        from agent_app.runtime.workflow_executor import WorkflowExecutor
        from agent_app.runtime.app_runner import AppRunner
        from agent_app.runtime.backends import DryRunBackend
        from agent_app.core.context import RunContext

        agent_registry.register("manager", AgentSpec(name="manager", instructions="Delegate", model="gpt-4o", tools=[]))
        agent_registry.register("researcher", AgentSpec(name="researcher", instructions="Research", model="gpt-4o", tools=[]))
        agent_registry.register("writer", AgentSpec(name="writer", instructions="Write", model="gpt-4o", tools=[]))
        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["researcher", "writer"],
            name="research",
        )
        workflow_registry.register(wf.name, wf)

        executor = WorkflowExecutor(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
            workflow_registry=workflow_registry,
            backend=DryRunBackend(),
            trace_collector=collector,
        )
        runner = AppRunner(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
            workflow_registry=workflow_registry,
            backend=DryRunBackend(),
            trace_collector=collector,
        )
        ctx = RunContext(run_id="r2", user_id="u1", tenant_id="t1", trace_id="trace-o1")
        result = await executor.run_workflow(
            workflow=wf, input="Research AI trends", context=ctx, app_runner=runner
        )
        events = await collector.get_events("trace-o1")
        types = [e.event_type for e in events]
        assert RunEventType.WORKFLOW_STARTED in types
        # At least one agent.started / agent.completed pair for specialists
        assert RunEventType.AGENT_STARTED in types
        assert RunEventType.AGENT_COMPLETED in types
        assert RunEventType.WORKFLOW_COMPLETED in types


class TestAgentAppApprovalTracing:
    """AgentApp.approve/reject/resume emit trace events."""

    @pytest.fixture
    def app_with_tracing(self, agent_registry, tool_registry, workflow_registry):
        from agent_app.core.app import AgentApp
        from agent_app.runtime.approval_store import InMemoryApprovalStore
        from agent_app.observability.collector import InMemoryTraceCollector
        from agent_app.config.loader import _bundle
        from agent_app.governance.approval import ApprovalRequest
        collector = InMemoryTraceCollector()
        app = AgentApp(
            registry=_bundle(agent_registry, tool_registry, workflow_registry),
            approval_store=InMemoryApprovalStore(),
            trace_collector=collector,
        )
        return app, collector

    @pytest.mark.asyncio
    async def test_approve_emits_event(self, app_with_tracing):
        from agent_app.governance.approval import ApprovalRequest
        app, collector = app_with_tracing
        # Create a pending approval first
        req = ApprovalRequest(
            approval_id="apv-001",
            run_id="run-1",
            tool_name="refund.request",
            status="pending",
            tenant_id="t1",
        )
        await app.approval_store.create(req)
        approved = await app.approve("apv-001", "admin", "ok")
        assert approved.status.value == "approved"
        # Check that approval.approved was recorded
        events = await collector.get_events("")
        types = [e.event_type for e in events]
        assert "approval.approved" in types

    @pytest.mark.asyncio
    async def test_reject_emits_event(self, app_with_tracing):
        from agent_app.governance.approval import ApprovalRequest
        app, collector = app_with_tracing
        req = ApprovalRequest(
            approval_id="apv-002",
            run_id="run-2",
            tool_name="refund.request",
            status="pending",
            tenant_id="t1",
        )
        await app.approval_store.create(req)
        rejected = await app.reject("apv-002", "admin", "not allowed")
        assert rejected.status.value == "rejected"
        events = await collector.get_events("")
        types = [e.event_type for e in events]
        assert "approval.rejected" in types


# ---------------------------------------------------------------------------
# Phase 12 Step 4: Event reliability — buffer vs collector distinction
# ---------------------------------------------------------------------------

class TestAppRunnerTraceBuffer:
    """AppRunner._record_event writes to both local buffer and collector.

    The local buffer is synchronous (Tier 1); the collector write is
    fire-and-forget via asyncio.create_task (Tier 2).
    """

    @pytest.fixture
    def runner_with_collector(self, agent_registry, tool_registry, workflow_registry):
        from agent_app.runtime.app_runner import AppRunner
        from agent_app.runtime.backends import DryRunBackend
        from agent_app.observability.collector import InMemoryTraceCollector
        collector = InMemoryTraceCollector()
        return AppRunner(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
            workflow_registry=workflow_registry,
            backend=DryRunBackend(),
            trace_collector=collector,
        ), collector

    def test_record_event_appends_to_local_buffer_no_collector(self):
        """_record_event synchronously appends to _trace_events (no collector)."""
        from agent_app.runtime.app_runner import AppRunner
        from agent_app.runtime.backends import DryRunBackend
        runner = AppRunner(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            backend=DryRunBackend(),
            trace_collector=None,  # No collector = no asyncio.create_task
        )
        runner._record_event(
            "run.started",
            trace_id="trace-1",
            run_id="run-1",
            user_id="u1",
            tenant_id="t1",
        )
        assert len(runner._trace_events) == 1
        assert runner._trace_events[0].event_type == "run.started"
        assert runner._trace_events[0].trace_id == "trace-1"

    @pytest.mark.asyncio
    async def test_record_event_appends_and_schedules_collector(self, runner_with_collector):
        """_record_event appends to buffer AND schedules collector.record()."""
        runner, collector = runner_with_collector
        runner._record_event(
            "run.started",
            trace_id="trace-2",
            run_id="run-1",
            user_id="u1",
            tenant_id="t1",
        )
        # Buffer has it immediately
        assert len(runner._trace_events) == 1
        assert runner._trace_events[0].event_type == "run.started"

    @pytest.mark.asyncio
    async def test_attach_trace_copies_buffer_to_result(self, runner_with_collector):
        """_attach_trace copies local buffer to result.trace_events."""
        from agent_app.core.result import AppRunResult
        runner, _ = runner_with_collector
        runner._record_event(
            "run.started",
            trace_id="trace-3",
            run_id="run-1",
        )
        runner._record_event(
            "run.completed",
            trace_id="trace-3",
            run_id="run-1",
        )
        result = AppRunResult(run_id="run-1", status="completed")
        attached = runner._attach_trace(result, "trace-3")
        assert len(attached.trace_events) == 2
        assert attached.trace_events[0].event_type == "run.started"
        assert attached.trace_events[1].event_type == "run.completed"
        # Buffer should be cleared after attach
        assert len(runner._trace_events) == 0

    @pytest.mark.asyncio
    async def test_collector_receives_events_after_event_loop(self, runner_with_collector):
        """Collector receives events after the event loop processes pending tasks."""
        runner, collector = runner_with_collector
        trace_id = "trace-4"
        runner._record_event(
            "run.started",
            trace_id=trace_id,
            run_id="run-1",
        )
        runner._record_event(
            "run.completed",
            trace_id=trace_id,
            run_id="run-1",
        )
        # Immediately: collector may not have events (fire-and-forget)
        immediate = await collector.get_events(trace_id)
        # After yielding to event loop: collector should have all events
        import asyncio
        await asyncio.sleep(0)  # let pending tasks run
        after_flush = await collector.get_events(trace_id)
        assert len(after_flush) == 2
        types = [e.event_type for e in after_flush]
        assert "run.started" in types
        assert "run.completed" in types

    @pytest.mark.asyncio
    async def test_result_trace_events_synchronous_no_collector_needed(self):
        """result.trace_events is available without any collector configured."""
        from agent_app.runtime.app_runner import AppRunner
        from agent_app.runtime.backends import DryRunBackend
        from agent_app.core.result import AppRunResult

        runner = AppRunner(
            agent_registry=AgentRegistry(),
            tool_registry=ToolRegistry(),
            workflow_registry=WorkflowRegistry(),
            backend=DryRunBackend(),
            trace_collector=None,  # No collector
        )
        runner._record_event(
            "run.started",
            trace_id="trace-5",
            run_id="run-1",
        )
        result = AppRunResult(run_id="run-1", status="completed")
        attached = runner._attach_trace(result, "trace-5")
        # trace_events works even without a collector
        assert len(attached.trace_events) == 1
        assert attached.trace_events[0].event_type == "run.started"
        assert attached.trace_id == "trace-5"


class TestEvalTraceEventsAssertion:
    """Regression tests for eval trace_events assertions."""

    def _make_result(self, trace_events=None, **kwargs):
        defaults = {
            "run_id": "r1",
            "status": "completed",
            "final_output": "done",
            "tool_calls": [],
            "interruptions": [],
            "error": None,
            "trace_events": trace_events or [],
        }
        defaults.update(kwargs)
        from agent_app.core.result import AppRunResult
        return AppRunResult(**defaults)

    def _make_case(self, **expect_kwargs):
        defaults = {
            "id": "c1",
            "input": "hi",
            "expect": EvalExpect(status="completed"),
        }
        if "expect" in expect_kwargs:
            defaults["expect"] = expect_kwargs.pop("expect")
        defaults["expect"].__dict__.update(expect_kwargs)
        return EvalCase(**defaults)

    def test_trace_events_pass_with_stable_events(self):
        """Eval passes when result.trace_events contains expected events."""
        from agent_app.observability.events import RunEvent
        events = [
            RunEvent(event_type="run.started", trace_id="t1", run_id="r1"),
            RunEvent(event_type="run.interrupted", trace_id="t1", run_id="r1"),
        ]
        result = self._make_result(status="interrupted", trace_events=events)
        case = self._make_case(
            expect=EvalExpect(
                status="interrupted",
                trace_events=["run.started", "run.interrupted"],
            )
        )
        errors = run_assertions(case, result)
        assert errors == []

    def test_trace_events_fail_with_missing_event(self):
        """Eval produces clear error when expected trace event is missing."""
        from agent_app.observability.events import RunEvent
        events = [
            RunEvent(event_type="run.started", trace_id="t1", run_id="r1"),
        ]
        result = self._make_result(status="interrupted", trace_events=events)
        case = self._make_case(
            expect=EvalExpect(
                status="interrupted",
                trace_events=["run.started", "run.interrupted"],
            )
        )
        errors = run_assertions(case, result)
        assert len(errors) == 1
        assert "run.interrupted" in errors[0]
        assert "was not recorded" in errors[0]
        assert "run.started" in errors[0]  # lists available events

    def test_trace_events_fail_when_no_events_recorded(self):
        """Eval error message is clear when no trace events exist at all."""
        result = self._make_result(status="completed", trace_events=[])
        case = self._make_case(
            expect=EvalExpect(
                status="completed",
                trace_events=["run.started"],
            )
        )
        errors = run_assertions(case, result)
        assert len(errors) == 1
        assert "run.started" in errors[0]
        assert "(none)" in errors[0]

    def test_trace_events_empty_expectation_always_passes(self):
        """Empty trace_events list means no assertion (always passes)."""
        from agent_app.observability.events import RunEvent
        events = [
            RunEvent(event_type="run.started", trace_id="t1", run_id="r1"),
        ]
        result = self._make_result(trace_events=events)
        case = self._make_case(
            expect=EvalExpect(status="completed", trace_events=[])
        )
        errors = run_assertions(case, result)
        assert errors == []

    def test_trace_events_substring_matching(self):
        """Expected event type can be a substring of the recorded type."""
        from agent_app.observability.events import RunEvent
        events = [
            RunEvent(event_type="tool.approval_required", trace_id="t1", run_id="r1"),
        ]
        result = self._make_result(status="interrupted", trace_events=events)
        case = self._make_case(
            expect=EvalExpect(
                status="interrupted",
                trace_events=["approval_required"],
            )
        )
        errors = run_assertions(case, result)
        assert errors == []

    def test_trace_events_combined_with_other_expectations(self):
        """trace_events assertion works alongside status and output checks."""
        from agent_app.observability.events import RunEvent
        events = [
            RunEvent(event_type="run.started", trace_id="t1", run_id="r1"),
        ]
        result = self._make_result(
            status="completed",
            final_output="order 123",
            trace_events=events,
        )
        case = self._make_case(
            expect=EvalExpect(
                status="completed",
                output_contains=["order"],
                trace_events=["run.started"],
            )
        )
        errors = run_assertions(case, result)
        assert errors == []


# ---------------------------------------------------------------------------
# Phase 12 Step 6: InMemoryTraceCollector retention policy
# ---------------------------------------------------------------------------

class TestInMemoryTraceCollectorRetention:
    """Retention limits for InMemoryTraceCollector."""

    @pytest.mark.asyncio
    async def test_default_unlimited(self):
        """Default collector has no limits."""
        collector = InMemoryTraceCollector()
        for i in range(200):
            await collector.record(RunEvent(
                event_type=RunEventType.RUN_STARTED,
                trace_id=f"trace-{i:04d}",
            ))
        traces = await collector.list_traces(limit=500)  # no collector limit, pass high query limit
        assert len(traces) == 200

    @pytest.mark.asyncio
    async def test_max_traces_evicts_oldest(self):
        """max_traces=N keeps only the N newest traces."""
        collector = InMemoryTraceCollector(max_traces=5)
        for i in range(10):
            await collector.record(RunEvent(
                event_type=RunEventType.RUN_STARTED,
                trace_id=f"trace-{i:04d}",
                timestamp=datetime(2024, 1, 1, 10, i, 0, tzinfo=timezone.utc),
            ))
        traces = await collector.list_traces()
        assert len(traces) == 5
        expected = {f"trace-{i:04d}" for i in range(5, 10)}
        assert set(traces) == expected

    @pytest.mark.asyncio
    async def test_max_events_per_trace_keeps_newest(self):
        """max_events_per_trace=N keeps only the N newest events per trace."""
        collector = InMemoryTraceCollector(max_events_per_trace=3)
        trace_id = "trace-limited"
        for i in range(10):
            await collector.record(RunEvent(
                event_type=RunEventType.RUN_STARTED,
                trace_id=trace_id,
                timestamp=datetime(2024, 1, 1, 10, 0, i, tzinfo=timezone.utc),
            ))
        events = await collector.get_events(trace_id)
        assert len(events) == 3
        timestamps = [e.timestamp.second for e in events]
        assert sorted(timestamps) == [7, 8, 9]

    @pytest.mark.asyncio
    async def test_get_events_sorted_after_retention(self):
        """get_events still returns events sorted by timestamp after retention."""
        collector = InMemoryTraceCollector(max_events_per_trace=3)
        trace_id = "trace-sorted"
        for i in [5, 1, 3, 2, 4]:
            await collector.record(RunEvent(
                event_type=RunEventType.RUN_STARTED,
                trace_id=trace_id,
                timestamp=datetime(2024, 1, 1, 10, 0, i, tzinfo=timezone.utc),
            ))
        events = await collector.get_events(trace_id)
        assert len(events) == 3
        seconds = [e.timestamp.second for e in events]
        assert seconds == [3, 4, 5]  # sorted ascending, newest 3 of [1,2,3,4,5]

    @pytest.mark.asyncio
    async def test_list_traces_respects_limit_with_retention(self):
        """list_traces limit still works when max_traces is set."""
        collector = InMemoryTraceCollector(max_traces=100)
        for i in range(20):
            await collector.record(RunEvent(
                event_type=RunEventType.RUN_STARTED,
                trace_id=f"trace-{i:03d}",
                tenant_id="tenant-a" if i < 10 else "tenant-b",
            ))
        traces_a = await collector.list_traces(tenant_id="tenant-a", limit=5)
        assert len(traces_a) == 5

    @pytest.mark.asyncio
    async def test_retention_combined(self):
        """Both max_traces and max_events_per_trace work together."""
        collector = InMemoryTraceCollector(max_traces=3, max_events_per_trace=2)
        for t in range(5):
            for e in range(5):
                await collector.record(RunEvent(
                    event_type=RunEventType.RUN_STARTED,
                    trace_id=f"trace-{t}",
                    timestamp=datetime(2024, 1, 1, 10, t, e, tzinfo=timezone.utc),
                ))
        traces = await collector.list_traces()
        assert len(traces) == 3
        for tid in traces:
            events = await collector.get_events(tid)
            assert len(events) <= 2


# ---------------------------------------------------------------------------
# Phase 12 Step 6: JSONL trace maintenance utilities
# ---------------------------------------------------------------------------

class TestJSONLTraceMaintenance:
    """count_events, count_traces, and compact for JSONLTraceCollector."""

    @pytest.mark.asyncio
    async def test_count_events(self, tmp_path):
        """count_events returns the number of valid JSON lines."""
        path = tmp_path / "traces.jsonl"
        collector = JSONLTraceCollector(path)
        for i in range(5):
            await collector.record(RunEvent(
                event_type=RunEventType.RUN_STARTED,
                trace_id=f"trace-{i}",
            ))
        assert await collector.count_events() == 5

    @pytest.mark.asyncio
    async def test_count_events_empty_file(self, tmp_path):
        """count_events returns 0 for empty file."""
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        collector = JSONLTraceCollector(path)
        assert await collector.count_events() == 0

    @pytest.mark.asyncio
    async def test_count_traces(self, tmp_path):
        """count_traces returns distinct trace_id count."""
        path = tmp_path / "traces.jsonl"
        collector = JSONLTraceCollector(path)
        for _ in range(3):
            await collector.record(RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="trace-a"))
            await collector.record(RunEvent(event_type=RunEventType.RUN_COMPLETED, trace_id="trace-b"))
        assert await collector.count_traces() == 2

    @pytest.mark.asyncio
    async def test_compact_basic(self, tmp_path):
        """compact writes events to output file."""
        path = tmp_path / "traces.jsonl"
        collector = JSONLTraceCollector(path)
        await collector.record(RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1"))
        await collector.record(RunEvent(event_type=RunEventType.RUN_COMPLETED, trace_id="t1"))
        out = tmp_path / "compacted.jsonl"
        result = await collector.compact(output_path=out)
        assert result == out
        assert out.exists()
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_compact_max_events_per_trace(self, tmp_path):
        """compact with max_events_per_trace keeps only newest events."""
        path = tmp_path / "traces.jsonl"
        collector = JSONLTraceCollector(path)
        for i in range(5):
            await collector.record(RunEvent(
                event_type=RunEventType.RUN_STARTED,
                trace_id="trace-a",
                timestamp=datetime(2024, 1, 1, 10, 0, i, tzinfo=timezone.utc),
            ))
        for i in range(3):
            await collector.record(RunEvent(
                event_type=RunEventType.RUN_STARTED,
                trace_id="trace-b",
                timestamp=datetime(2024, 1, 1, 10, 1, i, tzinfo=timezone.utc),
            ))
        out = tmp_path / "compact.jsonl"
        await collector.compact(output_path=out, max_events_per_trace=2)
        new_collector = JSONLTraceCollector(out)
        events_a = await new_collector.get_events("trace-a")
        events_b = await new_collector.get_events("trace-b")
        assert len(events_a) == 2
        assert len(events_b) == 2
        assert events_a[0].timestamp.second == 3
        assert events_a[1].timestamp.second == 4

    @pytest.mark.asyncio
    async def test_compact_atomic_replace(self, tmp_path):
        """compact with output_path=None does atomic replace."""
        path = tmp_path / "traces.jsonl"
        collector = JSONLTraceCollector(path)
        await collector.record(RunEvent(event_type=RunEventType.RUN_STARTED, trace_id="t1"))
        await collector.compact()
        assert path.exists()
        new_collector = JSONLTraceCollector(path)
        events = await new_collector.get_events("t1")
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_compact_skips_invalid_lines(self, tmp_path):
        """compact skips invalid JSON lines gracefully."""
        path = tmp_path / "traces.jsonl"
        path.write_text(
            '{"event_type": "run.started", "trace_id": "t1", "timestamp": "2024-01-01T10:00:00+00:00"}\n'
            'not valid json\n'
            '{"event_type": "run.completed", "trace_id": "t1", "timestamp": "2024-01-01T10:00:01+00:00"}\n',
            encoding="utf-8",
        )
        collector = JSONLTraceCollector(path)
        out = tmp_path / "clean.jsonl"
        await collector.compact(output_path=out)
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# Phase 12 Step 6: Config with retention settings
# ---------------------------------------------------------------------------

class TestTracingConfigRetention:
    """Config schema and loader support retention fields."""

    def test_tracing_config_defaults(self, tmp_path):
        """Default TracingConfig has no retention limits."""
        from agent_app.config.schema import TracingConfig
        cfg = TracingConfig()
        assert cfg.max_traces is None
        assert cfg.max_events_per_trace is None

    def test_tracing_config_with_retention(self, tmp_path):
        """TracingConfig accepts retention fields."""
        from agent_app.config.schema import TracingConfig
        cfg = TracingConfig(type="memory", max_traces=1000, max_events_per_trace=500)
        assert cfg.max_traces == 1000
        assert cfg.max_events_per_trace == 500

    def test_old_config_still_loads(self, tmp_path):
        """Config without retention fields loads successfully."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(
            "app:\n  name: test\nmodels:\n  default: gpt-4o\n"
            "observability:\n  tracing:\n    type: memory\n",
            encoding="utf-8",
        )
        from agent_app.config.loader import load_config
        config = load_config(config_file)
        assert config.observability.tracing.type == "memory"
        assert config.observability.tracing.max_traces is None
        assert config.observability.tracing.max_events_per_trace is None

    def test_config_with_retention_fields(self, tmp_path):
        """Config with retention fields loads and passes to collector."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(
            "app:\n  name: test\nmodels:\n  default: gpt-4o\n"
            "observability:\n  tracing:\n    type: memory\n"
            "    max_traces: 500\n    max_events_per_trace: 200\n",
            encoding="utf-8",
        )
        from agent_app.config.loader import load_config
        config = load_config(config_file)
        assert config.observability.tracing.max_traces == 500
        assert config.observability.tracing.max_events_per_trace == 200

    def test_build_app_passes_retention_to_collector(self, tmp_path):
        """build_app passes retention settings to InMemoryTraceCollector."""
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(
            "app:\n  name: test\nmodels:\n  default: gpt-4o\n"
            "observability:\n  tracing:\n    type: memory\n"
            "    max_traces: 10\n    max_events_per_trace: 5\n",
            encoding="utf-8",
        )
        from agent_app.config.loader import build_app
        app = build_app(config_file)
        assert app.trace_collector is not None
        assert app.trace_collector._max_traces == 10
        assert app.trace_collector._max_events_per_trace == 5

    def test_accepts_otel_type(self) -> None:
        from agent_app.config.schema import TracingConfig
        cfg = TracingConfig(type="otel")
        assert cfg.type == "otel"
        assert cfg.otel_service_name == "agent-app"
        assert cfg.otel_exporter == "console"

    def test_rejects_invalid_type(self) -> None:
        from agent_app.config.schema import TracingConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TracingConfig(type="invalid")

    def test_rejects_invalid_otel_exporter(self) -> None:
        from agent_app.config.schema import TracingConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TracingConfig(otel_exporter="invalid")


# ---------------------------------------------------------------------------
# Phase 12 Step 6: OpenTelemetry bridge stub
# ---------------------------------------------------------------------------

class TestOpenTelemetryBridge:
    """OpenTelemetryTraceExporter import and error behavior."""

    def test_import_does_not_require_opentelemetry(self):
        """Importing the otel module succeeds without opentelemetry installed."""
        # This should not raise — the module itself must not import opentelemetry at top level
        import agent_app.observability.otel  # noqa: F401

    def test_exporter_raises_without_opentelemetry(self):
        """Instantiating exporter without opentelemetry gives clear error."""
        import agent_app.observability.otel as otel_mod
        with pytest.raises(otel_mod.OpenTelemetryNotInstalledError, match="pip install"):
            otel_mod.OpenTelemetryTraceExporter()

    def test_exporter_error_message(self):
        """Error message contains the correct pip install command."""
        import agent_app.observability.otel as otel_mod
        try:
            otel_mod.OpenTelemetryTraceExporter()
        except otel_mod.OpenTelemetryNotInstalledError as exc:
            assert "agent-app-framework[otel]" in str(exc)

    @pytest.mark.asyncio
    async def test_export_events_raises_without_otel(self):
        """export_events also raises when opentelemetry is not installed."""
        import agent_app.observability.otel as otel_mod
        exporter = otel_mod.OpenTelemetryTraceExporter.__new__(otel_mod.OpenTelemetryTraceExporter)
        exporter._service_name = "test"
        exporter._tracer = None
        with pytest.raises(otel_mod.OpenTelemetryNotInstalledError):
            await exporter.export_events([])

    def test_get_spans_returns_empty_without_otel(self):
        """get_spans returns empty list without opentelemetry."""
        import agent_app.observability.otel as otel_mod
        exporter = otel_mod.OpenTelemetryTraceExporter.__new__(otel_mod.OpenTelemetryTraceExporter)
        assert exporter.get_spans() == []
