"""Shared pytest fixtures for unit tests."""

from __future__ import annotations

import asyncio

import pytest

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.tool_spec import ToolSpec
from agent_app.core.workflow import Workflow
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry


def _run_async(coro):
    """Run an async coroutine from synchronous test code.

    Uses a fresh event loop per call to avoid 'no current event loop' errors
    when tests run in batch mode (Python 3.12+ deprecates get_event_loop()
    when no loop is running).
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def agent_registry() -> AgentRegistry:
    return AgentRegistry()


@pytest.fixture
def tool_registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def workflow_registry() -> WorkflowRegistry:
    return WorkflowRegistry()


@pytest.fixture
def sample_agent_spec() -> AgentSpec:
    return AgentSpec(
        name="support",
        description="Customer support agent",
        model="gpt-4o",
        instructions="You are a helpful support assistant.",
        tools=["order.query"],
    )


@pytest.fixture
def sample_tool_spec() -> ToolSpec:
    return ToolSpec(
        name="order.query",
        description="Query order details",
        risk_level="low",
        permissions=["order:read"],
    )


@pytest.fixture
def policy_console_app():
    """Create a fresh FastAPI app for policy console testing.

    Returns a new AgentApp, FastAPI app, and TestClient each time,
    ensuring full isolation between tests when run in batch mode.
    """
    from agent_app import AgentApp
    from agent_app.governance.approval import InMemoryApprovalStore
    from agent_app.governance.audit import InMemoryAuditLogger
    from agent_app.adapters.fastapi import create_fastapi_app

    ar = AgentRegistry()
    tr = ToolRegistry()
    wr = WorkflowRegistry()
    app = AgentApp(
        registry=type("B", (), {"agent_registry": ar, "tool_registry": tr, "workflow_registry": wr})()
    )
    app.agent_registry = ar
    app.tool_registry = tr
    app.workflow_registry = wr
    app.approval_store = InMemoryApprovalStore()
    app.audit_logger = InMemoryAuditLogger()
    return create_fastapi_app(app)
