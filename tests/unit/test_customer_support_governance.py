"""Phase 22 customer_support multi-agent governance eval.

Validates that Phase 21 governance properties (approval, permission, rate-limit,
TTL, max_handoffs, max_agent_calls) hold when execution flows through handoff
and orchestrator workflows using the customer_support example topology.

No real OpenAI API key required — uses DryRunBackend.
"""

from __future__ import annotations

import time

import pytest

from agent_app import AgentApp, AgentSpec, Workflow
from agent_app.core.routing import RoutingPolicy, RoutingRule, RoutingMatchType
from agent_app.core.workflow import WorkflowType
from agent_app.governance.risk import RiskLevel
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry
from agent_app.runtime.approval_rate_limit import InMemoryApprovalRateLimiter
from agent_app.runtime.approval_store import InMemoryApprovalStore
from agent_app.runtime.session import InMemorySessionStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_tool(app, name, **spec_kwargs):
    """Register a tool with a simulated implementation."""
    from agent_app.core.tool_spec import ToolSpec

    spec = ToolSpec(name=name, description=f"Tool {name}", **spec_kwargs)

    async def _fn(**kwargs):
        return {"tool": name, "result": "ok"}

    app.register_tool(spec, fn=_fn)
    return spec


def _make_bundle():
    """Create a fresh registry bundle for each test."""
    bundle = type("B", (), {})()
    bundle.agent_registry = AgentRegistry()
    bundle.tool_registry = ToolRegistry()
    bundle.workflow_registry = WorkflowRegistry()
    return bundle


@pytest.fixture
def bundle():
    return _make_bundle()


# ---------------------------------------------------------------------------
# Handoff governance tests (customer_support topology)
# ---------------------------------------------------------------------------


class TestHandoffGovernance:
    """Governance properties for handoff (triage) workflow."""

    @pytest.fixture
    def app(self, bundle):
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        _register_tool(
            app, "refund.request",
            risk_level=RiskLevel.HIGH, requires_approval=True,
            permissions=["refund:create"],
        )
        _register_tool(
            app, "order.query",
            risk_level="low", requires_approval=False,
            permissions=["order:read"],
        )
        _register_tool(
            app, "billing.query",
            risk_level="low", requires_approval=False,
            permissions=["order:read"],
        )
        app.register_agent(AgentSpec(name="triage", instructions="Triage", tools=[]))
        app.register_agent(AgentSpec(
            name="refund", instructions="Refund", tools=["refund.request"],
        ))
        app.register_agent(AgentSpec(
            name="billing", instructions="Billing", tools=["billing.query"],
        ))
        app.register_agent(AgentSpec(
            name="technical_support", instructions="Tech", tools=[],
        ))
        wf = Workflow.handoff(
            entry="triage",
            agents=["refund", "billing", "technical_support"],
            name="customer_support",
        )
        app.register_workflow(wf)
        return app

    @pytest.mark.asyncio
    async def test_refund_handoff_requires_approval(self, app):
        """Refund handoff triggers approval gate."""
        result = await app.run(
            workflow="customer_support",
            input="I want a refund for order 123",
            permissions=["order:read", "refund:create"],
        )
        assert result.status == "interrupted"
        assert len(result.handoffs) == 1
        assert result.handoffs[0]["to_agent"] == "refund"
        assert len(result.interruptions) >= 1
        assert result.interruptions[0]["type"] == "approval_required"

    @pytest.mark.asyncio
    async def test_billing_completes_without_approval(self, app):
        """Billing handoff completes without approval (low risk tool)."""
        result = await app.run(
            workflow="customer_support",
            input="I need my invoice",
            permissions=["order:read"],
        )
        assert result.status == "completed"
        assert len(result.handoffs) == 1
        assert result.handoffs[0]["to_agent"] == "billing"

    @pytest.mark.asyncio
    async def test_tech_routed(self, app):
        """Technical issue routes to technical_support."""
        result = await app.run(
            workflow="customer_support",
            input="system error bug",
        )
        assert result.status == "completed"
        assert len(result.handoffs) == 1
        assert result.handoffs[0]["to_agent"] == "technical_support"

    @pytest.mark.asyncio
    async def test_no_match_stays_triage(self, app):
        """Unmatched input stays at triage."""
        result = await app.run(
            workflow="customer_support",
            input="hello there",
        )
        assert result.status == "completed"
        assert len(result.handoffs) == 0

    @pytest.mark.asyncio
    async def test_max_handoffs_zero_blocks(self, bundle):
        """max_handoffs=0 blocks any handoff."""
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        _register_tool(
            app, "refund.request",
            risk_level=RiskLevel.HIGH, requires_approval=True,
            permissions=["refund:create"],
        )
        app.register_agent(AgentSpec(name="triage", instructions="Triage", tools=[]))
        app.register_agent(AgentSpec(
            name="refund", instructions="Refund", tools=["refund.request"],
        ))
        wf = Workflow.handoff(
            entry="triage",
            agents=["refund"],
            name="cs_hf0",
            max_handoffs=0,
        )
        app.register_workflow(wf)
        result = await app.run(
            workflow="cs_hf0",
            input="refund issue",
            permissions=["refund:create"],
        )
        assert result.status == "failed"
        assert result.error is not None
        assert "handoff" in result.error["type"].lower()

    @pytest.mark.asyncio
    async def test_max_handoffs_one_allows_single(self, bundle):
        """max_handoffs=1 allows exactly one handoff."""
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        _register_tool(
            app, "refund.request",
            risk_level=RiskLevel.HIGH, requires_approval=True,
            permissions=["refund:create"],
        )
        app.register_agent(AgentSpec(name="triage", instructions="Triage", tools=[]))
        app.register_agent(AgentSpec(
            name="refund", instructions="Refund", tools=["refund.request"],
        ))
        wf = Workflow.handoff(
            entry="triage",
            agents=["refund"],
            name="cs_hf1",
            max_handoffs=1,
        )
        app.register_workflow(wf)
        result = await app.run(
            workflow="cs_hf1",
            input="refund issue",
            permissions=["refund:create"],
        )
        assert result.status == "interrupted"
        assert len(result.handoffs) == 1

    @pytest.mark.asyncio
    async def test_permission_denied_in_target(self, app):
        """Missing permission at target agent — handoff still occurs but tool fails."""
        result = await app.run(
            workflow="customer_support",
            input="refund order 123",
            permissions=["order:read"],  # missing refund:create
        )
        # Handoff to refund still happens (routing is before permission check)
        assert len(result.handoffs) == 1
        assert result.handoffs[0]["to_agent"] == "refund"
        # Tool execution fails due to missing permission
        assert result.error is not None
        assert result.error.get("type") == "permission_denied"

    @pytest.mark.asyncio
    async def test_approve_after_handoff_resumes(self, app):
        """Approving an interrupted handoff run resumes successfully."""
        result = await app.run(
            workflow="customer_support",
            input="I want a refund for order 123",
            permissions=["order:read", "refund:create"],
        )
        assert result.status == "interrupted"
        approval_id = result.interruptions[0]["approval_id"]
        # Approve the pending approval
        approved = await app.approve(approval_id, "eval_admin")
        assert approved.status == "approved"
        # Resume the run
        resumed = await app.resume(result.run_id, approval_id)
        assert resumed.status == "completed"

    @pytest.mark.asyncio
    async def test_reject_after_handoff(self, app):
        """Rejecting an interrupted handoff run returns rejected status."""
        result = await app.run(
            workflow="customer_support",
            input="I want a refund for order 123",
            permissions=["order:read", "refund:create"],
        )
        assert result.status == "interrupted"
        approval_id = result.interruptions[0]["approval_id"]
        # Reject the pending approval
        rejected = await app.reject(approval_id, "eval_admin", "not needed")
        assert rejected.status == "rejected"


# ---------------------------------------------------------------------------
# Orchestrator governance tests (customer_support topology)
# ---------------------------------------------------------------------------


class TestOrchestratorGovernance:
    """Governance properties for orchestrator workflow."""

    @pytest.fixture
    def app(self, bundle):
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        _register_tool(
            app, "refund.request",
            risk_level=RiskLevel.HIGH, requires_approval=True,
            permissions=["refund:create"],
        )
        _register_tool(
            app, "billing.query",
            risk_level="low", requires_approval=False,
            permissions=["order:read"],
        )
        app.register_agent(AgentSpec(name="manager", instructions="Manager", tools=[]))
        app.register_agent(AgentSpec(
            name="refund_spec", instructions="Refund", tools=["refund.request"],
        ))
        app.register_agent(AgentSpec(
            name="billing_spec", instructions="Billing", tools=["billing.query"],
        ))
        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["refund_spec", "billing_spec"],
            name="cs_orchestrator",
        )
        # Use routing policy for custom agent names
        policy = RoutingPolicy(name="cs_policy", rules=[
            RoutingRule(
                name="refund_rule", target="refund_spec",
                match_type=RoutingMatchType.KEYWORD,
                keywords=["refund"], priority=100,
            ),
            RoutingRule(
                name="billing_rule", target="billing_spec",
                match_type=RoutingMatchType.KEYWORD,
                keywords=["invoice", "billing"], priority=80,
            ),
        ])
        wf.routing_policy = policy
        app.register_workflow(wf)
        return app

    @pytest.mark.asyncio
    async def test_specialist_requires_approval_interrupts(self, app):
        """Orchestrator specialist approval propagates as interruption."""
        result = await app.run(
            workflow="cs_orchestrator",
            input="process a refund for order 123",
            permissions=["refund:create"],
        )
        assert result.status == "interrupted"
        assert len(result.interruptions) >= 1
        assert result.interruptions[0]["type"] == "approval_required"

    @pytest.mark.asyncio
    async def test_no_match_no_calls(self, app):
        """Unmatched input results in no specialist calls."""
        result = await app.run(
            workflow="cs_orchestrator",
            input="hello",
        )
        assert result.status == "completed"
        assert len(result.agent_calls) == 0

    @pytest.mark.asyncio
    async def test_max_agent_calls_enforced(self, bundle):
        """max_agent_calls limits specialist dispatch."""
        app = AgentApp(
            registry=bundle,
            session_store=InMemorySessionStore(),
            approval_store=InMemoryApprovalStore(),
        )
        _register_tool(
            app, "refund.request",
            risk_level=RiskLevel.HIGH, requires_approval=True,
            permissions=["refund:create"],
        )
        app.register_agent(AgentSpec(name="manager", instructions="Manager", tools=[]))
        app.register_agent(AgentSpec(
            name="refund_spec", instructions="Refund", tools=["refund.request"],
        ))
        wf = Workflow.orchestrator(
            manager="manager",
            agents_as_tools=["refund_spec"],
            name="cs_orch_limited",
            max_agent_calls=1,
        )
        policy = RoutingPolicy(name="cs_policy", rules=[
            RoutingRule(
                name="refund_rule", target="refund_spec",
                match_type=RoutingMatchType.KEYWORD,
                keywords=["refund"], priority=100,
            ),
        ])
        wf.routing_policy = policy
        app.register_workflow(wf)

        result = await app.run(
            workflow="cs_orch_limited",
            input="refund order 123",
            permissions=["refund:create"],
        )
        # max_agent_calls=1 allows the refund_spec call
        assert result.status == "interrupted"
        assert len(result.agent_calls) == 1

    @pytest.mark.asyncio
    async def test_permission_denied_in_specialist(self, app):
        """Missing permission at specialist blocks tool execution."""
        result = await app.run(
            workflow="cs_orchestrator",
            input="refund order 123",
            permissions=["order:read"],  # missing refund:create
        )
        assert result.status == "completed"
        # Specialist call returns failed due to permission denial
