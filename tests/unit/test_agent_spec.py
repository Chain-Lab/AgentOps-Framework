"""Tests for AgentSpec model."""

from agent_app.core.agent_spec import AgentSpec


class TestAgentSpec:
    def test_create_minimal(self) -> None:
        spec = AgentSpec(name="bot", instructions="Be helpful.")
        assert spec.name == "bot"
        assert spec.instructions == "Be helpful."
        assert spec.tools == []
        assert spec.model is None
        assert spec.raw_agent_kwargs == {}

    def test_create_full(self) -> None:
        spec = AgentSpec(
            name="support",
            description="Support agent",
            model="gpt-4o",
            instructions="You are helpful.",
            tools=["order.query"],
            handoffs=["billing"],
            guardrails=["pii_check"],
            metadata={"owner": "team-a"},
            raw_agent_kwargs={"temperature": 0.5},
        )
        assert spec.model == "gpt-4o"
        assert spec.tools == ["order.query"]
        assert spec.handoffs == ["billing"]
        assert spec.guardrails == ["pii_check"]
        assert spec.metadata["owner"] == "team-a"
        assert spec.raw_agent_kwargs["temperature"] == 0.5

    def test_name_required(self) -> None:
        import pytest
        with pytest.raises(Exception):  # ValidationError
            AgentSpec(instructions="hello")
