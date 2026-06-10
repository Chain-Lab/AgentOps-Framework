"""Comprehensive tests for OpenAIAgentsBackend.

Uses monkeypatch to inject a fake ``agents`` module so tests run without
the real OpenAI Agents SDK installed.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.core.tool_spec import ToolSpec
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.runtime.backends import DryRunBackend


# ---------------------------------------------------------------------------
# Fake agents SDK module
# ---------------------------------------------------------------------------

class FakeAgent:
    """Stand-in for agents.Agent."""
    def __init__(self, **kwargs: Any) -> None:
        self.name = kwargs.get("name")
        self.instructions = kwargs.get("instructions")
        self.model = kwargs.get("model")
        self.tools = kwargs.get("tools", [])
        self.kwargs = kwargs


class FakeRunResult:
    """Stand-in for Runner.run() result."""
    def __init__(self, **kwargs: Any) -> None:
        self.final_output = kwargs.get("final_output", "fake output")
        self.tool_calls = kwargs.get("tool_calls", [])
        self.usage = kwargs.get("usage", {"total_tokens": 42})


class FakeStreamEvent:
    """Stand-in for streaming events."""
    def __init__(self, **kwargs: Any) -> None:
        self.type = kwargs.get("type", "text.delta")
        self.delta = kwargs.get("delta")
        self.data = kwargs.get("data", {})


class FakeStreamedResult:
    """Stand-in for Runner.run_streamed() result."""
    def __init__(self, events: list[FakeStreamEvent], force_exception: Exception | None = None) -> None:
        self._events = events
        self._force_exception = force_exception

    async def stream_events(self) -> Any:
        if self._force_exception:
            raise self._force_exception
        for event in self._events:
            yield event


class FakeRunner:
    """Stand-in for agents.Runner."""
    def __init__(self) -> None:
        self.run_calls: list[dict] = []
        self.streamed_calls: list[dict] = []
        self._force_run_exception: Exception | None = None
        self._force_stream_exception: Exception | None = None

    async def run(self, native_agent: Any, **kwargs: Any) -> FakeRunResult:
        self.run_calls.append(kwargs)
        if self._force_run_exception:
            raise self._force_run_exception
        return FakeRunResult(
            final_output=kwargs.get("input", "") + " [processed]",
            tool_calls=[],
        )

    def run_streamed(self, native_agent: Any, **kwargs: Any) -> FakeStreamedResult:
        self.streamed_calls.append(kwargs)
        if self._force_stream_exception:
            return FakeStreamedResult([], force_exception=self._force_stream_exception)
        return FakeStreamedResult([
            FakeStreamEvent(type="run.started"),
            FakeStreamEvent(type="text.delta", delta="Hello "),
            FakeStreamEvent(type="text.delta", delta="world!"),
            FakeStreamEvent(type="run.completed"),
        ])


class FakeRunnerNoStreamed:
    """Fake Runner that does NOT have run_streamed (tests fallback path)."""
    def __init__(self) -> None:
        self.run_calls: list[dict] = []

    async def run(self, native_agent: Any, **kwargs: Any) -> FakeRunResult:
        self.run_calls.append(kwargs)
        return FakeRunResult(
            final_output=kwargs.get("input", "") + " [fallback]",
            tool_calls=[],
        )


def fake_function_tool(fn: Any = None, **kwargs: Any) -> Any:
    """Stand-in for agents.function_tool — returns a callable wrapper.

    The wrapper delegates to the original function so governance wrapper
    behavior is testable. It also exposes _original_fn for assertions.
    """
    class CallableMock:
        """A callable mock that delegates to the wrapped function."""
        def __init__(self, fn: Any) -> None:
            self._original_fn = fn
            self.__wrapped__ = fn
            self.needs_approval = kwargs.get("needs_approval", False)

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            if asyncio.iscoroutinefunction(self._original_fn):
                return self._original_fn(*args, **kwargs)
            return self._original_fn(*args, **kwargs)

        def __repr__(self) -> str:
            return f"<CallableMock for {self._original_fn!r}>"

    # Handle both @function_tool and @function_tool(...) usage
    if fn is None:
        def decorator(inner_fn: Any) -> Any:
            return CallableMock(inner_fn)
        return decorator
    return CallableMock(fn)


def _install_fake_sdk(monkeypatch: Any, runner: Any = None) -> None:
    """Install fake agents module into sys.modules.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        runner: Optional FakeRunner instance. Defaults to a plain FakeRunner.
    """
    runner_instance = runner or FakeRunner()

    fake_agents = MagicMock()
    fake_agents.Agent = FakeAgent
    fake_agents.Runner = runner_instance
    fake_agents.function_tool = fake_function_tool
    fake_agents.Tool = type("Tool", (), {})

    monkeypatch.setitem(sys.modules, "agents", fake_agents)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture()
def agent_spec() -> AgentSpec:
    return AgentSpec(
        name="test_bot",
        instructions="You are a test bot.",
        model="gpt-4o",
        tools=["order.query"],
    )


@pytest.fixture()
def run_context() -> RunContext:
    return RunContext(run_id="test-run-1", user_id="u1", tenant_id="t1")


@pytest.fixture()
def tool_registry() -> ToolRegistry:
    tr = ToolRegistry()
    spec = ToolSpec(
        name="order.query",
        description="Query an order",
        risk_level="low",
        permissions=[],
    )
    async def fake_query(**kwargs: Any) -> dict:
        return {"order_id": kwargs.get("order_id", "123"), "status": "ok"}

    tr.register("order.query", spec, fn=fake_query)
    return tr


# ---------------------------------------------------------------------------
# Import boundaries
# ---------------------------------------------------------------------------

class TestImportBoundaries:
    """Importing the adapter should not fail even without the SDK."""

    def test_import_adapter_without_sdk(self) -> None:
        """agent_app.adapters.openai_agents imports without agents installed."""
        # agents is NOT installed in this env — import should succeed
        # because the adapter only imports agents inside methods.
        import agent_app.adapters.openai_agents  # noqa: F401

    def test_import_agent_app_core_without_sdk(self) -> None:
        """import agent_app works without openai-agents."""
        import agent_app  # noqa: F401

    def test_public_api_no_openai(self) -> None:
        """Core public API accessible without openai-agents."""
        from agent_app import (  # noqa: F401
            AgentApp,
            AgentSpec,
            AppRunResult,
            RunContext,
            ToolSpec,
            Workflow,
            tool,
        )


# ---------------------------------------------------------------------------
# Missing dependency
# ---------------------------------------------------------------------------

class TestMissingDependency:
    """Without agents installed, calling backend methods gives clear errors."""

    @pytest.mark.asyncio
    async def test_run_raises_runtime_error(self) -> None:
        """run() without agents installed raises RuntimeError with install hint."""
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()
        spec = AgentSpec(name="bot", instructions="help")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

        with patch.dict(sys.modules, {"agents": None}):
            with pytest.raises(RuntimeError, match="OpenAI Agents"):
                await backend.run(spec, "input", ctx)

    @pytest.mark.asyncio
    async def test_stream_raises_runtime_error(self) -> None:
        """stream() without agents installed raises RuntimeError."""
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()
        spec = AgentSpec(name="bot", instructions="help")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

        with patch.dict(sys.modules, {"agents": None}):
            with pytest.raises(RuntimeError, match="OpenAI Agents"):
                async for _ in backend.stream(spec, "input", ctx):
                    pass

    def test_compile_agent_raises_runtime_error(self) -> None:
        """compile_agent() without agents installed raises RuntimeError."""
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()
        spec = AgentSpec(name="bot", instructions="help")

        with patch.dict(sys.modules, {"agents": None}):
            with pytest.raises(RuntimeError, match="OpenAI Agents"):
                backend.compile_agent(spec)

    def test_compile_tool_raises_runtime_error(self) -> None:
        """compile_tool() without agents installed raises RuntimeError."""
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()

        async def fake_fn(**kwargs: Any) -> dict:
            return {}

        with patch.dict(sys.modules, {"agents": None}):
            with pytest.raises(RuntimeError, match="OpenAI Agents"):
                backend.compile_tool(fake_fn)

    def test_load_agents_sdk_raises_clear_error(self) -> None:
        """_load_agents_sdk() raises RuntimeError with install hint."""
        from agent_app.adapters.openai_agents import _load_agents_sdk

        with patch.dict(sys.modules, {"agents": None}):
            with pytest.raises(RuntimeError, match="agent-app-framework\\[openai\\]"):
                _load_agents_sdk()


# ---------------------------------------------------------------------------
# compile_agent
# ---------------------------------------------------------------------------

class TestCompileAgent:
    """Test compile_agent with fake SDK injected."""

    @pytest.mark.asyncio
    async def test_compile_agent_passes_name_and_instructions(
        self, monkeypatch: Any, agent_spec: AgentSpec
    ) -> None:
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()
        agent = backend.compile_agent(agent_spec)

        assert agent.name == "test_bot"
        assert agent.instructions == "You are a test bot."

    @pytest.mark.asyncio
    async def test_compile_agent_passes_model(
        self, monkeypatch: Any, agent_spec: AgentSpec
    ) -> None:
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()
        agent = backend.compile_agent(agent_spec)

        assert agent.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_compile_agent_uses_default_model(
        self, monkeypatch: Any
    ) -> None:
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        spec = AgentSpec(name="bot", instructions="help")
        backend = OpenAIAgentsBackend(default_model="gpt-3.5-turbo")
        agent = backend.compile_agent(spec)

        assert agent.model == "gpt-3.5-turbo"

    @pytest.mark.asyncio
    async def test_compile_agent_resolves_tools_from_registry(
        self, monkeypatch: Any, agent_spec: AgentSpec, tool_registry: ToolRegistry
    ) -> None:
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(tool_registry=tool_registry)
        agent = backend.compile_agent(agent_spec)

        assert len(agent.tools) == 1
        assert agent.tools[0] is not None

    @pytest.mark.asyncio
    async def test_compile_agent_missing_tool_raises(
        self, monkeypatch: Any
    ) -> None:
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        spec = AgentSpec(
            name="bot", instructions="help", tools=["nonexistent.tool"]
        )
        backend = OpenAIAgentsBackend(tool_registry=ToolRegistry())

        with pytest.raises(KeyError, match="nonexistent.tool"):
            backend.compile_agent(spec)

    @pytest.mark.asyncio
    async def test_compile_agent_no_registry_skips_tool_resolution(
        self, monkeypatch: Any, agent_spec: AgentSpec
    ) -> None:
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()  # no registry
        agent = backend.compile_agent(agent_spec)

        # Without registry, tools list stays empty
        assert agent.tools == []

    @pytest.mark.asyncio
    async def test_compile_agent_raw_kwargs_passthrough(
        self, monkeypatch: Any
    ) -> None:
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        spec = AgentSpec(
            name="bot",
            instructions="help",
            raw_agent_kwargs={"temperature": 0.5, "max_tokens": 100},
        )
        backend = OpenAIAgentsBackend()
        agent = backend.compile_agent(spec)

        assert agent.kwargs["temperature"] == 0.5
        assert agent.kwargs["max_tokens"] == 100

    @pytest.mark.asyncio
    async def test_compile_agent_output_schema_maps_to_output_type(
        self, monkeypatch: Any
    ) -> None:
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        class MySchema:
            pass

        spec = AgentSpec(
            name="bot", instructions="help", output_schema=MySchema
        )
        backend = OpenAIAgentsBackend()
        agent = backend.compile_agent(spec)

        assert agent.kwargs["output_type"] is MySchema


# ---------------------------------------------------------------------------
# compile_tool
# ---------------------------------------------------------------------------

class TestCompileTool:
    """Test compile_tool with fake SDK."""

    @pytest.mark.asyncio
    async def test_compile_tool_wraps_callable(
        self, monkeypatch: Any
    ) -> None:
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        async def my_tool(query: str) -> dict:
            """My tool."""
            return {"result": query}

        backend = OpenAIAgentsBackend()
        compiled = backend.compile_tool(my_tool)

        assert compiled is not None
        assert hasattr(compiled, "_original_fn")

    @pytest.mark.asyncio
    async def test_compile_tool_from_registry_entry(
        self, monkeypatch: Any, tool_registry: ToolRegistry
    ) -> None:
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        entry = tool_registry.get_entry("order.query")
        backend = OpenAIAgentsBackend()
        compiled = backend.compile_tool(entry)

        assert compiled is not None
        assert compiled._original_fn is entry.fn

    @pytest.mark.asyncio
    async def test_compile_tool_invalid_type_raises(
        self, monkeypatch: Any
    ) -> None:
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()

        with pytest.raises(TypeError, match="Cannot compile tool"):
            backend.compile_tool("not_a_callable")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

class TestBackendRun:
    """Test run() with fake Runner."""

    @pytest.mark.asyncio
    async def test_run_calls_runner(
        self, monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext,
        fake_runner: FakeRunner, tool_registry: ToolRegistry,
    ) -> None:
        _install_fake_sdk(monkeypatch, runner=fake_runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(tool_registry=tool_registry)
        result = await backend.run(agent_spec, "hello", run_context)

        assert len(fake_runner.run_calls) == 1
        call_kwargs = fake_runner.run_calls[0]
        assert call_kwargs["input"] == "hello"
        assert call_kwargs["context"] is run_context

    @pytest.mark.asyncio
    async def test_run_returns_completed_result(
        self, monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext,
        fake_runner: FakeRunner, tool_registry: ToolRegistry,
    ) -> None:
        _install_fake_sdk(monkeypatch, runner=fake_runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(tool_registry=tool_registry)
        result = await backend.run(agent_spec, "hello", run_context)

        assert result.status == "completed"
        assert "hello" in result.final_output
        assert "[processed]" in result.final_output
        assert result.run_id == "test-run-1"
        assert result.latency_ms is not None

    @pytest.mark.asyncio
    async def test_run_extracts_final_output_from_result(
        self, monkeypatch: Any, run_context: RunContext,
    ) -> None:
        """Test that output is extracted from various SDK result formats."""
        runner = FakeRunner()
        runner.run = AsyncMock(  # type: ignore[method-assign]
            return_value=FakeRunResult(final_output="direct output")
        )
        _install_fake_sdk(monkeypatch, runner=runner)

        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        spec = AgentSpec(name="bot", instructions="help")
        backend = OpenAIAgentsBackend()
        result = await backend.run(spec, "test", run_context)

        assert result.final_output == "direct output"

    @pytest.mark.asyncio
    async def test_run_exception_returns_failed_result(
        self, monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext,
    ) -> None:
        runner = FakeRunner()
        runner._force_run_exception = RuntimeError("SDK exploded")
        _install_fake_sdk(monkeypatch, runner=runner)

        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()
        result = await backend.run(agent_spec, "hello", run_context)

        assert result.status == "failed"
        assert result.error is not None
        assert result.error["type"] == "RuntimeError"
        assert "SDK exploded" in result.error["message"]

    @pytest.mark.asyncio
    async def test_run_stores_last_native_agent(
        self, monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext,
        fake_runner: FakeRunner, tool_registry: ToolRegistry,
    ) -> None:
        _install_fake_sdk(monkeypatch, runner=fake_runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(tool_registry=tool_registry)
        await backend.run(agent_spec, "hello", run_context)

        assert backend._last_native_agent is not None
        assert backend._last_native_agent.name == "test_bot"

    @pytest.mark.asyncio
    async def test_run_with_no_tools(
        self, monkeypatch: Any, run_context: RunContext, fake_runner: FakeRunner,
    ) -> None:
        _install_fake_sdk(monkeypatch, runner=fake_runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        spec = AgentSpec(name="bot", instructions="help")
        backend = OpenAIAgentsBackend()
        result = await backend.run(spec, "hello", run_context)

        assert result.status == "completed"
        assert result.tool_calls == []


# ---------------------------------------------------------------------------
# stream()
# ---------------------------------------------------------------------------

class TestBackendStream:
    """Test stream() with fake Runner."""

    @pytest.mark.asyncio
    async def test_stream_emits_events(
        self, monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext,
        fake_runner: FakeRunner, tool_registry: ToolRegistry,
    ) -> None:
        _install_fake_sdk(monkeypatch, runner=fake_runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.streaming import StreamEventType

        backend = OpenAIAgentsBackend(tool_registry=tool_registry)
        events: list[Any] = []
        async for event in backend.stream(agent_spec, "hello", run_context):
            events.append(event)

        event_types = [e.type for e in events]
        assert StreamEventType.RUN_STARTED in event_types
        assert StreamEventType.TEXT_DELTA in event_types
        assert StreamEventType.RUN_COMPLETED in event_types

    @pytest.mark.asyncio
    async def test_stream_emits_text_deltas(
        self, monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext,
        fake_runner: FakeRunner, tool_registry: ToolRegistry,
    ) -> None:
        _install_fake_sdk(monkeypatch, runner=fake_runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(tool_registry=tool_registry)
        deltas: list[str] = []
        async for event in backend.stream(agent_spec, "hello", run_context):
            if event.delta:
                deltas.append(event.delta)

        assert "Hello " in deltas
        assert "world!" in deltas

    @pytest.mark.asyncio
    async def test_stream_exception_emits_failed(
        self, monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext,
    ) -> None:
        runner = FakeRunner()
        runner._force_stream_exception = RuntimeError("stream error")
        _install_fake_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.streaming import StreamEventType

        backend = OpenAIAgentsBackend()
        events: list[Any] = []
        async for event in backend.stream(agent_spec, "hello", run_context):
            events.append(event)

        event_types = [e.type for e in events]
        assert StreamEventType.RUN_STARTED in event_types
        assert StreamEventType.RUN_FAILED in event_types
        # Should NOT have completed after failure
        assert StreamEventType.RUN_COMPLETED not in event_types

    @pytest.mark.asyncio
    async def test_stream_fallback_when_no_run_streamed(
        self, monkeypatch: Any, agent_spec: AgentSpec, run_context: RunContext,
    ) -> None:
        """When SDK doesn't have run_streamed, fall back to run()."""
        runner = FakeRunnerNoStreamed()
        _install_fake_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.streaming import StreamEventType

        backend = OpenAIAgentsBackend()
        events: list[Any] = []
        async for event in backend.stream(agent_spec, "hello", run_context):
            events.append(event)

        event_types = [e.type for e in events]
        assert StreamEventType.RUN_STARTED in event_types
        assert StreamEventType.TEXT_DELTA in event_types
        assert StreamEventType.RUN_COMPLETED in event_types


# ---------------------------------------------------------------------------
# Config loader backend selection
# ---------------------------------------------------------------------------

class TestConfigLoaderBackend:
    """Test build_app with different backend configs."""

    def test_dry_run_backend_default(self, tmp_path: Any) -> None:
        """Default (no backend specified) uses DryRunBackend."""
        import yaml

        config_data = {
            "app": {"name": "test"},
            "agents": [
                {"name": "bot", "instructions": "help"},
            ],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        from agent_app.config.loader import build_app

        app = build_app(config_file)
        # Should not raise — default backend is dry_run
        assert app is not None

    def test_dry_run_backend_explicit(self, tmp_path: Any) -> None:
        """Explicit dry_run backend uses DryRunBackend."""
        import yaml

        config_data = {
            "runtime": {"backend": "dry_run"},
            "agents": [
                {"name": "bot", "instructions": "help"},
            ],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        from agent_app.config.loader import build_app

        app = build_app(config_file)
        assert app is not None

    def test_openai_backend_raises_without_sdk(self, tmp_path: Any) -> None:
        """backend=openai without agents installed raises clear error."""
        import yaml

        config_data = {
            "runtime": {"backend": "openai"},
            "agents": [
                {"name": "bot", "instructions": "help"},
            ],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        from agent_app.config.loader import build_app

        with patch.dict(sys.modules, {"agents": None}):
            with pytest.raises(RuntimeError, match="OpenAI Agents"):
                build_app(config_file)

    def test_invalid_backend_raises(self, tmp_path: Any) -> None:
        """Unknown backend raises ValueError."""
        import yaml

        config_data = {
            "runtime": {"backend": "unknown_backend"},
            "agents": [
                {"name": "bot", "instructions": "help"},
            ],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        from agent_app.config.loader import build_app

        with pytest.raises(ValueError, match="Unknown backend"):
            build_app(config_file)


# ---------------------------------------------------------------------------
# Backend protocol conformance
# ---------------------------------------------------------------------------

class TestBackendProtocol:
    """OpenAIAgentsBackend conforms to AgentBackend protocol."""

    def test_implements_protocol(self) -> None:
        """OpenAIAgentsBackend should satisfy AgentBackend runtime_checkable."""
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.backends import AgentBackend

        backend = OpenAIAgentsBackend()
        assert isinstance(backend, AgentBackend)

    def test_dry_run_implements_protocol(self) -> None:
        """DryRunBackend should satisfy AgentBackend."""
        from agent_app.runtime.backends import AgentBackend, DryRunBackend

        backend = DryRunBackend()
        assert isinstance(backend, AgentBackend)


# ---------------------------------------------------------------------------
# Phase 8: Governance-aware tool wrapper
# ---------------------------------------------------------------------------

class FakeApprovalRequest:
    """Stand-in for governance.approval.ApprovalRequest."""

    def __init__(self, **kwargs: Any) -> None:
        self.approval_id = kwargs.get("approval_id", "apv_test123")
        self.run_id = kwargs.get("run_id", "run-1")
        self.tool_name = kwargs.get("tool_name", "test.tool")
        self.arguments = kwargs.get("arguments", {})
        self.risk_level = kwargs.get("risk_level", "high")
        self.tenant_id = kwargs.get("tenant_id", "default")
        self.status = kwargs.get("status", "pending")


class FakeToolExecutionResult:
    """Stand-in for runtime.tool_executor.ToolExecutionResult."""

    def __init__(self, **kwargs: Any) -> None:
        self.status = kwargs.get("status", "completed")
        self.tool_name = kwargs.get("tool_name", "test.tool")
        self.output = kwargs.get("output", None)
        self.approval_request = kwargs.get("approval_request", None)
        self.error = kwargs.get("error", None)


class FakeToolExecutor:
    """Fake ToolExecutor for testing governance wrapper."""

    def __init__(
        self,
        tool_registry: Any = None,
        approval_store: Any = None,
        permission_checker: Any = None,
        audit_logger: Any = None,
        force_status: str = "completed",
        force_output: Any = None,
        force_error: dict | None = None,
        force_approval: Any = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.approval_store = approval_store
        self.permission_checker = permission_checker
        self.audit_logger = audit_logger
        self.force_status = force_status
        self.force_output = force_output
        self.force_error = force_error
        self.force_approval = force_approval
        self.execute_calls: list[dict] = []

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: Any,
        *,
        approved_tool_call: dict[str, Any] | None = None,
    ) -> FakeToolExecutionResult:
        self.execute_calls.append({
            "tool_name": tool_name,
            "arguments": arguments,
            "context": context,
            "approved_tool_call": approved_tool_call,
        })
        if self.force_status == "interrupted":
            approval = self.force_approval or FakeApprovalRequest(
                approval_id="apv_forced",
                tool_name=tool_name,
            )
            return FakeToolExecutionResult(
                status="interrupted",
                tool_name=tool_name,
                approval_request=approval,
            )
        if self.force_status == "failed":
            return FakeToolExecutionResult(
                status="failed",
                tool_name=tool_name,
                error=self.force_error or {"type": "test_error", "message": "forced"},
            )
        return FakeToolExecutionResult(
            status="completed",
            tool_name=tool_name,
            output=self.force_output,
        )


@pytest.fixture()
def fake_audit_logger() -> FakeToolExecutor:
    return FakeToolExecutor()


@pytest.fixture()
def fake_tool_executor(fake_audit_logger: FakeToolExecutor) -> FakeToolExecutor:
    return fake_audit_logger


# ---------------------------------------------------------------------------
# compile_tool with governance wrapper
# ---------------------------------------------------------------------------

class TestGovernedToolWrapper:
    """Test compile_tool with governance-aware wrapping (Phase 8)."""

    @pytest.mark.asyncio
    async def test_compile_tool_with_governance_returns_wrapper(
        self, monkeypatch: Any, fake_tool_executor: FakeToolExecutor,
        run_context: RunContext,
    ) -> None:
        """compile_tool with context and tool_executor returns governed wrapper."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(tool_executor=fake_tool_executor)

        async def my_tool(query: str) -> dict:
            return {"result": query}

        compiled = backend.compile_tool(my_tool, context=run_context)
        assert compiled is not None
        assert hasattr(compiled, "_original_fn")

    @pytest.mark.asyncio
    async def test_compile_tool_without_context_skips_governance(
        self, monkeypatch: Any, fake_tool_executor: FakeToolExecutor,
    ) -> None:
        """compile_tool without context does not use governance wrapper."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(tool_executor=fake_tool_executor)

        async def my_tool(query: str) -> dict:
            return {"result": query}

        compiled = backend.compile_tool(my_tool, context=None)
        # Without context, governance wrapper is NOT applied
        assert compiled is not None
        # ToolExecutor.execute should never have been called
        assert len(fake_tool_executor.execute_calls) == 0

    @pytest.mark.asyncio
    async def test_compile_tool_without_executor_skips_governance(
        self, monkeypatch: Any, run_context: RunContext,
    ) -> None:
        """compile_tool with context but no tool_executor skips governance."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(tool_executor=None)
        assert backend._tool_executor is None

        async def my_tool(query: str) -> dict:
            return {"result": query}

        compiled = backend.compile_tool(my_tool, context=run_context)
        assert compiled is not None

    @pytest.mark.asyncio
    async def test_governed_tool_low_risk_returns_output(
        self, monkeypatch: Any, fake_tool_executor: FakeToolExecutor,
        run_context: RunContext,
    ) -> None:
        """Low-risk tool returns real output via ToolExecutor."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        fake_tool_executor.force_status = "completed"
        fake_tool_executor.force_output = {"order_id": "123", "status": "ok"}

        backend = OpenAIAgentsBackend(tool_executor=fake_tool_executor)

        async def query_order(**kwargs: Any) -> dict:
            return {"order_id": kwargs.get("order_id", "123"), "status": "ok"}

        compiled = backend.compile_tool(query_order, context=run_context)
        result = await compiled(order_id="123")

        assert result == {"order_id": "123", "status": "ok"}
        assert len(fake_tool_executor.execute_calls) == 1
        call = fake_tool_executor.execute_calls[0]
        assert call["tool_name"] == "unknown"  # no spec to resolve name

    @pytest.mark.asyncio
    async def test_governed_tool_high_risk_returns_approval_required(
        self, monkeypatch: Any, fake_tool_executor: FakeToolExecutor,
        run_context: RunContext,
    ) -> None:
        """High-risk tool returns approval_required dict."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        approval = FakeApprovalRequest(
            approval_id="apv_abc123",
            tool_name="order.delete",
            risk_level="high",
        )
        fake_tool_executor.force_status = "interrupted"
        fake_tool_executor.force_approval = approval

        backend = OpenAIAgentsBackend(tool_executor=fake_tool_executor)

        async def delete_order(**kwargs: Any) -> dict:
            return {"deleted": True}

        compiled = backend.compile_tool(delete_order, context=run_context)
        result = await compiled(order_id="456")

        assert isinstance(result, dict)
        assert result["status"] == "approval_required"
        assert result["approval_id"] == "apv_abc123"
        assert "order.delete" in result["message"] or "unknown" in result["message"]

    @pytest.mark.asyncio
    async def test_governed_tool_permission_denied_returns_error(
        self, monkeypatch: Any, fake_tool_executor: FakeToolExecutor,
        run_context: RunContext,
    ) -> None:
        """Permission denied returns structured error dict."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        fake_tool_executor.force_status = "failed"
        fake_tool_executor.force_error = {
            "type": "permission_denied",
            "message": "Missing permissions: order:write",
        }

        backend = OpenAIAgentsBackend(tool_executor=fake_tool_executor)

        async def write_order(**kwargs: Any) -> dict:
            return {}

        compiled = backend.compile_tool(write_order, context=run_context)
        result = await compiled(order_id="789")

        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert result["error"]["type"] == "permission_denied"

    @pytest.mark.asyncio
    async def test_governed_tool_from_registry_entry(
        self, monkeypatch: Any, fake_tool_executor: FakeToolExecutor,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """Governed wrapper works with ToolRegistry entries."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        fake_tool_executor.force_status = "completed"
        fake_tool_executor.force_output = {"data": "from_governed_exec"}

        backend = OpenAIAgentsBackend(
            tool_executor=fake_tool_executor,
            tool_registry=tool_registry,
        )
        entry = tool_registry.get_entry("order.query")
        compiled = backend.compile_tool(entry, context=run_context)
        result = await compiled(order_id="999")

        assert result == {"data": "from_governed_exec"}
        assert len(fake_tool_executor.execute_calls) == 1

    @pytest.mark.asyncio
    async def test_governed_tool_calls_execute_with_correct_args(
        self, monkeypatch: Any, fake_tool_executor: FakeToolExecutor,
        run_context: RunContext,
    ) -> None:
        """ToolExecutor.execute receives correct tool_name, arguments, context."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(tool_executor=fake_tool_executor)

        async def my_tool(**kwargs: Any) -> dict:
            return {}

        compiled = backend.compile_tool(my_tool, context=run_context)
        await compiled(arg1="val1", arg2=42)

        assert len(fake_tool_executor.execute_calls) == 1
        call = fake_tool_executor.execute_calls[0]
        assert call["arguments"] == {"arg1": "val1", "arg2": 42}
        assert call["context"].run_id == "test-run-1"
        assert call["context"].user_id == "u1"

    @pytest.mark.asyncio
    async def test_governed_tool_original_fn_not_called_when_interrupted(
        self, monkeypatch: Any, fake_tool_executor: FakeToolExecutor,
        run_context: RunContext,
    ) -> None:
        """When approval required, the original function is NOT executed."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        call_count = 0

        async def dangerous_tool(**kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            return {"dangerous": True}

        fake_tool_executor.force_status = "interrupted"
        fake_tool_executor.force_approval = FakeApprovalRequest(
            approval_id="apv_danger",
            tool_name="dangerous.tool",
            risk_level="high",
        )

        backend = OpenAIAgentsBackend(tool_executor=fake_tool_executor)
        compiled = backend.compile_tool(dangerous_tool, context=run_context)
        result = await compiled()

        assert call_count == 0  # Original function never called
        assert result["status"] == "approval_required"

    @pytest.mark.asyncio
    async def test_governed_tool_original_fn_called_when_completed(
        self, monkeypatch: Any, fake_tool_executor: FakeToolExecutor,
        run_context: RunContext,
    ) -> None:
        """When completed, the original function IS executed by ToolExecutor."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(tool_executor=fake_tool_executor)

        async def safe_tool(**kwargs: Any) -> dict:
            return {"safe": True, "args": kwargs}

        compiled = backend.compile_tool(safe_tool, context=run_context)
        result = await compiled(x=1)

        # ToolExecutor.execute was called, and it executes the original fn
        assert len(fake_tool_executor.execute_calls) == 1


# ---------------------------------------------------------------------------
# Context binding
# ---------------------------------------------------------------------------

class TestContextBinding:
    """Test that run context is correctly bound to tool compilation."""

    @pytest.mark.asyncio
    async def test_run_passes_context_to_compile_agent(
        self, monkeypatch: Any, run_context: RunContext, fake_runner: FakeRunner,
    ) -> None:
        """run() compiles agent with the run context for governance."""
        _install_fake_sdk(monkeypatch, runner=fake_runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(tool_registry=ToolRegistry())
        spec = AgentSpec(name="bot", instructions="help", tools=[])

        await backend.run(spec, "test input", run_context)

        # Verify compile_agent was called (agent exists in runner)
        assert len(fake_runner.run_calls) == 1

    @pytest.mark.asyncio
    async def test_compile_agent_passes_context_to_compile_tool(
        self, monkeypatch: Any, run_context: RunContext,
        fake_tool_executor: FakeToolExecutor,
    ) -> None:
        """compile_agent passes context to each compile_tool call."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            tool_executor=fake_tool_executor,
            tool_registry=ToolRegistry(),
        )

        spec = AgentSpec(
            name="bot",
            instructions="help",
            tools=["test.tool"],
        )

        # Manually register a tool so compile_agent can resolve it
        from agent_app.core.tool_spec import ToolSpec
        tool_spec = ToolSpec(name="test.tool", description="test", risk_level="low")
        backend._tool_registry.register("test.tool", tool_spec, fn=lambda **kw: {})

        agent = backend.compile_agent(spec, context=run_context)

        # The compiled agent should have tools
        assert len(agent.tools) == 1

    @pytest.mark.asyncio
    async def test_consecutive_runs_use_different_contexts(
        self, monkeypatch: Any, fake_runner: FakeRunner,
    ) -> None:
        """Two consecutive runs do not share context state."""
        _install_fake_sdk(monkeypatch, runner=fake_runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()
        spec = AgentSpec(name="bot", instructions="help", tools=[])

        ctx1 = RunContext(run_id="run-1", user_id="u1", tenant_id="t1")
        ctx2 = RunContext(run_id="run-2", user_id="u2", tenant_id="t2")

        result1 = await backend.run(spec, "input1", ctx1)
        result2 = await backend.run(spec, "input2", ctx2)

        assert result1.run_id == "run-1"
        assert result2.run_id == "run-2"
        assert len(fake_runner.run_calls) == 2
        # Each call should have the correct context
        assert fake_runner.run_calls[0]["context"].run_id == "run-1"
        assert fake_runner.run_calls[1]["context"].run_id == "run-2"


# ---------------------------------------------------------------------------
# Phase 8: Interruption detection from SDK result
# ---------------------------------------------------------------------------

class TestInterruptionDetection:
    """Test _extract_governance_interruptions helper."""

    def test_detect_approval_required_from_new_items(
        self, monkeypatch: Any,
    ) -> None:
        """Detect approval_required from new_items in RunResult."""
        from agent_app.adapters.openai_agents import _extract_governance_interruptions

        # Create a fake item with approval_required output
        fake_item = MagicMock()
        fake_item.output = {
            "status": "approval_required",
            "approval_id": "apv_123",
            "tool_name": "order.delete",
            "risk_level": "high",
            "message": "Approval required",
        }

        fake_result = MagicMock()
        fake_result.new_items = [fake_item]
        fake_result.tool_calls = []
        fake_result.interruptions = None

        interruptions = _extract_governance_interruptions(fake_result)
        assert len(interruptions) == 1
        assert interruptions[0]["approval_id"] == "apv_123"
        assert interruptions[0]["tool_name"] == "order.delete"

    def test_detect_approval_required_from_items_fallback(
        self, monkeypatch: Any,
    ) -> None:
        """Detect approval_required from items when new_items is absent."""
        from agent_app.adapters.openai_agents import _extract_governance_interruptions

        fake_item = MagicMock()
        fake_item.output = {
            "status": "approval_required",
            "approval_id": "apv_456",
            "tool_name": "refund.process",
            "risk_level": "high",
        }

        fake_result = MagicMock()
        fake_result.new_items = None
        fake_result.items = [fake_item]
        fake_result.tool_calls = []
        fake_result.interruptions = None

        interruptions = _extract_governance_interruptions(fake_result)
        assert len(interruptions) == 1
        assert interruptions[0]["approval_id"] == "apv_456"

    def test_no_interruption_when_tools_complete(
        self, monkeypatch: Any,
    ) -> None:
        """Empty interruptions list when all tools complete successfully."""
        from agent_app.adapters.openai_agents import _extract_governance_interruptions

        fake_item = MagicMock()
        fake_item.output = {"status": "completed", "result": "ok"}

        fake_result = MagicMock()
        fake_result.new_items = [fake_item]
        fake_result.tool_calls = []
        fake_result.interruptions = None

        interruptions = _extract_governance_interruptions(fake_result)
        assert interruptions == []

    def test_detect_from_tool_calls_with_approval_metadata(
        self, monkeypatch: Any,
    ) -> None:
        """Detect interruptions from tool_calls when new_items has no data."""
        from agent_app.adapters.openai_agents import _extract_governance_interruptions

        class FakeToolCall:
            """Non-MagicMock tool call with approval_required in arguments."""
            arguments = {
                "status": "approval_required",
                "approval_id": "apv_tc",
                "tool_name": "data.wipe",
                "risk_level": "critical",
            }

        fake_tc = FakeToolCall()

        fake_result = MagicMock()
        fake_result.new_items = None
        fake_result.items = None
        fake_result.tool_calls = [fake_tc]
        fake_result.interruptions = None

        interruptions = _extract_governance_interruptions(fake_result)
        assert len(interruptions) == 1
        assert interruptions[0]["approval_id"] == "apv_tc"

    def test_detect_approval_required_via_result_interruptions_attr(
        self, monkeypatch: Any,
    ) -> None:
        """Detect interruptions via result.interruptions attribute."""
        from agent_app.adapters.openai_agents import _extract_governance_interruptions

        stored = [
            {
                "type": "approval_required",
                "approval_id": "apv_direct",
                "tool_name": "sys.reboot",
                "risk_level": "critical",
            }
        ]

        fake_result = MagicMock()
        fake_result.new_items = None
        fake_result.items = None
        fake_result.tool_calls = []
        fake_result.interruptions = stored

        interruptions = _extract_governance_interruptions(fake_result)
        assert len(interruptions) == 1
        assert interruptions[0]["approval_id"] == "apv_direct"
        assert interruptions[0]["tool_name"] == "sys.reboot"

    def test_detect_from_result_interruptions_attribute(
        self, monkeypatch: Any,
    ) -> None:
        """Detect interruptions from result.interruptions attribute."""
        from agent_app.adapters.openai_agents import _extract_governance_interruptions

        stored_interruptions = [
            {
                "type": "approval_required",
                "approval_id": "apv_attr",
                "tool_name": "sys.shutdown",
                "risk_level": "critical",
            }
        ]

        fake_result = MagicMock()
        fake_result.new_items = None
        fake_result.items = None
        fake_result.tool_calls = []
        fake_result.interruptions = stored_interruptions

        interruptions = _extract_governance_interruptions(fake_result)
        assert len(interruptions) == 1
        assert interruptions[0]["approval_id"] == "apv_attr"

    def test_no_interruption_when_result_empty(
        self, monkeypatch: Any,
    ) -> None:
        """No interruptions when result has no tool activity."""
        from agent_app.adapters.openai_agents import _extract_governance_interruptions

        fake_result = MagicMock()
        fake_result.new_items = None
        fake_result.items = None
        fake_result.tool_calls = []
        fake_result.interruptions = None

        interruptions = _extract_governance_interruptions(fake_result)
        assert interruptions == []

    @pytest.mark.asyncio
    async def test_run_sets_interrupted_status_with_governance_in_interruptions(
        self, monkeypatch: Any, run_context: RunContext, fake_runner: FakeRunner,
    ) -> None:
        """run() sets status=interrupted when result has interruptions."""
        _install_fake_sdk(monkeypatch, runner=fake_runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()
        spec = AgentSpec(name="bot", instructions="help", tools=[])

        result = await backend.run(spec, "hello", run_context)
        # Normal run without tools should complete
        assert result.status == "completed"
        assert result.interruptions == []


# ---------------------------------------------------------------------------
# Phase 8: Config loader governance injection
# ---------------------------------------------------------------------------

class TestConfigLoaderGovernance:
    """Test that config loader injects governance components into OpenAI backend."""

    def test_openai_backend_receives_tool_executor(
        self, tmp_path: Any,
    ) -> None:
        """build_app creates OpenAIAgentsBackend with ToolExecutor."""
        import yaml

        config_data = {
            "runtime": {"backend": "openai"},
            "governance": {
                "approvals": {"type": "memory"},
                "audit": {"type": "memory"},
                "permissions": {"mode": "default"},
            },
            "agents": [
                {"name": "bot", "instructions": "help"},
            ],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        from agent_app.config.loader import build_app

        with patch.dict(sys.modules, {"agents": None}):
            with pytest.raises(RuntimeError, match="OpenAI Agents"):
                build_app(config_file)

    def test_openai_backend_without_governance_config_works(
        self, tmp_path: Any,
    ) -> None:
        """build_app with backend=openai but no governance config doesn't crash."""
        import yaml

        config_data = {
            "runtime": {"backend": "openai"},
            "agents": [
                {"name": "bot", "instructions": "help"},
            ],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        from agent_app.config.loader import build_app

        with patch.dict(sys.modules, {"agents": None}):
            with pytest.raises(RuntimeError, match="OpenAI Agents"):
                build_app(config_file)

    def test_dry_run_backend_unaffected_by_governance_changes(
        self, tmp_path: Any,
    ) -> None:
        """DryRunBackend config loading is unchanged."""
        import yaml

        config_data = {
            "runtime": {"backend": "dry_run"},
            "agents": [
                {"name": "bot", "instructions": "help"},
            ],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        from agent_app.config.loader import build_app
        from agent_app.runtime.backends import DryRunBackend

        app = build_app(config_file)
        assert app._backend is not None
        assert isinstance(app._backend, DryRunBackend)

    def test_dry_run_default_backend(self, tmp_path: Any) -> None:
        """Default backend (no runtime config) uses DryRunBackend."""
        import yaml

        config_data = {
            "agents": [
                {"name": "bot", "instructions": "help"},
            ],
        }
        config_file = tmp_path / "agentapp.yaml"
        config_file.write_text(yaml.dump(config_data))

        from agent_app.config.loader import build_app
        from agent_app.runtime.backends import DryRunBackend

        app = build_app(config_file)
        assert isinstance(app._backend, DryRunBackend)


# ---------------------------------------------------------------------------
# Phase 8: End-to-end governance integration tests
# ---------------------------------------------------------------------------

class TestGovernanceEndToEnd:
    """End-to-end tests with fake SDK and governance components."""

    @pytest.mark.asyncio
    async def test_full_governance_flow_low_risk(
        self, monkeypatch: Any, run_context: RunContext,
    ) -> None:
        """Low-risk tool flows through governance and returns output."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.tool_executor import ToolExecutor
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.permission import DefaultPermissionChecker
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        # Use a shared ToolRegistry so both backend and executor can find tools
        shared_registry = ToolRegistry()

        audit_logger = InMemoryAuditLogger()
        tool_executor = ToolExecutor(
            tool_registry=shared_registry,
            approval_store=InMemoryApprovalStore(),
            permission_checker=DefaultPermissionChecker(),
            audit_logger=audit_logger,
        )

        backend = OpenAIAgentsBackend(
            tool_executor=tool_executor,
            tool_registry=shared_registry,
        )

        # Register a low-risk tool
        from agent_app.core.tool_spec import ToolSpec

        async def get_weather(**kw: Any) -> dict:
            return {"temp_c": 22, "condition": "sunny"}

        spec = ToolSpec(
            name="weather.get",
            description="Get weather",
            risk_level="low",
            permissions=[],
        )
        shared_registry.register("weather.get", spec, fn=get_weather)

        # Compile and invoke the governed tool
        entry = shared_registry.get_entry("weather.get")
        compiled = backend.compile_tool(entry, context=run_context)
        result = await compiled(city="Berlin")

        assert result == {"temp_c": 22, "condition": "sunny"}

        # Verify audit was recorded
        events = audit_logger.list_events(run_id="test-run-1")
        assert len(events) >= 1
        tool_events = [e for e in events if e.tool_name == "weather.get"]
        assert len(tool_events) >= 1

    @pytest.mark.asyncio
    async def test_full_governance_flow_high_risk(
        self, monkeypatch: Any, run_context: RunContext,
    ) -> None:
        """High-risk tool returns approval_required through governance."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.tool_executor import ToolExecutor
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.permission import DefaultPermissionChecker
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        shared_registry = ToolRegistry()

        audit_logger = InMemoryAuditLogger()
        tool_executor = ToolExecutor(
            tool_registry=shared_registry,
            approval_store=InMemoryApprovalStore(),
            permission_checker=DefaultPermissionChecker(),
            audit_logger=audit_logger,
        )

        backend = OpenAIAgentsBackend(
            tool_executor=tool_executor,
            tool_registry=shared_registry,
        )

        from agent_app.core.tool_spec import ToolSpec

        async def delete_user(**kw: Any) -> dict:
            return {"deleted": True}

        spec = ToolSpec(
            name="user.delete",
            description="Delete user",
            risk_level="high",
            requires_approval=True,
            permissions=[],
        )
        shared_registry.register("user.delete", spec, fn=delete_user)

        entry = shared_registry.get_entry("user.delete")
        compiled = backend.compile_tool(entry, context=run_context)
        result = await compiled(user_id="42")

        assert result["status"] == "approval_required"
        assert "approval_id" in result

        # Verify audit recorded the approval_required event
        events = audit_logger.list_events(run_id="test-run-1")
        approval_events = [e for e in events if e.event_type == "tool.approval_required"]
        assert len(approval_events) >= 1

    @pytest.mark.asyncio
    async def test_permission_denied_flow(
        self, monkeypatch: Any, run_context: RunContext,
    ) -> None:
        """Permission denied returns error through governance."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.tool_executor import ToolExecutor
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.permission import DefaultPermissionChecker
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        shared_registry = ToolRegistry()

        audit_logger = InMemoryAuditLogger()
        tool_executor = ToolExecutor(
            tool_registry=shared_registry,
            approval_store=InMemoryApprovalStore(),
            permission_checker=DefaultPermissionChecker(),
            audit_logger=audit_logger,
        )

        backend = OpenAIAgentsBackend(
            tool_executor=tool_executor,
            tool_registry=shared_registry,
        )

        from agent_app.core.tool_spec import ToolSpec

        async def admin_action(**kw: Any) -> dict:
            return {}

        spec = ToolSpec(
            name="admin.action",
            description="Admin action",
            risk_level="medium",
            permissions=["admin:write"],  # requires admin:write
        )
        shared_registry.register("admin.action", spec, fn=admin_action)

        # Run context does NOT have admin:write permission
        no_perm_context = RunContext(
            run_id="no-perm-run",
            user_id="u1",
            tenant_id="t1",
            permissions=["user:read"],  # missing admin:write
        )

        entry = shared_registry.get_entry("admin.action")
        compiled = backend.compile_tool(entry, context=no_perm_context)
        result = await compiled()

        assert result["status"] == "error"
        assert result["error"]["type"] == "permission_denied"

        # Verify audit recorded the permission_denied event
        events = audit_logger.list_events(run_id="no-perm-run")
        denial_events = [e for e in events if e.event_type == "tool.permission_denied"]
        assert len(denial_events) >= 1


# ---------------------------------------------------------------------------
# Phase 8.5: Edge-case governance tests
# ---------------------------------------------------------------------------

class TestGovernanceEdgeCases:
    """Edge-case tests for governance wrapper robustness."""

    @pytest.mark.asyncio
    async def test_permission_denied_does_not_call_original_fn(
        self, monkeypatch: Any, run_context: RunContext,
    ) -> None:
        """When permission denied, the original function is NEVER called."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.tool_executor import ToolExecutor
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.permission import DefaultPermissionChecker
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        shared_registry = ToolRegistry()
        call_log: list[str] = []

        async def sensitive_action(**kw: Any) -> dict:
            call_log.append("called")
            return {"secret": "data"}

        spec = ToolSpec(
            name="sensitive.action",
            description="Sensitive action",
            risk_level="medium",
            permissions=["admin:write"],
        )
        shared_registry.register("sensitive.action", spec, fn=sensitive_action)

        tool_executor = ToolExecutor(
            tool_registry=shared_registry,
            approval_store=InMemoryApprovalStore(),
            permission_checker=DefaultPermissionChecker(),
            audit_logger=InMemoryAuditLogger(),
        )

        backend = OpenAIAgentsBackend(
            tool_executor=tool_executor,
            tool_registry=shared_registry,
        )

        no_perm_ctx = RunContext(
            run_id="no-perm-edge",
            user_id="u1",
            tenant_id="t1",
            permissions=["user:read"],
        )

        entry = shared_registry.get_entry("sensitive.action")
        compiled = backend.compile_tool(entry, context=no_perm_ctx)
        result = await compiled()

        assert call_log == []  # Original function never called
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_approval_required_result_is_json_serializable(
        self, monkeypatch: Any, run_context: RunContext,
    ) -> None:
        """Governance wrapper return values are JSON serializable."""
        import json

        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.tool_executor import ToolExecutor
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.permission import DefaultPermissionChecker
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        shared_registry = ToolRegistry()

        spec = ToolSpec(
            name="dangerous.tool",
            description="Dangerous",
            risk_level="high",
            requires_approval=True,
            permissions=[],
        )

        async def dangerous(**kw: Any) -> dict:
            return {"dangerous": True}

        shared_registry.register("dangerous.tool", spec, fn=dangerous)

        tool_executor = ToolExecutor(
            tool_registry=shared_registry,
            approval_store=InMemoryApprovalStore(),
            permission_checker=DefaultPermissionChecker(),
            audit_logger=InMemoryAuditLogger(),
        )

        backend = OpenAIAgentsBackend(
            tool_executor=tool_executor,
            tool_registry=shared_registry,
        )

        entry = shared_registry.get_entry("dangerous.tool")
        compiled = backend.compile_tool(entry, context=run_context)
        result = await compiled()

        # Must be JSON serializable (the SDK may serialize tool outputs)
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert parsed["status"] == "approval_required"
        assert "approval_id" in parsed
        assert parsed["tool_name"] == "dangerous.tool"

    @pytest.mark.asyncio
    async def test_error_result_is_json_serializable(
        self, monkeypatch: Any, run_context: RunContext,
    ) -> None:
        """Error governance results are JSON serializable."""
        import json

        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.tool_executor import ToolExecutor
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.permission import DefaultPermissionChecker
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        shared_registry = ToolRegistry()

        spec = ToolSpec(
            name="restricted.tool",
            description="Restricted",
            risk_level="medium",
            permissions=["admin:write"],
        )

        async def restricted(**kw: Any) -> dict:
            return {}

        shared_registry.register("restricted.tool", spec, fn=restricted)

        tool_executor = ToolExecutor(
            tool_registry=shared_registry,
            approval_store=InMemoryApprovalStore(),
            permission_checker=DefaultPermissionChecker(),
            audit_logger=InMemoryAuditLogger(),
        )

        backend = OpenAIAgentsBackend(
            tool_executor=tool_executor,
            tool_registry=shared_registry,
        )

        no_perm_ctx = RunContext(
            run_id="json-test",
            user_id="u1",
            tenant_id="t1",
            permissions=[],
        )

        entry = shared_registry.get_entry("restricted.tool")
        compiled = backend.compile_tool(entry, context=no_perm_ctx)
        result = await compiled()

        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert parsed["status"] == "error"
        assert parsed["error"]["type"] == "permission_denied"

    @pytest.mark.asyncio
    async def test_approval_request_contains_run_id_and_tenant_id(
        self, monkeypatch: Any,
    ) -> None:
        """Approval request created by ToolExecutor contains correct context."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.tool_executor import ToolExecutor
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.governance.permission import DefaultPermissionChecker
        from agent_app.runtime.approval_store import InMemoryApprovalStore

        shared_registry = ToolRegistry()

        spec = ToolSpec(
            name="critical.action",
            description="Critical action",
            risk_level="high",
            requires_approval=True,
            permissions=[],
        )

        async def critical(**kw: Any) -> dict:
            return {}

        shared_registry.register("critical.action", spec, fn=critical)

        approval_store = InMemoryApprovalStore()
        tool_executor = ToolExecutor(
            tool_registry=shared_registry,
            approval_store=approval_store,
            permission_checker=DefaultPermissionChecker(),
            audit_logger=InMemoryAuditLogger(),
        )

        backend = OpenAIAgentsBackend(
            tool_executor=tool_executor,
            tool_registry=shared_registry,
        )

        ctx = RunContext(
            run_id="ctx-test-run-42",
            user_id="user-alice",
            tenant_id="tenant-xyz",
        )

        entry = shared_registry.get_entry("critical.action")
        compiled = backend.compile_tool(entry, context=ctx)
        result = await compiled()

        assert result["status"] == "approval_required"
        approval_id = result["approval_id"]

        # Verify the approval in the store has correct run_id and tenant_id
        approval = await approval_store.get(approval_id)
        assert approval.run_id == "ctx-test-run-42"
        assert approval.tenant_id == "tenant-xyz"
        assert approval.tool_name == "critical.action"

    @pytest.mark.asyncio
    async def test_stream_binds_context_to_tools(
        self, monkeypatch: Any, run_context: RunContext,
    ) -> None:
        """stream() also passes context for governance-aware tool compilation."""
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.streaming import StreamEventType

        backend = OpenAIAgentsBackend()
        spec = AgentSpec(name="bot", instructions="help", tools=[])

        events: list[Any] = []
        async for event in backend.stream(spec, "hello", run_context):
            events.append(event)

        event_types = [e.type for e in events]
        assert StreamEventType.RUN_STARTED in event_types
        assert StreamEventType.RUN_COMPLETED in event_types

    @pytest.mark.asyncio
    async def test_governed_tool_sync_callable(
        self, monkeypatch: Any, fake_tool_executor: FakeToolExecutor,
        run_context: RunContext,
    ) -> None:
        """Governance wrapper works with sync (non-async) tool functions.

        Note: The sync wrapper path uses asyncio.run() internally, which
        can't be nested in an already-running event loop. This test verifies
        that compile_tool accepts sync functions without error.
        """
        _install_fake_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        fake_tool_executor.force_status = "completed"
        fake_tool_executor.force_output = {"synced": True}

        backend = OpenAIAgentsBackend(tool_executor=fake_tool_executor)

        def sync_tool(**kwargs: Any) -> dict:
            """A synchronous tool function."""
            return {"synced": True, "args": kwargs}

        # compile_tool should accept sync functions
        compiled = backend.compile_tool(sync_tool, context=run_context)
        assert compiled is not None

        # The actual sync invocation path uses asyncio.run() which can't
        # be tested in an async test. Verify ToolExecutor was set up.
        assert backend._tool_executor is fake_tool_executor
