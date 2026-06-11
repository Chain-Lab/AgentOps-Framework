"""Phase 24: Tests for CLI policy commands."""

from __future__ import annotations

import pytest

from agent_app.governance.policy_validation import validate_policy_config
from agent_app.governance.policy_simulator import PolicySimulator


class TestCLIPolicyValidate:
    def test_validate_success(self, tmp_path, capsys):
        """agentapp policy validate — valid config exits 0."""
        from agent_app.config.schema import PolicyEngineConfig
        from agent_app.governance.policy import ConfigurablePolicyEngine

        cfg = PolicyEngineConfig(
            enabled=True,
            default_action="allow",
            rules=[
                {
                    "name": "r1",
                    "when": {"tool_name": "order.query"},
                    "then": {"action": "allow"},
                }
            ],
        )
        result = validate_policy_config(cfg)
        assert result.valid is True

    def test_validate_failure_exits_nonzero(self):
        """validate with errors → valid=False."""
        result = validate_policy_config({
            "enabled": True,
            "rules": [
                {
                    "name": "bad",
                    "when": {"tool_name": "x", "tool_name_prefix": "y"},
                    "then": {"action": "nonexistent"},
                }
            ],
        })
        assert result.valid is False

    def test_validate_warning_does_not_fail(self):
        """validate with only warnings → valid=True."""
        result = validate_policy_config({"enabled": True, "rules": []})
        assert result.valid is True
        assert any(i.level == "warning" for i in result.issues)


class TestCLIPolicySimulate:
    @pytest.mark.asyncio
    async def test_simulate_outputs_action(self):
        from agent_app.governance.policy import ConfigurablePolicyEngine

        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "require_refund_approval",
                "when": {"tool_name": "refund.request"},
                "then": {"action": "require_approval", "reason": "Refunds need approval"},
            }
        ])
        sim = PolicySimulator(policy_engine=engine)
        from agent_app.governance.policy_simulator import PolicySimulationInput
        inp = PolicySimulationInput(
            tool_name="refund.request",
            risk_level="high",
            role="refund_operator",
            permission="refund:create",
            tenant_id="eval_tenant",
        )
        result = await sim.simulate(inp)
        assert result.decision.action.value == "require_approval"
        assert result.decision.metadata.get("rule_name") == "require_refund_approval"

    @pytest.mark.asyncio
    async def test_simulate_outputs_reason(self):
        from agent_app.governance.policy import ConfigurablePolicyEngine

        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "r1",
                "when": {"tool_name": "x"},
                "then": {"action": "deny", "reason": "Not allowed"},
            }
        ])
        sim = PolicySimulator(policy_engine=engine)
        from agent_app.governance.policy_simulator import PolicySimulationInput
        inp = PolicySimulationInput(tool_name="x")
        result = await sim.simulate(inp)
        assert result.decision.reason == "Not allowed"


class TestCLIPolicyExplain:
    @pytest.mark.asyncio
    async def test_explain_outputs_matched_rule(self):
        from agent_app.governance.policy import ConfigurablePolicyEngine

        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "require_approval_for_refunds",
                "when": {"tool_name": "refund.request"},
                "then": {"action": "require_approval", "reason": "Refunds need approval"},
            }
        ])
        sim = PolicySimulator(policy_engine=engine)
        from agent_app.governance.policy_simulator import PolicySimulationInput
        inp = PolicySimulationInput(
            tool_name="refund.request",
            risk_level="high",
            tenant_id="eval_tenant",
        )
        result = await sim.explain(inp)
        assert result.trace is not None
        assert result.trace.rule_name == "require_approval_for_refunds"
        assert result.trace.action.value == "require_approval"
        assert result.trace.reason == "Refunds need approval"
        assert result.trace.matched_conditions["tool_name"] == "refund.request"
        assert result.trace.context_summary["tool_name"] == "refund.request"
        assert result.trace.context_summary["risk_level"] == "high"

    @pytest.mark.asyncio
    async def test_explain_no_match(self):
        from agent_app.governance.policy import ConfigurablePolicyEngine

        engine = ConfigurablePolicyEngine(
            rules=[{"name": "r1", "when": {"tool_name": "x"}, "then": {"action": "deny"}}],
            default_action="allow",
        )
        sim = PolicySimulator(policy_engine=engine)
        from agent_app.governance.policy_simulator import PolicySimulationInput
        inp = PolicySimulationInput(tool_name="unknown.tool")
        result = await sim.explain(inp)
        assert result.trace.rule_name is None
        assert result.trace.action.value == "allow"
