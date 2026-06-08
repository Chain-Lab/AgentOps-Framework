"""Smoke tests for OpenAI multi-agent backend (Phase 11).

These tests require a real OPENAI_API_KEY and call the live OpenAI API.
They are opt-in only — skipped by default and never run in CI.

Run with:
    OPENAI_API_KEY=sk-... python -m pytest tests/smoke/ -m openai_smoke -v
"""

from __future__ import annotations

import os

import pytest

from agent_app.config.loader import build_app


pytestmark = [
    pytest.mark.openai_smoke,
    pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set — skipping smoke tests",
    ),
]


# ---------------------------------------------------------------------------
# Config paths
# ---------------------------------------------------------------------------

HANDOFF_CONFIG = "examples/customer_support/agentapp.yaml"
ORCHESTRATOR_CONFIG = "examples/research_assistant/agentapp.yaml"


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestMultiAgentSmoke:
    """Smoke tests for OpenAI multi-agent workflows with real API."""

    @pytest.mark.asyncio
    async def test_handoff_workflow_routes(self):
        """Handoff workflow routes to a specialist agent."""
        app = build_app(HANDOFF_CONFIG)
        result = await app.run(
            workflow="customer_support",
            input="I want a refund for my order",
        )
        assert result.status in ("completed", "interrupted")
        assert result.workflow_trace is not None
        assert result.workflow_trace.workflow_type == "handoff"

    @pytest.mark.asyncio
    async def test_orchestrator_workflow_delegates(self):
        """Orchestrator workflow delegates to specialist agents."""
        app = build_app(ORCHESTRATOR_CONFIG)
        result = await app.run(
            workflow="research_assistant",
            input="Research AI trends and write a summary",
        )
        assert result.status in ("completed", "interrupted")
        assert result.workflow_trace is not None
        assert result.workflow_trace.workflow_type == "orchestrator"
