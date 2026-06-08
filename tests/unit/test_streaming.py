"""Tests for streaming events and DryRunBackend streaming."""

import pytest

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.runtime.backends import DryRunBackend
from agent_app.runtime.streaming import StreamEvent, StreamEventType


class TestStreamEvent:
    def test_create_minimal(self) -> None:
        ev = StreamEvent(type="run.started", run_id="r1")
        assert ev.type == "run.started"
        assert ev.run_id == "r1"
        assert ev.delta is None
        assert ev.data == {}

    def test_to_dict_minimal(self) -> None:
        ev = StreamEvent(type="run.started", run_id="r1")
        d = ev.to_dict()
        assert d == {"type": "run.started", "run_id": "r1"}

    def test_to_dict_with_delta(self) -> None:
        ev = StreamEvent(type="text.delta", run_id="r1", delta="hello")
        d = ev.to_dict()
        assert d["delta"] == "hello"

    def test_to_dict_with_data(self) -> None:
        ev = StreamEvent(
            type="run.completed", run_id="r1", data={"final_output": "done"}
        )
        d = ev.to_dict()
        assert d["data"] == {"final_output": "done"}


class TestStreamEventType:
    def test_values(self) -> None:
        assert StreamEventType.RUN_STARTED == "run.started"
        assert StreamEventType.TEXT_DELTA == "text.delta"
        assert StreamEventType.RUN_COMPLETED == "run.completed"
        assert StreamEventType.RUN_FAILED == "run.failed"


class TestDryRunBackendStream:
    @pytest.mark.asyncio
    async def test_stream_produces_events(self) -> None:
        backend = DryRunBackend()
        spec = AgentSpec(name="bot", instructions="help")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

        events = []
        async for ev in backend.stream(spec, "hello", ctx):
            events.append(ev)

        assert len(events) >= 3
        assert events[0].type == StreamEventType.RUN_STARTED
        assert events[-1].type == StreamEventType.RUN_COMPLETED

    @pytest.mark.asyncio
    async def test_stream_has_text_deltas(self) -> None:
        backend = DryRunBackend()
        spec = AgentSpec(name="bot", instructions="help")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

        deltas = []
        async for ev in backend.stream(spec, "hello", ctx):
            if ev.type == StreamEventType.TEXT_DELTA:
                deltas.append(ev.delta)

        assert len(deltas) > 0
        full = "".join(deltas)
        assert "hello" in full
        assert "bot" in full

    @pytest.mark.asyncio
    async def test_stream_run_id_consistent(self) -> None:
        backend = DryRunBackend()
        spec = AgentSpec(name="bot", instructions="help")
        ctx = RunContext(run_id="r_xyz", user_id="u1", tenant_id="t1")

        run_ids = []
        async for ev in backend.stream(spec, "hi", ctx):
            if ev.run_id:
                run_ids.append(ev.run_id)

        assert all(rid == "r_xyz" for rid in run_ids)

    @pytest.mark.asyncio
    async def test_stream_completed_has_final_output(self) -> None:
        backend = DryRunBackend()
        spec = AgentSpec(name="bot", instructions="help")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

        final = None
        async for ev in backend.stream(spec, "test input", ctx):
            if ev.type == StreamEventType.RUN_COMPLETED:
                final = ev.data.get("final_output")

        assert final is not None
        assert "test input" in final
