"""Tests for Registry base class and concrete registries."""

import pytest

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.tool_spec import ToolSpec
from agent_app.core.workflow import Workflow
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry


class TestAgentRegistry:
    def test_register_and_get(self) -> None:
        reg = AgentRegistry()
        spec = AgentSpec(name="bot", instructions="Help")
        reg.register("bot", spec)
        assert reg.get("bot") is spec

    def test_exists(self) -> None:
        reg = AgentRegistry()
        assert not reg.exists("bot")
        reg.register("bot", AgentSpec(name="bot", instructions="Help"))
        assert reg.exists("bot")

    def test_list(self) -> None:
        reg = AgentRegistry()
        reg.register("a", AgentSpec(name="a", instructions="A"))
        reg.register("b", AgentSpec(name="b", instructions="B"))
        assert reg.list() == ["a", "b"]

    def test_duplicate_raises(self) -> None:
        reg = AgentRegistry()
        reg.register("bot", AgentSpec(name="bot", instructions="Help"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register("bot", AgentSpec(name="bot", instructions="Help"))

    def test_name_mismatch_raises(self) -> None:
        reg = AgentRegistry()
        with pytest.raises(ValueError, match="does not match"):
            reg.register("other", AgentSpec(name="bot", instructions="Help"))

    def test_get_missing_raises(self) -> None:
        reg = AgentRegistry()
        with pytest.raises(KeyError, match="not registered"):
            reg.get("nonexistent")

    def test_unregister(self) -> None:
        reg = AgentRegistry()
        reg.register("bot", AgentSpec(name="bot", instructions="Help"))
        reg.unregister("bot")
        assert not reg.exists("bot")

    def test_clear(self) -> None:
        reg = AgentRegistry()
        reg.register("a", AgentSpec(name="a", instructions="A"))
        reg.clear()
        assert reg.list() == []

    def test_invalid_name_type(self) -> None:
        reg = AgentRegistry()
        with pytest.raises(TypeError):
            reg.register(123, AgentSpec(name="bot", instructions="Help"))  # type: ignore[arg-type]


class TestToolRegistry:
    def test_register_and_get_spec(self) -> None:
        reg = ToolRegistry()
        spec = ToolSpec(name="order.query", description="Query")
        reg.register("order.query", spec)
        assert reg.get_spec("order.query") is spec

    def test_register_with_fn(self) -> None:
        reg = ToolRegistry()
        spec = ToolSpec(name="search", description="Search")
        async def search_fn(q: str) -> dict:
            return {}
        reg.register("search", spec, fn=search_fn)
        assert reg.get_fn("search") is search_fn

    def test_duplicate_raises(self) -> None:
        reg = ToolRegistry()
        reg.register("t", ToolSpec(name="t", description="T"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register("t", ToolSpec(name="t", description="T v2"))

    def test_name_mismatch_raises(self) -> None:
        reg = ToolRegistry()
        with pytest.raises(ValueError, match="does not match"):
            reg.register("other", ToolSpec(name="t", description="T"))


class TestWorkflowRegistry:
    def test_register_and_get(self) -> None:
        reg = WorkflowRegistry()
        wf = Workflow.single(agent="support")
        reg.register("default", wf)
        assert reg.get("default") is wf

    def test_list(self) -> None:
        reg = WorkflowRegistry()
        reg.register("a", Workflow.single(agent="a", name="a"))
        reg.register("b", Workflow.single(agent="b", name="b"))
        assert reg.list() == ["a", "b"]
