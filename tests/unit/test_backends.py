"""Tests for DryRunBackend and OpenAIAgentsBackend."""

import sys
from unittest.mock import patch

import pytest

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.context import RunContext
from agent_app.runtime.backends import DryRunBackend


class TestDryRunBackend:
    @pytest.mark.asyncio
    async def test_dry_run_echoes_input(self) -> None:
        backend = DryRunBackend()
        spec = AgentSpec(name="bot", instructions="help")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        result = await backend.run(spec, "hello", ctx)
        assert result.status == "completed"
        assert "hello" in result.final_output
        assert "bot" in result.final_output
        assert result.run_id == "r1"

    @pytest.mark.asyncio
    async def test_dry_run_with_tools(self) -> None:
        backend = DryRunBackend()
        spec = AgentSpec(name="bot", instructions="help", tools=["order.query"])
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

        class FakeTool:
            name = "order.query"

        result = await backend.run(spec, "test", ctx, tools=[FakeTool()])
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["tool"] == "order.query"
        assert result.tool_calls[0]["status"] == "dry_run"


class TestOpenAIAgentsBackend:
    @pytest.mark.asyncio
    async def test_missing_openai_agents_raises_helpful_error(self) -> None:
        """Without openai-agents installed, run() should raise a clear ImportError."""
        from agent_app.adapters.openai_agents import OpenAIAgentsBackend

        backend = OpenAIAgentsBackend()
        spec = AgentSpec(name="bot", instructions="help")
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")

        # Mock the agents module as unavailable
        with patch.dict(sys.modules, {"agents": None}):
            with pytest.raises(RuntimeError, match="OpenAI Agents"):
                await backend.run(spec, "input", ctx)
