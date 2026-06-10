"""Phase 11 tests: OpenAI multi-agent backend (handoff + orchestrator).

Tests the OpenAIAgentsBackend.run_workflow() method for:
- Handoff workflows (entry agent compiles with handoffs)
- Orchestrator workflows (manager with agents-as-tools)
- Workflow trace generation
- Backend delegation from AgentApp

Uses a fake SDK injected via sys.modules monkeypatching — no real API calls.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.core.workflow import Workflow, WorkflowType
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fake SDK with multi-agent support
# ---------------------------------------------------------------------------

class FakeToolApprovalItem:
    def __init__(self, **kwargs: Any) -> None:
        self.call_id = kwargs.get("call_id", "call_1")
        self.tool_name = kwargs.get("tool_name", "test.tool")
        self.name = kwargs.get("name", "test.tool")
        self.arguments = kwargs.get("arguments", {})
        self.tool_lookup_key = kwargs.get("tool_lookup_key", None)


class FakeRunState:
    def __init__(self, **kwargs: Any) -> None:
        self._interruptions = kwargs.get("interruptions", [])
        self._original_input = kwargs.get("original_input", "")

    def get_interruptions(self) -> list[Any]:
        return list(self._interruptions)

    def approve(self, item: Any, always_approve: bool = False) -> None:
        if item in self._interruptions:
            self._interruptions.remove(item)

    def reject(self, item: Any, always_reject: bool = False, *, rejection_message: str | None = None) -> None:
        if item in self._interruptions:
            self._interruptions.remove(item)

    def to_json(self) -> dict[str, Any]:
        return {"$schemaVersion": "1.10", "original_input": self._original_input}

    @staticmethod
    def from_json(initial_agent: Any, state_json: dict[str, Any]) -> "FakeRunState":
        return FakeRunState(original_input=state_json.get("original_input", ""))


class FakeRunResult:
    """Generic SDK RunResult."""
    def __init__(self, **kwargs: Any) -> None:
        self.final_output = kwargs.get("final_output", "done")
        self.tool_calls = kwargs.get("tool_calls", [])
        self.usage = kwargs.get("usage", {})
        self.interruptions = kwargs.get("interruptions", [])
        self.input = kwargs.get("input", "")
        self._original_input = kwargs.get("input", "")

    def to_state(self) -> FakeRunState:
        return FakeRunState(original_input=self._original_input)


class FakeAgent:
    """Fake SDK Agent with handoffs and as_tool support."""
    def __init__(self, **kwargs: Any) -> None:
        self.name = kwargs.get("name", "agent")
        self.instructions = kwargs.get("instructions", "")
        self.tools = kwargs.get("tools", [])
        self.handoffs = kwargs.get("handoffs", [])
        self.model = kwargs.get("model", None)
        self._kwargs = kwargs

    def as_tool(self, **kwargs: Any) -> Any:
        """Native SDK as_tool — returns a fake tool."""

        class FakeAsTool:
            def __init__(self, **kw: Any) -> None:
                self.name = kw.get("tool_name", "agent_tool")
                self.description = kw.get("tool_description", "")
                self._needs_approval = False

        return FakeAsTool(**kwargs)


class FakeRunner:
    """Fake SDK Runner that tracks calls."""
    def __init__(self) -> None:
        self.run_calls: list[dict] = []

    async def run(self, native_agent: Any, input: Any = "", **kwargs: Any) -> FakeRunResult:
        call_info = {"input": input, "agent_name": getattr(native_agent, "name", "unknown")}
        call_info.update(kwargs)
        self.run_calls.append(call_info)

        # Detect if this is a tool call (agent has tools and is not the main agent)
        tools = getattr(native_agent, "tools", [])
        handoffs = getattr(native_agent, "handoffs", [])

        # Build tool_calls from agent tools for traceability
        tool_calls = []
        for t in tools:
            t_name = getattr(t, "name", getattr(t, "_name", "unknown_tool"))
            tool_calls.append({"tool": t_name, "arguments": {"input": str(input)[:50]}, "status": "completed"})

        if handoffs:
            # Handoff agent — return with handoffs info
            return FakeRunResult(
                final_output=f"[handoff] Agent '{getattr(native_agent, 'name', '?')}' processed: {str(input)[:50]}",
                tool_calls=tool_calls,
            )

        # Regular agent
        return FakeRunResult(
            final_output=f"[openai] Agent '{getattr(native_agent, 'name', '?')}' received: {str(input)[:50]}",
            tool_calls=tool_calls,
        )


def _install_fake_multi_agent_sdk(monkeypatch: Any, runner: Any = None) -> FakeRunner:
    """Install fake agents module with multi-agent support."""
    runner_instance = runner or FakeRunner()

    fake_agents = type(sys)("agents")
    fake_agents.Agent = FakeAgent
    fake_agents.Runner = runner_instance
    fake_agents.ToolApprovalItem = FakeToolApprovalItem
    fake_agents.RunState = FakeRunState
    fake_run_state_mod = type(sys)("agents.run_state")
    fake_run_state_mod.RunState = FakeRunState
    fake_agents.run_state = fake_run_state_mod

    def fake_function_tool(fn: Any = None, **kwargs: Any) -> Any:
        if fn is None:
            def decorator(inner_fn: Any) -> Any:
                inner_fn._needs_approval = kwargs.get("needs_approval", False)
                inner_fn._name = kwargs.get("_name", "tool")
                return inner_fn
            return decorator
        fn._needs_approval = kwargs.get("needs_approval", False)
        fn._name = kwargs.get("_name", "tool")
        return fn

    fake_agents.function_tool = fake_function_tool
    monkeypatch.setitem(sys.modules, "agents", fake_agents)
    return runner_instance


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent_registry() -> AgentRegistry:
    reg = AgentRegistry()
    for name in ["triage", "refund", "billing", "technical_support",
                 "manager", "researcher", "analyst", "writer"]:
        reg.register(name, AgentSpec(name=name, instructions=f"{name} agent", tools=[]))
    return reg


@pytest.fixture()
def tool_registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture()
def run_context() -> RunContext:
    return RunContext(run_id="test-run-1", user_id="u1", tenant_id="t1")


@pytest.fixture()
def backend(agent_registry: AgentRegistry, tool_registry: ToolRegistry) -> Any:
    from agent_app.adapters.openai_agents import OpenAIAgentsBackend
    return OpenAIAgentsBackend(
        agent_registry=agent_registry,
        tool_registry=tool_registry,
    )


# ---------------------------------------------------------------------------
# compile_agent handoffs tests
# ---------------------------------------------------------------------------

class TestCompileAgentHandoffs:
    """Test compile_agent with handoffs parameter."""

    @pytest.mark.asyncio
    async def test_compile_agent_with_handoffs(self, monkeypatch: Any, agent_registry: AgentRegistry) -> None:
        """compile_agent passes handoffs to SDK Agent."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(agent_registry=agent_registry)
        spec = AgentSpec(name="triage", instructions="Triage", tools=[])
        refund_spec = agent_registry.get("refund")
        compiled_refund = backend.compile_agent(refund_spec)

        entry = backend.compile_agent(spec, handoffs=[compiled_refund])
        assert hasattr(entry, "handoffs")
        assert len(entry.handoffs) == 1

    @pytest.mark.asyncio
    async def test_compile_agent_without_handoffs(self, monkeypatch: Any, agent_registry: AgentRegistry) -> None:
        """compile_agent without handoffs has empty handoffs."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(agent_registry=agent_registry)
        spec = AgentSpec(name="triage", instructions="Triage", tools=[])
        entry = backend.compile_agent(spec)
        assert entry.handoffs == []

    @pytest.mark.asyncio
    async def test_handoffs_override_agent_spec(self, monkeypatch: Any, agent_registry: AgentRegistry) -> None:
        """Explicit handoffs parameter takes priority over agent_spec.handoffs."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(agent_registry=agent_registry)
        refund_spec = agent_registry.get("refund")
        billing_spec = agent_registry.get("billing")
        compiled_refund = backend.compile_agent(refund_spec)
        compiled_billing = backend.compile_agent(billing_spec)

        spec = AgentSpec(name="triage", instructions="Triage", tools=[], handoffs=[])
        entry = backend.compile_agent(spec, handoffs=[compiled_refund, compiled_billing])
        assert len(entry.handoffs) == 2

    @pytest.mark.asyncio
    async def test_compile_agent_without_handoffs_not_polluted(
        self, monkeypatch: Any, agent_registry: AgentRegistry
    ) -> None:
        """Calling compile_agent without handoffs after with handoffs does not leak."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(agent_registry=agent_registry)
        refund_spec = agent_registry.get("refund")
        compiled_refund = backend.compile_agent(refund_spec)

        spec = AgentSpec(name="triage", instructions="Triage", tools=[])
        # First call with handoffs
        backend.compile_agent(spec, handoffs=[compiled_refund])
        # Second call without handoffs — should not inherit
        entry = backend.compile_agent(spec)
        assert entry.handoffs == []

    @pytest.mark.asyncio
    async def test_compile_agent_single_agent_unchanged(
        self, monkeypatch: Any, agent_registry: AgentRegistry
    ) -> None:
        """Single agent compile_agent produces no handoffs by default."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(agent_registry=agent_registry)
        spec = AgentSpec(name="assistant", instructions="Help", tools=[])
        entry = backend.compile_agent(spec)
        assert entry.handoffs == []


# ---------------------------------------------------------------------------
# Handoff workflow tests
# ---------------------------------------------------------------------------

class TestHandoffWorkflow:
    """Test OpenAIAgentsBackend._run_handoff_workflow."""

    @pytest.mark.asyncio
    async def test_handoff_workflow_compiles_entry_and_candidates(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """Handoff workflow compiles entry + all candidate agents."""
        runner = FakeRunner()
        _install_fake_multi_agent_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.handoff(
            entry="triage",
            agents=["refund", "billing", "technical_support"],
            name="test_handoff",
        )
        result = await backend._run_handoff_workflow(wf, "I want a refund", run_context)

        assert result.status == "completed"
        assert result.workflow_trace is not None
        assert result.workflow_trace.workflow_name == "test_handoff"
        assert result.workflow_trace.workflow_type == "handoff"
        # Verify Runner.run was called
        assert len(runner.run_calls) >= 1
        assert runner.run_calls[0]["agent_name"] == "triage"

    @pytest.mark.asyncio
    async def test_handoff_workflow_trace_includes_candidates(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """Workflow trace includes handoff_candidates step."""
        runner = FakeRunner()
        _install_fake_multi_agent_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.handoff(
            entry="triage",
            agents=["refund", "billing"],
            name="test",
        )
        result = await backend._run_handoff_workflow(wf, "hello", run_context)

        # Find handoff_candidates step
        candidate_steps = [
            s for s in result.workflow_trace.steps
            if s.step_type == "handoff_candidates"
        ]
        assert len(candidate_steps) == 1
        assert set(candidate_steps[0].metadata["agents"]) == {"refund", "billing"}

    @pytest.mark.asyncio
    async def test_handoff_missing_entry_agent_fails(
        self, monkeypatch: Any, tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """Missing entry agent returns failed result."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        empty_reg = AgentRegistry()
        backend = OpenAIAgentsBackend(
            agent_registry=empty_reg,
            tool_registry=tool_registry,
        )

        wf = Workflow.handoff(entry="nonexistent", agents=[], name="test")
        result = await backend._run_handoff_workflow(wf, "hello", run_context)
        assert result.status == "failed"
        assert "nonexistent" in str(result.error)

    @pytest.mark.asyncio
    async def test_handoff_missing_candidate_fails(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """Missing candidate agent returns failed result."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.handoff(
            entry="triage",
            agents=["ghost_agent"],
            name="test",
        )
        result = await backend._run_handoff_workflow(wf, "hello", run_context)
        assert result.status == "failed"
        assert "ghost_agent" in str(result.error)

    @pytest.mark.asyncio
    async def test_handoff_sdk_exception_returns_failed(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """SDK exception during handoff returns failed result."""
        import agents as real_agents

        class FailingRunner:
            async def run(self, agent: Any, input: Any = "", **kwargs: Any) -> Any:
                raise RuntimeError("SDK failure")

        _install_fake_multi_agent_sdk(monkeypatch, runner=FailingRunner())
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.handoff(entry="triage", agents=["refund"], name="test")
        result = await backend._run_handoff_workflow(wf, "hello", run_context)
        assert result.status == "failed"
        assert result.error["type"] == "backend_execution_failed"
        assert "SDK failure" not in str(result.error)


# ---------------------------------------------------------------------------
# Orchestrator compile tests
# ---------------------------------------------------------------------------

class TestOrchestratorCompile:
    """Test compile_agent_as_tool and manager compilation."""

    @pytest.mark.asyncio
    async def test_compile_agent_as_tool_uses_native_as_tool(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """compile_agent_as_tool uses SDK Agent.as_tool() when available."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        specialist = agent_registry.get("researcher")
        compiled = backend.compile_agent(specialist)
        tool = backend.compile_agent_as_tool(compiled, "researcher", "input", run_context)

        assert hasattr(tool, "name")
        assert tool.name == "researcher"

    @pytest.mark.asyncio
    async def test_compile_agent_as_tool_fallback_without_as_tool(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """compile_agent_as_tool falls back when Agent lacks as_tool."""

        class NoAsToolAgent:
            def __init__(self, **kwargs: Any) -> None:
                self.name = kwargs.get("name", "agent")
                self.tools = []

        _install_fake_multi_agent_sdk(monkeypatch)
        # Override FakeAgent to remove as_tool
        import agents as fake_mod
        fake_mod.Agent = NoAsToolAgent

        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        agent = NoAsToolAgent(name="specialist")
        tool = backend.compile_agent_as_tool(agent, "specialist", "input", run_context)
        assert tool is not None  # Fallback returns a function_tool

    @pytest.mark.asyncio
    async def test_orchestrator_manager_includes_specialist_tools(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """Manager agent compilation includes specialist as_tool tools."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["researcher", "analyst"],
            name="test",
        )

        # Verify that compile_agent_as_tool produces tools
        for spec_name in ["researcher", "analyst"]:
            spec = agent_registry.get(spec_name)
            compiled = backend.compile_agent(spec, context=run_context)
            tool = backend.compile_agent_as_tool(compiled, spec_name, "input", run_context)
            assert tool is not None


# ---------------------------------------------------------------------------
# Orchestrator workflow tests
# ---------------------------------------------------------------------------

class TestOrchestratorWorkflow:
    """Test OpenAIAgentsBackend._run_orchestrator_workflow."""

    @pytest.mark.asyncio
    async def test_orchestrator_workflow_completes(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """Orchestrator workflow returns completed result."""
        runner = FakeRunner()
        _install_fake_multi_agent_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["researcher", "analyst", "writer"],
            name="test",
        )
        result = await backend._run_orchestrator_workflow(wf, "research AI", run_context)

        assert result.status == "completed"
        assert result.workflow_trace is not None
        assert result.workflow_trace.workflow_name == "test"
        assert result.workflow_trace.workflow_type == "orchestrator"
        assert result.workflow_trace.entry_agent == "manager"

    @pytest.mark.asyncio
    async def test_orchestrator_workflow_trace_includes_agent_tools(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """Workflow trace includes agent_tools step."""
        runner = FakeRunner()
        _install_fake_multi_agent_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["researcher", "analyst"],
            name="test",
        )
        result = await backend._run_orchestrator_workflow(wf, "input", run_context)

        agent_tools_steps = [
            s for s in result.workflow_trace.steps
            if s.step_type == "agent_tools"
        ]
        assert len(agent_tools_steps) == 1
        assert set(agent_tools_steps[0].metadata["agents_as_tools"]) == {"researcher", "analyst"}

    @pytest.mark.asyncio
    async def test_orchestrator_missing_manager_fails(
        self, monkeypatch: Any, tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """Missing manager agent returns failed."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=AgentRegistry(),
            tool_registry=tool_registry,
        )

        wf = Workflow.orchestrator(
            manager="nonexistent",
            agents_as_tools=["researcher"],
            name="test",
        )
        result = await backend._run_orchestrator_workflow(wf, "input", run_context)
        assert result.status == "failed"
        assert "nonexistent" in str(result.error)

    @pytest.mark.asyncio
    async def test_orchestrator_missing_specialist_fails(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """Missing specialist agent returns failed."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["ghost_specialist"],
            name="test",
        )
        result = await backend._run_orchestrator_workflow(wf, "input", run_context)
        assert result.status == "failed"
        assert "ghost_specialist" in str(result.error)

    @pytest.mark.asyncio
    async def test_orchestrator_sdk_exception_returns_failed(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """SDK exception during orchestrator returns failed."""
        class FailingRunner:
            async def run(self, agent: Any, input: Any = "", **kwargs: Any) -> Any:
                raise RuntimeError("SDK orchestrator failure")

        _install_fake_multi_agent_sdk(monkeypatch, runner=FailingRunner())
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["researcher"],
            name="test",
        )
        result = await backend._run_orchestrator_workflow(wf, "input", run_context)
        assert result.status == "failed"
        assert result.error["type"] == "backend_execution_failed"
        assert "SDK orchestrator failure" not in str(result.error)

    @pytest.mark.asyncio
    async def test_orchestrator_no_tool_calls_still_completes(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """Orchestrator with no tool_calls still returns completed."""
        class NoToolCallsRunner:
            async def run(self, agent: Any, input: Any = "", **kwargs: Any) -> Any:
                return FakeRunResult(
                    final_output="done without tools",
                    tool_calls=[],  # no tool calls at all
                )

        _install_fake_multi_agent_sdk(monkeypatch, runner=NoToolCallsRunner())
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["researcher"],
            name="test",
        )
        result = await backend._run_orchestrator_workflow(wf, "input", run_context)
        assert result.status == "completed"
        assert result.agent_calls == []  # gracefully empty, not crashed


# ---------------------------------------------------------------------------
# run_workflow dispatch tests
# ---------------------------------------------------------------------------

class TestRunWorkflowDispatch:
    """Test run_workflow() dispatch by workflow type."""

    @pytest.mark.asyncio
    async def test_run_workflow_single_delegates_to_run(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """SINGLE workflow delegates to run()."""
        runner = FakeRunner()
        _install_fake_multi_agent_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.single(agent="refund", name="single_test")
        result = await backend.run_workflow(wf, "hello", run_context)
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_run_workflow_handoff(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """HANDOFF workflow runs via _run_handoff_workflow."""
        runner = FakeRunner()
        _install_fake_multi_agent_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.handoff(entry="triage", agents=["refund"], name="test")
        result = await backend.run_workflow(wf, "I want a refund", run_context)
        assert result.status == "completed"
        assert result.workflow_trace.workflow_type == "handoff"

    @pytest.mark.asyncio
    async def test_run_workflow_orchestrator(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """ORCHESTRATOR workflow runs via _run_orchestrator_workflow."""
        runner = FakeRunner()
        _install_fake_multi_agent_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["researcher"],
            name="test",
        )
        result = await backend.run_workflow(wf, "research AI", run_context)
        assert result.status == "completed"
        assert result.workflow_trace.workflow_type == "orchestrator"

    @pytest.mark.asyncio
    async def test_run_workflow_dag_returns_failed(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry, run_context: RunContext,
    ) -> None:
        """DAG workflow returns failed (not yet implemented)."""
        _install_fake_multi_agent_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )

        wf = Workflow(name="dag_test", type=WorkflowType.DAG)
        result = await backend.run_workflow(wf, "input", run_context)
        assert result.status == "failed"
        assert "DAG" in str(result.error)


# ---------------------------------------------------------------------------
# AgentApp integration tests
# ---------------------------------------------------------------------------

class TestAgentAppMultiAgentIntegration:
    """Test AgentApp delegates multi-agent workflows to OpenAI backend."""

    @pytest.mark.asyncio
    async def test_agentapp_handoff_with_openai_backend(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry,
    ) -> None:
        """AgentApp.run(workflow=...) delegates to OpenAI backend for handoff."""
        runner = FakeRunner()
        _install_fake_multi_agent_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.core.app import AgentApp

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )
        app = AgentApp(backend=backend)

        wf = Workflow.handoff(
            entry="triage",
            agents=["refund", "billing"],
            name="support",
        )
        app.register_workflow(wf)

        result = await app.run(workflow="support", input="I want a refund")
        assert result.status == "completed"
        assert result.workflow_trace is not None
        assert result.workflow_trace.workflow_type == "handoff"

    @pytest.mark.asyncio
    async def test_agentapp_orchestrator_with_openai_backend(
        self, monkeypatch: Any, agent_registry: AgentRegistry,
        tool_registry: ToolRegistry,
    ) -> None:
        """AgentApp.run(workflow=...) delegates to OpenAI backend for orchestrator."""
        runner = FakeRunner()
        _install_fake_multi_agent_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.core.app import AgentApp

        backend = OpenAIAgentsBackend(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
        )
        app = AgentApp(backend=backend)

        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["researcher", "writer"],
            name="research",
        )
        app.register_workflow(wf)

        result = await app.run(workflow="research", input="research AI trends")
        assert result.status == "completed"
        assert result.workflow_trace.workflow_type == "orchestrator"

    @pytest.mark.asyncio
    async def test_agentapp_dryrun_handoff_unchanged(
        self, agent_registry: AgentRegistry, tool_registry: ToolRegistry,
    ) -> None:
        """DryRun backend handoff behavior is unchanged (no OpenAI SDK)."""
        from agent_app import AgentSpec, Workflow
        from agent_app.core.app import AgentApp
        from agent_app.runtime.backends import DryRunBackend

        app = AgentApp(
            backend=DryRunBackend(),
        )
        # Register agents on the app's registry
        for name in ["triage", "refund", "billing"]:
            app.register_agent(AgentSpec(name=name, instructions=f"{name} agent", tools=[]))
        wf = Workflow.handoff(
            entry="triage",
            agents=["refund", "billing"],
            name="support",
        )
        app.register_workflow(wf)

        result = await app.run(workflow="support", input="I want a refund")
        assert result.status == "completed"
        assert result.workflow_trace is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeAgentRegistry:
    """Minimal agent registry for tests."""
    def __init__(self, specs: dict[str, AgentSpec]) -> None:
        self._specs = specs

    def get(self, name: str) -> AgentSpec:
        if name not in self._specs:
            raise KeyError(name)
        return self._specs[name]

    def list(self) -> list[str]:
        return list(self._specs.keys())


def _make_test_registry() -> tuple[AgentRegistry, dict[str, AgentSpec]]:
    """Create a real AgentRegistry with standard test agents."""
    reg = AgentRegistry()
    specs: dict[str, AgentSpec] = {}
    for name in ["triage", "refund", "billing", "technical_support",
                 "manager", "researcher", "analyst", "writer"]:
        spec = AgentSpec(name=name, instructions=f"{name} agent", tools=[])
        specs[name] = spec
        reg.register(name, spec)
    return reg, specs
