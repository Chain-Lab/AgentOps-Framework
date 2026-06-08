"""Shared pytest fixtures for unit tests."""

from __future__ import annotations

import pytest

from agent_app.core.agent_spec import AgentSpec
from agent_app.core.tool_spec import ToolSpec
from agent_app.core.workflow import Workflow
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry


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
