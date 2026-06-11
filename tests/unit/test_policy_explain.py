"""Phase 24: Tests for policy explain / decision trace."""

from __future__ import annotations

import pytest

from agent_app.governance.policy import (
    ConfigurablePolicyEngine,
    DefaultPolicyEngine,
    PolicyAction,
    PolicyDecision,
    PolicyEvaluationContext,
)


def _ctx(**kwargs) -> PolicyEvaluationContext:
    defaults = dict(
        run_id="run_001",
        workflow_name="test_wf",
        workflow_type="handoff",
        agent_name="triage",
        tool_name="refund.request",
        risk_level="high",
        user_id="u1",
        tenant_id="t1",
        roles=["refund_operator"],
        permissions=["refund:create"],
        metadata={},
    )
    defaults.update(kwargs)
    return PolicyEvaluationContext(**defaults)


class TestPolicyDecisionTrace:
    def test_trace_creation(self):
        from agent_app.governance.policy import PolicyDecisionTrace
        from datetime import datetime, timezone

        trace = PolicyDecisionTrace(
            decision_id="dec_001",
            run_id="run_001",
            rule_name="require_approval_for_refunds",
            action=PolicyAction.REQUIRE_APPROVAL,
            reason="Refunds require approval",
            matched_conditions={"tool_name": "refund.request"},
            context_summary={"tool_name": "refund.request", "risk_level": "high"},
        )
        assert trace.decision_id == "dec_001"
        assert trace.rule_name == "require_approval_for_refunds"
        assert trace.action == PolicyAction.REQUIRE_APPROVAL
        assert trace.matched_conditions["tool_name"] == "refund.request"
        assert trace.context_summary["risk_level"] == "high"

    def test_trace_has_timestamp(self):
        from agent_app.governance.policy import PolicyDecisionTrace

        trace = PolicyDecisionTrace(
            decision_id="dec_002",
            action=PolicyAction.ALLOW,
        )
        assert trace.created_at is not None

    def test_context_summary_no_sensitive_args(self):
        from agent_app.governance.policy import PolicyDecisionTrace

        # context_summary should only contain safe fields, not full arguments
        trace = PolicyDecisionTrace(
            decision_id="dec_003",
            action=PolicyAction.DENY,
            context_summary={
                "tool_name": "dangerous.delete",
                "risk_level": "critical",
                "agent_name": "admin",
                "tenant_id": "t1",
            },
        )
        # Should NOT have full arguments
        assert "arguments" not in trace.context_summary
        assert "path" not in trace.context_summary
        # Should have safe fields
        assert trace.context_summary["tool_name"] == "dangerous.delete"


class TestDefaultPolicyEngineExplain:
    @pytest.mark.asyncio
    async def test_explain_returns_trace(self):
        engine = DefaultPolicyEngine()
        ctx = _ctx(permissions=["refund:create"])
        trace = await engine.explain(ctx)
        assert trace is not None
        assert trace.rule_name is None  # default engine has no named rules
        assert trace.action == PolicyAction.REQUIRE_APPROVAL
        assert "risk_level" in trace.context_summary

    @pytest.mark.asyncio
    async def test_explain_deny_missing_perms(self):
        engine = DefaultPolicyEngine()
        ctx = _ctx(
            permissions=[],
            metadata={"required_permissions": ["refund:create"]},
        )
        trace = await engine.explain(ctx)
        assert trace.action == PolicyAction.DENY
        assert "missing" in (trace.reason or "").lower() or "permission" in (trace.reason or "").lower()


class TestConfigurablePolicyEngineExplain:
    @pytest.mark.asyncio
    async def test_explain_matched_rule(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "refund_requires_approval",
                "when": {"tool_name": "refund.request"},
                "then": {"action": "require_approval", "reason": "Refunds need approval"},
            }
        ])
        ctx = _ctx(tool_name="refund.request")
        trace = await engine.explain(ctx)
        assert trace.rule_name == "refund_requires_approval"
        assert trace.action == PolicyAction.REQUIRE_APPROVAL
        assert trace.matched_conditions["tool_name"] == "refund.request"
        assert trace.reason == "Refunds need approval"

    @pytest.mark.asyncio
    async def test_explain_no_match_uses_default(self):
        engine = ConfigurablePolicyEngine(
            rules=[{"name": "r1", "when": {"tool_name": "x"}, "then": {"action": "deny"}}],
            default_action="allow",
        )
        ctx = _ctx(tool_name="order.query")
        trace = await engine.explain(ctx)
        assert trace.rule_name is None
        assert trace.action == PolicyAction.ALLOW

    @pytest.mark.asyncio
    async def test_explain_deny_trace(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "deny_dangerous",
                "when": {"tool_name": "dangerous.delete"},
                "then": {"action": "deny", "reason": "Blocked"},
            }
        ])
        ctx = _ctx(tool_name="dangerous.delete")
        trace = await engine.explain(ctx)
        assert trace.action == PolicyAction.DENY
        assert trace.reason == "Blocked"
        assert trace.matched_conditions["tool_name"] == "dangerous.delete"

    @pytest.mark.asyncio
    async def test_explain_audit_only_trace(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "audit_billing",
                "when": {"tool_name_prefix": "billing."},
                "then": {"action": "audit_only", "reason": "Compliance"},
            }
        ])
        ctx = _ctx(tool_name="billing.query")
        trace = await engine.explain(ctx)
        assert trace.action == PolicyAction.AUDIT_ONLY
        assert trace.matched_conditions["tool_name_prefix"] == "billing."

    @pytest.mark.asyncio
    async def test_explain_missing_roles(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "require_role",
                "when": {"tool_name": "refund.request", "missing_roles": ["admin"]},
                "then": {"action": "deny", "reason": "Missing admin role"},
            }
        ])
        ctx = _ctx(tool_name="refund.request", roles=["user"])
        trace = await engine.explain(ctx)
        assert trace.action == PolicyAction.DENY
        assert "admin" in (trace.reason or "")

    @pytest.mark.asyncio
    async def test_explain_matches_conditions_in_trace(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "strict",
                "when": {"workflow_type": "handoff", "target_agent": "refund"},
                "then": {"action": "require_approval"},
            }
        ])
        ctx = _ctx(workflow_type="handoff", target_agent="refund")
        trace = await engine.explain(ctx)
        assert trace.matched_conditions["workflow_type"] == "handoff"
        assert trace.matched_conditions["target_agent"] == "refund"

    @pytest.mark.asyncio
    async def test_explain_context_summary_safe_fields_only(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "r1",
                "when": {"tool_name": "refund.request"},
                "then": {"action": "allow"},
            }
        ])
        ctx = _ctx()
        trace = await engine.explain(ctx)
        # Context summary should have safe fields
        cs = trace.context_summary
        assert "tool_name" in cs or "risk_level" in cs or "tenant_id" in cs
        # Should NOT have raw arguments
        assert "arguments" not in cs
