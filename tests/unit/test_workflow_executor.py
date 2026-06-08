"""Tests for WorkflowExecutor — handoff and orchestrator runtime (Phase 5)."""

import pytest

from agent_app.core.workflow import Workflow, WorkflowType
from agent_app.runtime.workflow_executor import (
    _route_handoff_heuristic as _route_handoff,
    _route_orchestrator_heuristic as _route_orchestrator,
    WorkflowExecutor,
)


# ---------------------------------------------------------------------------
# Routing unit tests
# ---------------------------------------------------------------------------

class TestHandoffRouting:
    def test_refund_keyword(self):
        target, reason = _route_handoff(
            "I want a refund for order 123", ["refund", "billing"], "triage"
        )
        assert target == "refund"
        assert "refund" in reason.lower()

    def test_billing_keyword(self):
        target, reason = _route_handoff(
            "I need my invoice", ["refund", "billing"], "triage"
        )
        assert target == "billing"

    def test_tech_keyword(self):
        target, reason = _route_handoff(
            "I have a system error", ["refund", "billing", "technical_support"], "triage"
        )
        assert target == "technical_support"

    def test_no_match_stays_at_entry(self):
        target, reason = _route_handoff(
            "hello there", ["refund", "billing"], "triage"
        )
        assert target == "triage"
        assert "no match" in reason.lower()

    def test_chinese_refund(self):
        target, reason = _route_handoff(
            "我要退款", ["refund", "billing"], "triage"
        )
        assert target == "refund"


class TestOrchestratorRouting:
    def test_research_keyword(self):
        matched = _route_orchestrator(
            "research AI trends", ["researcher", "analyst", "writer"]
        )
        assert "researcher" in matched

    def test_data_keyword(self):
        matched = _route_orchestrator(
            "analyze the data", ["researcher", "analyst", "writer"]
        )
        assert "analyst" in matched

    def test_write_keyword(self):
        matched = _route_orchestrator(
            "write a report", ["researcher", "analyst", "writer"]
        )
        assert "writer" in matched

    def test_mixed_keywords(self):
        matched = _route_orchestrator(
            "research and write", ["researcher", "analyst", "writer"]
        )
        assert "researcher" in matched
        assert "writer" in matched

    def test_no_match(self):
        matched = _route_orchestrator(
            "hello", ["researcher", "analyst", "writer"]
        )
        assert matched == []


# ---------------------------------------------------------------------------
# WorkflowExecutor integration tests
# ---------------------------------------------------------------------------

class TestWorkflowExecutorHandoff:
    @pytest.fixture
    def app(self):
        from agent_app import AgentApp, AgentSpec, ToolSpec, Workflow
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
        # Register agents
        app.register_agent(AgentSpec(name="triage", instructions="Triage", tools=[]))
        app.register_agent(AgentSpec(name="refund", instructions="Refund", tools=[]))
        app.register_agent(AgentSpec(name="billing", instructions="Billing", tools=[]))
        app.register_agent(AgentSpec(name="technical_support", instructions="Tech", tools=[]))
        # Register workflow
        wf = Workflow.handoff(
            entry="triage", agents=["refund", "billing", "technical_support"], name="test"
        )
        app.register_workflow(wf)
        return app

    @pytest.mark.asyncio
    async def test_refund_handoff(self, app):
        result = await app.run(
            workflow="test",
            input="I want a refund for order 123",
            permissions=["order:read", "refund:create"],
        )
        assert result.status == "completed"
        assert len(result.handoffs) == 1
        assert result.handoffs[0]["from_agent"] == "triage"
        assert result.handoffs[0]["to_agent"] == "refund"

    @pytest.mark.asyncio
    async def test_billing_handoff(self, app):
        result = await app.run(
            workflow="test",
            input="I need my invoice",
            permissions=["order:read"],
        )
        assert result.status == "completed"
        assert len(result.handoffs) == 1
        assert result.handoffs[0]["to_agent"] == "billing"

    @pytest.mark.asyncio
    async def test_tech_handoff(self, app):
        result = await app.run(
            workflow="test",
            input="system error bug",
        )
        assert result.status == "completed"
        assert len(result.handoffs) == 1
        assert result.handoffs[0]["to_agent"] == "technical_support"

    @pytest.mark.asyncio
    async def test_no_match_stays_triage(self, app):
        result = await app.run(
            workflow="test",
            input="hello",
        )
        assert result.status == "completed"
        # No handoff occurs when input doesn't match any candidate
        assert len(result.handoffs) == 0
        assert "triage" in (result.final_output or "")

    @pytest.mark.asyncio
    async def test_unknown_target_agent_fails(self, app):
        """Handoff to a non-existent agent should return failed."""
        # Register workflow that includes a non-existent agent in candidates
        wf = Workflow.handoff(
            entry="triage",
            agents=["ghost_agent"],  # doesn't exist in registry
            name="bad",
        )
        app.register_workflow(wf)
        result = await app.run(
            workflow="bad",
            input="ghost",  # matches 'ghost_agent' via name part matching
        )
        assert result.status == "failed"
        assert "ghost_agent" in str(result.error)


class TestWorkflowExecutorOrchestrator:
    @pytest.fixture
    def app(self):
        from agent_app import AgentApp, AgentSpec, Workflow
        from agent_app.registry.agent_registry import AgentRegistry
        from agent_app.registry.tool_registry import ToolRegistry
        from agent_app.registry.workflow_registry import WorkflowRegistry
        from agent_app.runtime.session import InMemorySessionStore

        bundle = type("B", (), {})()
        bundle.agent_registry = AgentRegistry()
        bundle.tool_registry = ToolRegistry()
        bundle.workflow_registry = WorkflowRegistry()
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
        )
        for name in ["manager", "researcher", "analyst", "writer"]:
            app.register_agent(AgentSpec(name=name, instructions=f"{name} agent", tools=[]))
        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["researcher", "analyst", "writer"],
            name="test",
        )
        app.register_workflow(wf)
        return app

    @pytest.mark.asyncio
    async def test_research_calls_researcher(self, app):
        result = await app.run(
            workflow="test",
            input="research the latest AI trends",
        )
        assert result.status == "completed"
        names = {c["agent_name"] for c in result.agent_calls}
        assert "researcher" in names

    @pytest.mark.asyncio
    async def test_data_calls_analyst(self, app):
        result = await app.run(
            workflow="test",
            input="analyze the sales data",
        )
        assert result.status == "completed"
        names = {c["agent_name"] for c in result.agent_calls}
        assert "analyst" in names

    @pytest.mark.asyncio
    async def test_write_calls_writer(self, app):
        result = await app.run(
            workflow="test",
            input="write a summary report",
        )
        assert result.status == "completed"
        names = {c["agent_name"] for c in result.agent_calls}
        assert "writer" in names

    @pytest.mark.asyncio
    async def test_mixed_calls_multiple(self, app):
        result = await app.run(
            workflow="test",
            input="research AI trends and write a report",
        )
        assert result.status == "completed"
        names = {c["agent_name"] for c in result.agent_calls}
        assert "researcher" in names
        assert "writer" in names

    @pytest.mark.asyncio
    async def test_no_match_no_agent_calls(self, app):
        result = await app.run(
            workflow="test",
            input="hello",
        )
        assert result.status == "completed"
        assert len(result.agent_calls) == 0
