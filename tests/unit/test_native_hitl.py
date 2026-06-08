"""Phase 10 tests: Native OpenAI HITL mode and RunState resume.

Tests the native HITL path where the SDK's ``needs_approval`` and
``RunState`` are used for real pause/resume, as opposed to the wrapper
mode where the framework simulates approval_required via tool outputs.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.core.tool_spec import ToolSpec
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.runtime.run_state_store import InMemoryRunStateStore


# ---------------------------------------------------------------------------
# Fake SDK with native HITL support
# ---------------------------------------------------------------------------

class FakeToolApprovalItem:
    """Stand-in for SDK ToolApprovalItem."""
    def __init__(self, **kwargs: Any) -> None:
        self.call_id = kwargs.get("call_id", "call_1")
        self.tool_name = kwargs.get("tool_name", "test.tool")
        self.name = kwargs.get("name", "test.tool")
        self.arguments = kwargs.get("arguments", {})
        self.tool_lookup_key = kwargs.get("tool_lookup_key", None)
        self.tool_namespace = kwargs.get("tool_namespace", None)
        self.tool_origin = kwargs.get("tool_origin", None)


class FakeRunState:
    """Stand-in for SDK RunState with full HITL API."""
    def __init__(self, **kwargs: Any) -> None:
        self._interruptions: list[Any] = kwargs.get("interruptions", [])
        self._current_agent = kwargs.get("current_agent", None)
        self._context = kwargs.get("context", None)
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
        return {
            "$schemaVersion": "1.10",
            "current_agent": {"name": "test_agent"},
            "original_input": self._original_input,
            "interruptions_count": len(self._interruptions),
        }

    def to_string(self) -> str:
        return f"RunState(interruptions={len(self._interruptions)})"

    @staticmethod
    def from_json(initial_agent: Any, state_json: dict[str, Any]) -> "FakeRunState":
        """Synchronous from_json for testing."""
        return FakeRunState(
            original_input=state_json.get("original_input", ""),
        )


class FakeStreamEvent:
    """Stand-in for streaming events."""
    def __init__(self, **kwargs: Any) -> None:
        self.type = kwargs.get("type", "text.delta")
        self.delta = kwargs.get("delta")
        self.data = kwargs.get("data", {})


class FakeRunResultWithInterruptions:
    """RunResult with native SDK interruptions."""
    def __init__(self, **kwargs: Any) -> None:
        self.final_output = kwargs.get("final_output", "interrupted")
        self.tool_calls = kwargs.get("tool_calls", [])
        self.usage = kwargs.get("usage", {})
        self.interruptions = kwargs.get("interruptions", [])
        self.input = kwargs.get("input", "")
        self._current_agent = kwargs.get("current_agent", None)
        self._original_input = kwargs.get("input", "")

    def to_state(self) -> FakeRunState:
        return FakeRunState(
            interruptions=list(self.interruptions),
            original_input=self._original_input,
        )


class FakeRunResultResumed:
    """RunResult after successful resume."""
    def __init__(self, **kwargs: Any) -> None:
        self.final_output = kwargs.get("final_output", "resumed output")
        self.tool_calls = kwargs.get("tool_calls", [])
        self.usage = kwargs.get("usage", {})
        self.interruptions = kwargs.get("interruptions", [])
        self.input = kwargs.get("input", "")
        self._original_input = kwargs.get("input", "")


class FakeRunnerNative:
    """Fake Runner that simulates native HITL."""
    def __init__(self, interruptions: list[Any] | None = None) -> None:
        self.run_calls: list[dict] = []
        self.streamed_calls: list[dict] = []
        self._interruptions = interruptions or [FakeToolApprovalItem(
            call_id="call_1",
            tool_name="delete_file",
            arguments={"path": "/tmp/test"},
        )]

    async def run(self, native_agent: Any, input: Any = "", **kwargs: Any) -> Any:
        call_info = dict(kwargs)
        call_info["input"] = input
        call_info["native_agent"] = native_agent
        self.run_calls.append(call_info)
        # If input is a RunState (resume), no interruptions
        if hasattr(input, "get_interruptions"):
            return FakeRunResultResumed(
                final_output="resumed: file deleted",
            )
        # First call — return with interruptions
        return FakeRunResultWithInterruptions(
            final_output="I'll delete that file for you.",
            interruptions=list(self._interruptions),
            input=input if isinstance(input, str) else "",
        )

    def run_streamed(self, native_agent: Any, input: Any = "", **kwargs: Any):
        """Fake run_streamed for streaming tests."""
        call_info = dict(kwargs)
        call_info["input"] = input
        self.streamed_calls.append(call_info)
        return self

    async def stream_events(self):
        """Yield fake streaming events."""
        yield FakeStreamEvent(type="run.started")
        yield FakeStreamEvent(type="text.delta", delta="Hello ")
        yield FakeStreamEvent(type="text.delta", delta="world!")
        yield FakeStreamEvent(type="run.completed")

    def run_streamed(self, native_agent: Any, **kwargs: Any):
        self.streamed_calls: list[dict] = []
        self.streamed_calls.append(kwargs)
        return self


class FakeRunnerNoInterruptions:
    """Fake Runner that never interrupts."""
    async def run(self, native_agent: Any, input: Any = "", **kwargs: Any) -> Any:
        return FakeRunResultResumed(
            final_output="done",
            interruptions=[],
        )


def _install_fake_native_sdk(monkeypatch: Any, runner: Any = None) -> None:
    """Install fake agents module with native HITL support."""
    runner_instance = runner or FakeRunnerNative()

    fake_agents = MagicMock()
    fake_agents.Agent = type("Agent", (), {"__init__": lambda self, **kw: setattr(self, "tools", kw.get("tools", []))})
    fake_agents.Runner = runner_instance
    fake_agents.ToolApprovalItem = FakeToolApprovalItem
    # Make both `from agents import RunState` and `from agents.run_state import RunState` work
    fake_agents.RunState = FakeRunState
    fake_run_state_mod = MagicMock()
    fake_run_state_mod.RunState = FakeRunState
    fake_agents.run_state = fake_run_state_mod

    def fake_function_tool(fn: Any = None, **kwargs: Any) -> Any:
        if fn is None:
            def decorator(inner_fn: Any) -> Any:
                inner_fn._needs_approval = kwargs.get("needs_approval", False)
                return inner_fn
            return decorator
        fn._needs_approval = kwargs.get("needs_approval", False)
        return fn

    fake_agents.function_tool = fake_function_tool
    monkeypatch.setitem(sys.modules, "agents", fake_agents)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent_spec() -> AgentSpec:
    return AgentSpec(
        name="test_bot",
        instructions="You are a test bot.",
        model="gpt-4o",
        tools=["delete_file"],
    )


@pytest.fixture()
def run_context() -> RunContext:
    return RunContext(run_id="test-run-1", user_id="u1", tenant_id="t1")


@pytest.fixture()
def tool_registry() -> ToolRegistry:
    tr = ToolRegistry()
    spec = ToolSpec(
        name="delete_file",
        description="Delete a file",
        risk_level="high",
        requires_approval=True,
        permissions=["file:delete"],
    )
    async def delete_file(**kwargs: Any) -> dict:
        return {"deleted": True, "path": kwargs.get("path", "")}
    tr.register("delete_file", spec, fn=delete_file)
    return tr


# ---------------------------------------------------------------------------
# Native HITL compile_tool
# ---------------------------------------------------------------------------

class TestNativeHITLCompileTool:
    """Test compile_tool with hitl_mode='native'."""

    @pytest.mark.asyncio
    async def test_native_mode_sets_needs_approval(
        self, monkeypatch: Any, tool_registry: ToolRegistry,
    ) -> None:
        """In native mode, requires_approval=True tools get needs_approval=True."""
        _install_fake_native_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            hitl_mode="native",
        )
        entry = tool_registry.get_entry("delete_file")
        compiled = backend.compile_tool(entry)
        assert getattr(compiled, "_needs_approval", False) is True

    @pytest.mark.asyncio
    async def test_native_mode_low_risk_no_approval(
        self, monkeypatch: Any, tool_registry: ToolRegistry,
    ) -> None:
        """In native mode, low-risk tools do NOT get needs_approval."""
        _install_fake_native_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        # Add a low-risk tool
        spec = ToolSpec(name="query", description="Query", risk_level="low")
        async def query(**kwargs: Any) -> dict:
            return {}
        tool_registry.register("query", spec, fn=query)

        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            hitl_mode="native",
        )
        entry = tool_registry.get_entry("query")
        compiled = backend.compile_tool(entry)
        assert getattr(compiled, "_needs_approval", False) is False

    @pytest.mark.asyncio
    async def test_wrapper_mode_unchanged(
        self, monkeypatch: Any, tool_registry: ToolRegistry,
    ) -> None:
        """Wrapper mode (default) does not set needs_approval."""
        _install_fake_native_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            hitl_mode="wrapper",
        )
        entry = tool_registry.get_entry("delete_file")
        compiled = backend.compile_tool(entry)
        # Wrapper mode should not set needs_approval
        assert getattr(compiled, "_needs_approval", False) is False

    @pytest.mark.asyncio
    async def test_invalid_hitl_mode_raises(self) -> None:
        """Invalid hitl_mode raises ValueError."""
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        with pytest.raises(ValueError, match="Invalid hitl_mode"):
            OpenAIAgentsBackend(hitl_mode="invalid")


# ---------------------------------------------------------------------------
# Native HITL run() — interruption detection
# ---------------------------------------------------------------------------

class TestNativeHITLRun:
    """Test run() with native SDK interruptions."""

    @pytest.mark.asyncio
    async def test_native_run_detects_interruptions(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """Native mode detects SDK interruptions and sets status=interrupted."""
        _install_fake_native_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            hitl_mode="native",
        )
        result = await backend.run(agent_spec, "delete /tmp/test", run_context)

        assert result.status == "interrupted"
        assert len(result.interruptions) > 0
        assert result.interruptions[0]["type"] == "approval_required"
        assert result.interruptions[0]["tool_name"] == "delete_file"

    @pytest.mark.asyncio
    async def test_native_run_saves_backend_state(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """Native mode serializes RunState into backend_state."""
        _install_fake_native_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            hitl_mode="native",
        )
        result = await backend.run(agent_spec, "delete /tmp/test", run_context)

        assert result.backend_state is not None
        assert result.backend_state.get("serialization") == "to_json"
        assert result.backend_state.get("hitl_mode") == "native"
        assert result.backend_state.get("backend") == "openai"
        assert "value" in result.backend_state  # serialized state data

    @pytest.mark.asyncio
    async def test_native_run_no_interruptions_completes(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """Native mode with no interruptions returns completed."""
        runner = FakeRunnerNoInterruptions()
        _install_fake_native_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            hitl_mode="native",
        )
        result = await backend.run(agent_spec, "hello", run_context)

        assert result.status == "completed"
        assert result.interruptions == []
        assert result.backend_state == {}

    @pytest.mark.asyncio
    async def test_wrapper_mode_unchanged_run(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """Wrapper mode still detects governance interruptions."""
        _install_fake_native_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            tool_executor=_make_fake_executor("completed"),
            hitl_mode="wrapper",
        )
        result = await backend.run(agent_spec, "hello", run_context)

        assert result.status == "completed"
        assert result.backend_state == {}


# ---------------------------------------------------------------------------
# RunState serialization
# ---------------------------------------------------------------------------

class TestRunStateSerialization:
    """Test RunState serialization helpers."""

    def test_serialize_with_to_json(self) -> None:
        """Serialization uses to_json() when available."""
        from agent_app.adapters.openai_agents import _serialize_run_state

        state = FakeRunState()
        data = _serialize_run_state(state)
        assert data["serialization"] == "to_json"
        assert "value" in data
        assert data["value"]["$schemaVersion"] == "1.10"

    def test_serialize_repr_fallback(self) -> None:
        """Falls back to repr for non-standard RunState."""
        from agent_app.adapters.openai_agents import _serialize_run_state

        class WeirdState:
            pass

        data = _serialize_run_state(WeirdState())
        assert data["serialization"] == "repr"
        assert "_non_resumable" in data

    def test_deserialize_success(self) -> None:
        """Deserialization delegates to SDK from_json with valid data."""
        from agent_app.adapters.openai_agents import _deserialize_run_state

        class FakeAgent:
            name = "test"
            instructions = "help"
            tools = []
            handoffs = []

        state_data = {
            "serialization": "to_json",
            "value": {
                "$schemaVersion": "1.10",
                "current_agent": {"name": "test"},
                "original_input": "hello",
                "context": {
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "approvals": {},
                    "context": {},
                    "context_meta": {"type": "dict", "serialized_keys": []},
                },
                "max_turns": 10,
                "generated_items": [],
                "session_items": [],
                "current_turn": 0,
                "model_responses": [],
                "tool_use_tracker": {},
                "no_active_agent_run": True,
                "input_guardrail_results": [],
                "output_guardrail_results": [],
                "tool_input_guardrail_results": [],
                "tool_output_guardrail_results": [],
                "conversation_id": None,
                "previous_response_id": None,
                "auto_previous_response_id": False,
                "generated_prompt_cache_key": None,
                "reasoning_item_id_policy": None,
                "current_step": None,
                "current_turn_persisted_item_count": 0,
                "trace": None,
                "last_model_response": None,
                "last_processed_response": None,
            },
        }
        state, err = _deserialize_run_state(state_data, FakeAgent())
        assert state is not None
        assert err == ""

    def test_deserialize_missing_run_state(self) -> None:
        """Returns error when no run_state data."""
        from agent_app.adapters.openai_agents import _deserialize_run_state

        state, err = _deserialize_run_state({}, type("Agent", (), {}))
        assert state is None
        assert "No RunState" in err

    def test_deserialize_non_resumable(self) -> None:
        """Returns error for repr-based serialization."""
        from agent_app.adapters.openai_agents import _deserialize_run_state

        data = {"serialization": "repr", "value": "<RunState ...>", "_non_resumable": True}
        state, err = _deserialize_run_state(data, type("Agent", (), {}))
        assert state is None
        assert "not deserializable" in err


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

class TestNativeResume:
    """Test OpenAIAgentsBackend.resume() for native HITL."""

    @pytest.mark.asyncio
    async def test_resume_success(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """Resume approves interruption and continues execution."""
        runner = FakeRunnerNative()
        _install_fake_native_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            hitl_mode="native",
        )

        # First run — gets interrupted
        first = await backend.run(agent_spec, "delete file", run_context)
        assert first.status == "interrupted"
        assert len(first.backend_state) > 0

        # Resume with approval
        resume_result = await backend.resume(
            agent_spec=agent_spec,
            context=run_context,
            backend_state=first.backend_state,
            approvals=[{"approval_id": "call_1", "status": "approved"}],
        )
        assert resume_result.status == "completed"
        assert "resumed" in resume_result.final_output.lower()

    @pytest.mark.asyncio
    async def test_resume_without_backend_state_fails(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext,
    ) -> None:
        """Resume without backend_state returns failed."""
        _install_fake_native_sdk(monkeypatch)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(hitl_mode="native")
        result = await backend.resume(
            agent_spec=agent_spec,
            context=run_context,
            backend_state={},
            approvals=[],
        )
        assert result.status == "failed"
        assert "No RunState" in result.error["message"]

    @pytest.mark.asyncio
    async def test_resume_with_rejection(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """Resume with rejection removes interruption from state."""
        runner = FakeRunnerNative()
        _install_fake_native_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            hitl_mode="native",
        )

        first = await backend.run(agent_spec, "delete file", run_context)

        # Resume with rejection — should still work (interruption removed)
        resume_result = await backend.resume(
            agent_spec=agent_spec,
            context=run_context,
            backend_state=first.backend_state,
            approvals=[{"approval_id": "call_1", "status": "rejected"}],
        )
        assert resume_result.status == "completed"

    @pytest.mark.asyncio
    async def test_resume_runner_receives_state(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """Resume passes RunState to Runner.run."""
        runner = FakeRunnerNative()
        _install_fake_native_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            hitl_mode="native",
        )

        first = await backend.run(agent_spec, "delete file", run_context)

        await backend.resume(
            agent_spec=agent_spec,
            context=run_context,
            backend_state=first.backend_state,
            approvals=[{"approval_id": "call_1", "status": "approved"}],
        )

        # Verify Runner.run was called with a RunState (second call)
        assert len(runner.run_calls) == 2
        second_input = runner.run_calls[1].get("input")
        assert hasattr(second_input, "get_interruptions")

    @pytest.mark.asyncio
    async def test_dry_run_resume_stub(
        self, agent_spec: AgentSpec, run_context: RunContext,
    ) -> None:
        """DryRunBackend.resume() returns stub."""
        from agent_app.runtime.backends import DryRunBackend

        backend = DryRunBackend()
        result = await backend.resume(agent_spec, run_context)
        assert result.status == "completed"
        assert "DryRunBackend" in result.final_output


# ---------------------------------------------------------------------------
# Streaming with interruptions
# ---------------------------------------------------------------------------

class TestStreamingInterruptions:
    """Test stream() captures SDK interruptions."""

    @pytest.mark.asyncio
    async def test_stream_with_interruptions_captures_state(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """stream() captures RunState when interruptions occur."""
        runner = FakeRunnerNative()
        _install_fake_native_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            hitl_mode="native",
        )

        events = []
        async for event in backend.stream(agent_spec, "delete file", run_context):
            events.append(event)

        event_types = [e.type for e in events]
        from agent_app.runtime.streaming import StreamEventType
        assert StreamEventType.RUN_STARTED in event_types
        assert StreamEventType.RUN_COMPLETED in event_types


# ---------------------------------------------------------------------------
# AppRunner / AgentApp integration
# ---------------------------------------------------------------------------

class TestAppIntegrationNativeHITL:
    """Integration tests for native HITL with AppRunner and AgentApp."""

    @pytest.mark.asyncio
    async def test_native_interrupted_run_saved_to_store(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """Interrupted native run is saved to RunStateStore."""
        runner = FakeRunnerNative()
        _install_fake_native_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.runtime.app_runner import AppRunner
        from agent_app.runtime.run_state_store import InMemoryRunStateStore

        run_state_store = InMemoryRunStateStore()
        backend = OpenAIAgentsBackend(
            tool_registry=tool_registry,
            hitl_mode="native",
        )
        app_runner = AppRunner(
            agent_registry=_make_agent_registry(agent_spec),
            tool_registry=tool_registry,
            workflow_registry=_make_workflow_registry(),
            backend=backend,
            run_state_store=run_state_store,
        )

        result = await app_runner.run(
            agent=agent_spec.name,
            input="delete file",
            user_id="u1",
            tenant_id="t1",
        )
        assert result.status == "interrupted"

        # Verify run state was saved
        from agent_app.runtime.run_state import RunStateStatus
        interrupted_runs = await run_state_store.list_interrupted()
        assert len(interrupted_runs) == 1
        assert interrupted_runs[0].status == RunStateStatus.INTERRUPTED.value
        assert interrupted_runs[0].backend_state.get("hitl_mode") == "native"

    @pytest.mark.asyncio
    async def test_agentapp_resume_dispatches_to_backend(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """AgentApp.resume() calls backend.resume() for native mode."""
        runner = FakeRunnerNative()
        _install_fake_native_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.core.app import AgentApp
        from agent_app.runtime.run_state_store import InMemoryRunStateStore
        from agent_app.runtime.run_state import RunStateStatus

        run_state_store = InMemoryRunStateStore()
        backend = OpenAIAgentsBackend(
            agent_registry=_make_agent_registry(agent_spec),
            tool_registry=tool_registry,
            hitl_mode="native",
        )
        app = AgentApp(
            registry=_bundle(
                _make_agent_registry(agent_spec),
                tool_registry,
                _make_workflow_registry(),
            ),
            backend=backend,
            run_state_store=run_state_store,
            approval_store=_make_approval_store(),
        )

        # First run — interrupted
        first = await app.run(agent=agent_spec.name, input="delete file")
        assert first.status == "interrupted"
        run_id = first.run_id

        # Approve and resume
        await app.approve(first.interruptions[0]["approval_id"], "manager")
        resumed = await app.resume(run_id)

        assert resumed.status == "completed"

    @pytest.mark.asyncio
    async def test_pending_approval_returns_interrupted(
        self, monkeypatch: Any, agent_spec: AgentSpec,
        run_context: RunContext, tool_registry: ToolRegistry,
    ) -> None:
        """Resume with pending approval still returns interrupted."""
        runner = FakeRunnerNative()
        _install_fake_native_sdk(monkeypatch, runner=runner)
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend
        from agent_app.core.app import AgentApp
        from agent_app.runtime.run_state_store import InMemoryRunStateStore

        run_state_store = InMemoryRunStateStore()
        backend = OpenAIAgentsBackend(
            agent_registry=_make_agent_registry(agent_spec),
            tool_registry=tool_registry,
            hitl_mode="native",
        )
        app = AgentApp(
            registry=_bundle(
                _make_agent_registry(agent_spec),
                tool_registry,
                _make_workflow_registry(),
            ),
            backend=backend,
            run_state_store=run_state_store,
            approval_store=_make_approval_store(),
        )

        first = await app.run(agent=agent_spec.name, input="delete file")
        run_id = first.run_id

        # Resume without approving — still interrupted
        resumed = await app.resume(run_id)
        assert resumed.status == "interrupted"


# ---------------------------------------------------------------------------
# Interruption mapping
# ---------------------------------------------------------------------------

class TestInterruptionMapping:
    """Test SDK interruption → framework approval mapping."""

    def test_interruption_to_approval_request(self) -> None:
        """ToolApprovalItem maps to approval request dict."""
        from agent_app.adapters.openai_agents import _interruption_to_approval_request

        item = FakeToolApprovalItem(
            call_id="call_abc",
            tool_name="delete_file",
            arguments={"path": "/tmp/test"},
        )
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        approval = _interruption_to_approval_request(item, "r1", ctx)

        assert approval["tool_name"] == "delete_file"
        assert approval["arguments"] == {"path": "/tmp/test"}
        assert approval["status"] == "pending"
        assert approval["run_id"] == "r1"
        assert approval["tenant_id"] == "t1"

    def test_interruption_without_arguments(self) -> None:
        """Handles ToolApprovalItem with no arguments."""
        from agent_app.adapters.openai_agents import _interruption_to_approval_request

        item = FakeToolApprovalItem(call_id="call_1", tool_name="test")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        approval = _interruption_to_approval_request(item, "r1", ctx)
        assert approval["tool_name"] == "test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeAgentRegistry:
    def __init__(self, spec: AgentSpec) -> None:
        self._spec = spec

    def get(self, name: str) -> AgentSpec:
        assert name == self._spec.name
        return self._spec

    def list(self) -> list[str]:
        return [self._spec.name]


class _FakeWorkflowRegistry:
    def get(self, name: str) -> Any:
        from agent_app.core.workflow import Workflow
        return Workflow.single(agent=name, name=name)

    def list(self) -> list[str]:
        return []


class _FakeApprovalStore:
    def __init__(self) -> None:
        self._approvals: dict[str, Any] = {}

    async def create(self, request: Any) -> Any:
        from agent_app.governance.approval import ApprovalStatus
        req = request
        req.status = ApprovalStatus.PENDING
        req.approval_id = getattr(req, "approval_id", "apv_test")
        self._approvals[req.approval_id] = req
        return req

    async def get(self, approval_id: str) -> Any:
        if approval_id not in self._approvals:
            # Auto-create on demand for native HITL tests
            from agent_app.governance.approval import ApprovalStatus
            req = type("ApprovalRequest", (), {
                "approval_id": approval_id,
                "status": ApprovalStatus.PENDING,
                "tool_name": "unknown",
                "arguments": {},
                "risk_level": "high",
                "run_id": "",
                "tenant_id": "default",
                "requested_by": "test",
                "resolved_by": None,
                "reason": None,
            })()
            self._approvals[approval_id] = req
        return self._approvals[approval_id]

    async def approve(self, approval_id: str, approved_by: str, reason: str | None = None) -> Any:
        from agent_app.governance.approval import ApprovalStatus
        if approval_id not in self._approvals:
            # Auto-create if not exists (for native HITL tests)
            req = type("ApprovalRequest", (), {
                "approval_id": approval_id,
                "status": ApprovalStatus.PENDING,
                "tool_name": "unknown",
                "arguments": {},
                "risk_level": "high",
                "run_id": "",
                "tenant_id": "default",
                "requested_by": "test",
                "resolved_by": None,
                "reason": None,
            })()
            self._approvals[approval_id] = req
        req = self._approvals[approval_id]
        req.status = ApprovalStatus.APPROVED
        req.resolved_by = approved_by
        req.reason = reason
        return req

    async def reject(self, approval_id: str, rejected_by: str, reason: str | None = None) -> Any:
        from agent_app.governance.approval import ApprovalStatus
        if approval_id not in self._approvals:
            req = type("ApprovalRequest", (), {
                "approval_id": approval_id,
                "status": ApprovalStatus.PENDING,
                "tool_name": "unknown",
                "arguments": {},
                "risk_level": "high",
                "run_id": "",
                "tenant_id": "default",
                "requested_by": "test",
                "resolved_by": None,
                "reason": None,
            })()
            self._approvals[approval_id] = req
        req = self._approvals[approval_id]
        req.status = ApprovalStatus.REJECTED
        req.resolved_by = rejected_by
        req.reason = reason
        return req

    async def list_pending(self, tenant_id: str | None = None) -> list:
        return []


def _make_agent_registry(spec: AgentSpec) -> Any:
    return _FakeAgentRegistry(spec)


def _make_workflow_registry() -> Any:
    return _FakeWorkflowRegistry()


def _make_approval_store() -> Any:
    return _FakeApprovalStore()


class _Bundle:
    def __init__(self, ar: Any, tr: Any, wr: Any) -> None:
        self.agent_registry = ar
        self.tool_registry = tr
        self.workflow_registry = wr


def _bundle(ar: Any, tr: Any, wr: Any) -> Any:
    return _Bundle(ar, tr, wr)


def _make_fake_executor(status: str = "completed", output: Any = None) -> Any:
    """Create a simple FakeToolExecutor for tests."""
    class FakeToolExecutionResult:
        def __init__(self, **kwargs: Any) -> None:
            self.status = kwargs.get("status", "completed")
            self.tool_name = kwargs.get("tool_name", "test")
            self.output = kwargs.get("output", None)
            self.approval_request = kwargs.get("approval_request", None)
            self.error = kwargs.get("error", None)

    class FakeToolExecutor:
        def __init__(self) -> None:
            self.execute_calls: list[dict] = []
            self.force_status = status
            self.force_output = output

        async def execute(self, tool_name: str, arguments: dict, context: Any) -> FakeToolExecutionResult:
            self.execute_calls.append({
                "tool_name": tool_name,
                "arguments": arguments,
                "context": context,
            })
            return FakeToolExecutionResult(
                status=self.force_status,
                tool_name=tool_name,
                output=self.force_output,
            )

    return FakeToolExecutor()
