"""Smoke tests for OpenAI native HITL mode.

These tests require a real OPENAI_API_KEY and call the live OpenAI API.
They are opt-in only — skipped by default and never run in CI.

Run with:
    OPENAI_API_KEY=sk-... python -m pytest tests/smoke/ -m openai_smoke -v
"""

from __future__ import annotations

import os
from unittest.mock import patch

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
# Helper: build a native HITL app with real OpenAI backend
# ---------------------------------------------------------------------------

NATIVE_CONFIG_PATH = "examples/openai_basic/agentapp.native.yaml"


@pytest.fixture()
def native_app():
    """Build an AgentApp with native HITL mode and real OpenAI backend."""
    return build_app(NATIVE_CONFIG_PATH)


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestNativeHITLSmoke:
    """Smoke tests that call the real OpenAI API with native HITL mode."""

    @pytest.mark.asyncio
    async def test_native_mode_completes_simple_query(self, native_app):
        """A simple query without high-risk tools should complete."""
        result = await native_app.run(
            agent="assistant",
            input="What is 2 + 2?",
        )
        assert result.status == "completed"
        assert result.final_output is not None

    @pytest.mark.asyncio
    async def test_native_mode_detects_high_risk_tool(self, native_app):
        """Requesting a high-risk tool should trigger interruption."""
        result = await native_app.run(
            agent="assistant",
            input="Delete the test account",
        )
        # Should be interrupted due to account.delete requiring approval
        assert result.status == "interrupted"
        assert len(result.interruptions) > 0
        assert result.interruptions[0]["type"] == "approval_required"

    @pytest.mark.asyncio
    async def test_native_mode_preserves_backend_state(self, native_app):
        """Interrupted runs should have serialized backend_state."""
        result = await native_app.run(
            agent="assistant",
            input="Delete the test account",
        )
        assert result.status == "interrupted"
        assert result.backend_state is not None
        assert "serialization" in result.backend_state
        assert "value" in result.backend_state
