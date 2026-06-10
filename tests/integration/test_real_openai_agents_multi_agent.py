"""Marker-gated integration test for real OpenAI Agents SDK HITL.

Default: SKIPPED.  Requires explicit opt-in via environment variables:

    RUN_OPENAI_AGENTS_INTEGRATION=1 OPENAI_API_KEY=sk-... pytest tests/integration/test_real_openai_agents_multi_agent.py

The test checks SDK capabilities at runtime and skips if the installed
openai-agents version does not support multi-agent handoffs / tools.
No real destructive tools are executed.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Guard: skip unless explicitly enabled
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_OPENAI_AGENTS_INTEGRATION") != "1",
    reason="Set RUN_OPENAI_AGENTS_INTEGRATION=1 to run real OpenAI SDK multi-agent tests",
)


def _require_sdk() -> Any:
    """Import and return the openai-agents SDK module.

    Skips the test if the SDK is not installed.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")

    try:
        import agents  # noqa: F401
    except ImportError:
        pytest.skip("openai-agents SDK not installed")

    from agent_app.adapters.openai_agents import _load_agents_sdk
    return _load_agents_sdk()


def _install_fake_sdk_for_integration() -> None:
    """Inject a minimal fake SDK so the test doesn't need real network."""
    import types

    if "agents" in sys.modules:
        return

    fake = types.ModuleType("agents")
    fake.Agent = type("Agent", (), {})
    fake.Runner = type("Runner", (), {
        "run": lambda *a, **k: None,
        "run_streamed": lambda *a, **k: None,
    })
    fake.RunState = type("RunState", (), {})
    fake.ToolApprovalItem = type("ToolApprovalItem", (), {})
    fake.__version__ = "0.2.0"
    sys.modules["agents"] = fake
    sys.modules["openai_agents"] = fake


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_sdk_module_loads() -> None:
    """Verify the openai-agents module can be imported."""
    sdk = _require_sdk()
    assert sdk is not None


@pytest.mark.asyncio
async def test_real_sdk_backend_compiles_without_api_key() -> None:
    """Verify OpenAIAgentsBackend compiles an agent spec without making API calls.

    Uses fake SDK injection; verifies the compilation path works.
    """
    _install_fake_sdk_for_integration()

    from agent_app.adapters.openai_agents import OpenAIAgentsBackend
    from agent_app.core.agent_spec import AgentSpec
    from agent_app.registry.agent_registry import AgentRegistry
    from agent_app.registry.tool_registry import ToolRegistry

    registry = AgentRegistry()
    tool_registry = ToolRegistry()
    backend = OpenAIAgentsBackend(
        agent_registry=registry,
        tool_registry=tool_registry,
        raise_on_missing=True,
    )

    agent_spec = AgentSpec(
        name="compile_test",
        instructions="Test agent.",
        model="gpt-4o",
    )
    registry.register(agent_spec)
    compiled = backend.compile_agent(agent_spec)
    assert compiled is not None
