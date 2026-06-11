"""Phase 24: Tests for policy simulator."""

from __future__ import annotations

import pytest

from agent_app.governance.policy import (
    PolicyAction,
    PolicyDecision,
    PolicyEvaluationContext,
)
from agent_app.governance.policy_simulator import (
    PolicySimulationInput,
    PolicySimulationResult,
    PolicySimulator,
)


def _make_simulator(rules=None, default_action="allow"):
    from agent_app.governance.policy import ConfigurablePolicyEngine
    engine = ConfigurablePolicyEngine(rules=rules, default_action=default_action)
    return PolicySimulator(policy_engine=engine)


class TestPolicySimulationInput:
    def test_defaults(self):
        inp = PolicySimulationInput(tool_name="order.query")
        assert inp.tool_name == "order.query"
        assert inp.risk_level == "low"
        assert inp.roles == []
        assert inp.permissions == []
        assert inp.metadata == {}

    def test_full_input(self):
        inp = PolicySimulationInput(
            tool_name="refund.request",
            risk_level="high",
            workflow_type="handoff",
            agent_name="refund",
            target_agent="refund_support",
            user_id="u1",
            tenant_id="t1",
            roles=["refund_operator"],
            permissions=["refund:create"],
            metadata={"extra": "data"},
        )
        assert inp.risk_level == "high"
        assert inp.tenant_id == "t1"
        assert inp.roles == ["refund_operator"]


class TestPolicySimulationResult:
    def test_has_decision(self):
        from agent_app.governance.policy import PolicyDecisionTrace
        trace = PolicyDecisionTrace(decision_id="d1", action=PolicyAction.ALLOW)
        result = PolicySimulationResult(
            decision=PolicyDecision(action=PolicyAction.ALLOW),
            trace=trace,
        )
        assert result.decision.action == PolicyAction.ALLOW
        assert result.trace is not None


class TestPolicySimulator:
    # -- simulate allow --

    @pytest.mark.asyncio
    async def test_simulate_allow(self):
        sim = _make_simulator()
        inp = PolicySimulationInput(tool_name="order.query", risk_level="low")
        result = await sim.simulate(inp)
        assert result.decision.action == PolicyAction.ALLOW

    # -- simulate deny --

    @pytest.mark.asyncio
    async def test_simulate_deny(self):
        sim = _make_simulator(rules=[
            {
                "name": "deny_dangerous",
                "when": {"tool_name": "dangerous.delete"},
                "then": {"action": "deny", "reason": "Blocked"},
            }
        ])
        inp = PolicySimulationInput(tool_name="dangerous.delete")
        result = await sim.simulate(inp)
        assert result.decision.action == PolicyAction.DENY
        assert result.decision.allowed is False

    # -- simulate require_approval --

    @pytest.mark.asyncio
    async def test_simulate_require_approval(self):
        sim = _make_simulator(rules=[
            {
                "name": "require_refund_approval",
                "when": {"tool_name": "refund.request"},
                "then": {"action": "require_approval", "ttl_seconds": 1800},
            }
        ])
        inp = PolicySimulationInput(tool_name="refund.request", risk_level="high")
        result = await sim.simulate(inp)
        assert result.decision.action == PolicyAction.REQUIRE_APPROVAL
        assert result.decision.ttl_seconds == 1800

    # -- simulate audit_only --

    @pytest.mark.asyncio
    async def test_simulate_audit_only(self):
        sim = _make_simulator(rules=[
            {
                "name": "audit_billing",
                "when": {"tool_name": "billing.query"},
                "then": {"action": "audit_only", "reason": "Compliance"},
            }
        ])
        inp = PolicySimulationInput(tool_name="billing.query")
        result = await sim.simulate(inp)
        assert result.decision.action == PolicyAction.AUDIT_ONLY

    # -- simulate missing role --

    @pytest.mark.asyncio
    async def test_simulate_missing_role(self):
        sim = _make_simulator(rules=[
            {
                "name": "require_role",
                "when": {"tool_name": "admin.nuke", "missing_roles": ["admin"]},
                "then": {"action": "deny", "reason": "Missing admin role"},
            }
        ])
        inp = PolicySimulationInput(tool_name="admin.nuke", roles=["user"])
        result = await sim.simulate(inp)
        assert result.decision.action == PolicyAction.DENY

    # -- simulate missing permission --

    @pytest.mark.asyncio
    async def test_simulate_missing_permission(self):
        sim = _make_simulator(rules=[
            {
                "name": "require_perm",
                "when": {"tool_name": "data.export", "missing_permissions": ["data:export"]},
                "then": {"action": "deny", "reason": "Missing data:export"},
            }
        ])
        inp = PolicySimulationInput(tool_name="data.export", permissions=[])
        result = await sim.simulate(inp)
        assert result.decision.action == PolicyAction.DENY

    # -- does not execute tool --

    @pytest.mark.asyncio
    async def test_simulate_does_not_execute_tool(self):
        """Simulation should never call actual tool functions."""
        call_count = 0

        def fake_tool(**kwargs):
            nonlocal call_count
            call_count += 1
            return {"called": True}

        sim = _make_simulator(rules=[
            {
                "name": "allow_tool",
                "when": {"tool_name": "my.tool"},
                "then": {"action": "allow"},
            }
        ])
        inp = PolicySimulationInput(tool_name="my.tool")
        await sim.simulate(inp)
        assert call_count == 0  # tool was never called

    # -- does not create approval --

    @pytest.mark.asyncio
    async def test_simulate_does_not_create_approval(self):
        """Simulation must not create approval requests."""
        sim = _make_simulator(rules=[
            {
                "name": "require",
                "when": {"tool_name": "risky.tool"},
                "then": {"action": "require_approval"},
            }
        ])
        inp = PolicySimulationInput(tool_name="risky.tool")
        result = await sim.simulate(inp)
        assert result.decision.action == PolicyAction.REQUIRE_APPROVAL
        # The result is a PolicyDecision, not an ApprovalRequest — no side effect
        assert not hasattr(result.decision, "approval_id")

    # -- explain returns trace --

    @pytest.mark.asyncio
    async def test_explain_returns_trace(self):
        sim = _make_simulator(rules=[
            {
                "name": "my_rule",
                "when": {"tool_name": "test.tool"},
                "then": {"action": "deny", "reason": "Blocked by rule"},
            }
        ])
        inp = PolicySimulationInput(tool_name="test.tool")
        result = await sim.explain(inp)
        assert result.trace is not None
        assert result.trace.rule_name == "my_rule"
        assert result.trace.action == PolicyAction.DENY
        assert result.trace.reason == "Blocked by rule"
        assert result.trace.matched_conditions["tool_name"] == "test.tool"

    @pytest.mark.asyncio
    async def test_explain_trace_no_sensitive_data(self):
        sim = _make_simulator(rules=[
            {
                "name": "r1",
                "when": {"tool_name": "secret.query"},
                "then": {"action": "allow"},
            }
        ])
        inp = PolicySimulationInput(tool_name="secret.query")
        result = await sim.explain(inp)
        cs = result.trace.context_summary
        assert "arguments" not in cs
        assert "path" not in cs

    @pytest.mark.asyncio
    async def test_simulate_uses_default_action(self):
        sim = _make_simulator(
            rules=[{"name": "r1", "when": {"tool_name": "x"}, "then": {"action": "deny"}}],
            default_action="allow",
        )
        inp = PolicySimulationInput(tool_name="unknown.tool")
        result = await sim.simulate(inp)
        assert result.decision.action == PolicyAction.ALLOW

    @pytest.mark.asyncio
    async def test_simulate_with_default_deny(self):
        sim = _make_simulator(
            rules=[],
            default_action="deny",
        )
        inp = PolicySimulationInput(tool_name="anything")
        result = await sim.simulate(inp)
        assert result.decision.action == PolicyAction.DENY
