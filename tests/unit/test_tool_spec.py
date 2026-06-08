"""Tests for ToolSpec model."""

from agent_app.core.tool_spec import ToolSpec


class TestToolSpec:
    def test_create_minimal(self) -> None:
        spec = ToolSpec(name="order.query", description="Query order")
        assert spec.name == "order.query"
        assert spec.namespace == "order"
        assert spec.risk_level == "low"
        assert spec.requires_approval is False
        assert spec.permissions == []
        assert spec.timeout_seconds == 30
        assert spec.audit_enabled is True

    def test_namespace_auto_derived(self) -> None:
        spec = ToolSpec(name="refund.request", description="Refund")
        assert spec.namespace == "refund"

    def test_single_word_name(self) -> None:
        spec = ToolSpec(name="search", description="Search")
        assert spec.namespace == "search"  # falls back to name

    def test_explicit_namespace(self) -> None:
        spec = ToolSpec(
            name="query", description="Query", namespace="custom"
        )
        assert spec.namespace == "custom"

    def test_high_risk_tool(self) -> None:
        spec = ToolSpec(
            name="refund.request",
            description="Create refund",
            risk_level="high",
            requires_approval=True,
            permissions=["refund:create"],
            timeout_seconds=60,
        )
        assert spec.risk_level == "high"
        assert spec.requires_approval is True
        assert "refund:create" in spec.permissions
        assert spec.timeout_seconds == 60
