"""Tests for AgentApp governance lifecycle — approve, reject, resume."""

import pytest

from agent_app import AgentApp, AgentSpec, ToolSpec, Workflow
from agent_app.core.context import RunContext
from agent_app.governance.approval import ApprovalStatus
from agent_app.governance.risk import RiskLevel
from agent_app.registry.agent_registry import AgentRegistry
from agent_app.registry.tool_registry import ToolRegistry
from agent_app.registry.workflow_registry import WorkflowRegistry
from agent_app.runtime.approval_store import InMemoryApprovalStore
from agent_app.runtime.session import InMemorySessionStore


class _Bundle:
    def __init__(self):
        self.agent_registry = AgentRegistry()
        self.tool_registry = ToolRegistry()
        self.workflow_registry = WorkflowRegistry()


@pytest.fixture
def app_with_governance():
    """Create an AgentApp with isolated registries (no global pollution)."""
    bundle = _Bundle()
    store = InMemorySessionStore()
    approval_store = InMemoryApprovalStore()
    app = AgentApp(
        registry=bundle,
        session_store=store,
        approval_store=approval_store,
    )
    return app


def _register_tool(app, name, **spec_kwargs):
    """Register a tool directly into the app's isolated registry."""
    spec = ToolSpec(name=name, description=f"Tool {name}", **spec_kwargs)

    async def _fn(**kwargs):
        return {"tool": name, "result": "ok"}

    app.register_tool(spec, fn=_fn)
    return spec


class TestAgentAppGovernance:
    @pytest.mark.asyncio
    async def test_order_query_completes(self, app_with_governance) -> None:
        """Low-risk tool: should complete normally."""
        _register_tool(app_with_governance, "order.query", risk_level="low")
        app_with_governance.register_agent(
            AgentSpec(name="support", instructions="Help", tools=["order.query"])
        )
        app_with_governance.register_workflow(
            Workflow.single(agent="support", name="cs")
        )

        result = await app_with_governance.run(
            agent="support",
            input="I want to check my order 123",
            user_id="u1",
            tenant_id="t1",
            permissions=["order:read"],
        )
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_refund_with_permission_completes(self, app_with_governance) -> None:
        """With refund:create permission, should execute the tool."""
        _register_tool(
            app_with_governance, "refund.request",
            risk_level="high", permissions=["refund:create"],
        )
        app_with_governance.register_agent(
            AgentSpec(
                name="support", instructions="Help",
                tools=["refund.request"],
            )
        )
        app_with_governance.register_workflow(
            Workflow.single(agent="support", name="cs")
        )

        result = await app_with_governance.run(
            agent="support",
            input="I want to refund order 123",
            user_id="u1",
            tenant_id="t1",
            permissions=["refund:create"],
        )
        # With correct perms, tool executes (may complete or be interrupted)
        assert result.status in ("completed", "interrupted")

    @pytest.mark.asyncio
    async def test_refund_without_permission_fails(self, app_with_governance) -> None:
        """Without refund:create permission, should fail with permission_denied."""
        _register_tool(
            app_with_governance, "refund.request",
            risk_level="high", permissions=["refund:create"],
        )
        app_with_governance.register_agent(
            AgentSpec(
                name="support", instructions="Help",
                tools=["refund.request"],
            )
        )
        app_with_governance.register_workflow(
            Workflow.single(agent="support", name="cs")
        )

        result = await app_with_governance.run(
            agent="support",
            input="I want to refund order 123",
            user_id="u1",
            tenant_id="t1",
            permissions=[],  # No permissions
        )
        assert result.status == "failed"
        assert result.error is not None
        assert result.error["type"] == "permission_denied"

    @pytest.mark.asyncio
    async def test_high_risk_triggers_approval(self, app_with_governance) -> None:
        """High-risk tool with requires_approval=True and no perms required → interrupted."""
        _register_tool(
            app_with_governance, "test.high",
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            permissions=[],
        )
        app_with_governance.register_agent(
            AgentSpec(
                name="support", instructions="Help",
                tools=["test.high"],
            )
        )
        app_with_governance.register_workflow(
            Workflow.single(agent="support", name="cs")
        )

        result = await app_with_governance.run(
            agent="support",
            input="do a high risk action",
            user_id="u1",
            tenant_id="t1",
        )
        assert result.status == "interrupted"
        assert len(result.interruptions) == 1
        assert result.interruptions[0]["type"] == "approval_required"
        assert result.interruptions[0]["tool_name"] == "test.high"
        approval_id = result.interruptions[0]["approval_id"]

        pending = await app_with_governance.list_pending_approvals()
        assert len(pending) == 1
        assert pending[0].approval_id == approval_id

    @pytest.mark.asyncio
    async def test_approve_changes_status(self, app_with_governance) -> None:
        _register_tool(
            app_with_governance, "test.high",
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            permissions=[],
        )
        app_with_governance.register_agent(
            AgentSpec(name="support", instructions="Help", tools=["test.high"])
        )
        app_with_governance.register_workflow(
            Workflow.single(agent="support", name="cs")
        )

        result = await app_with_governance.run(
            agent="support",
            input="do a high risk action",
            user_id="u1",
            tenant_id="t1",
        )
        approval_id = result.interruptions[0]["approval_id"]

        updated = await app_with_governance.approve(
            approval_id=approval_id,
            approved_by="manager_001",
            reason="Customer verified",
        )
        assert updated.status == ApprovalStatus.APPROVED
        assert updated.resolved_by == "manager_001"

    @pytest.mark.asyncio
    async def test_reject_changes_status(self, app_with_governance) -> None:
        _register_tool(
            app_with_governance, "test.high",
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            permissions=[],
        )
        app_with_governance.register_agent(
            AgentSpec(name="support", instructions="Help", tools=["test.high"])
        )
        app_with_governance.register_workflow(
            Workflow.single(agent="support", name="cs")
        )

        result = await app_with_governance.run(
            agent="support",
            input="do a high risk action",
            user_id="u1",
            tenant_id="t1",
        )
        approval_id = result.interruptions[0]["approval_id"]

        updated = await app_with_governance.reject(
            approval_id=approval_id,
            rejected_by="manager_001",
            reason="Outside policy window",
        )
        assert updated.status == ApprovalStatus.REJECTED
        assert updated.reason == "Outside policy window"

    @pytest.mark.asyncio
    async def test_resume_approved_returns_completed(self, app_with_governance) -> None:
        _register_tool(
            app_with_governance, "test.high",
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            permissions=[],
        )
        app_with_governance.register_agent(
            AgentSpec(name="support", instructions="Help", tools=["test.high"])
        )
        app_with_governance.register_workflow(
            Workflow.single(agent="support", name="cs")
        )

        result = await app_with_governance.run(
            agent="support",
            input="do a high risk action",
            user_id="u1",
            tenant_id="t1",
        )
        approval_id = result.interruptions[0]["approval_id"]
        await app_with_governance.approve(approval_id, "manager")

        resumed = await app_with_governance.resume(
            run_id=result.run_id,
            approval_id=approval_id,
        )
        assert resumed.status == "completed"
        assert "approved" in resumed.final_output.lower()

    @pytest.mark.asyncio
    async def test_resume_rejected_returns_completed_with_message(self, app_with_governance) -> None:
        _register_tool(
            app_with_governance, "test.high",
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            permissions=[],
        )
        app_with_governance.register_agent(
            AgentSpec(name="support", instructions="Help", tools=["test.high"])
        )
        app_with_governance.register_workflow(
            Workflow.single(agent="support", name="cs")
        )

        result = await app_with_governance.run(
            agent="support",
            input="do a high risk action",
            user_id="u1",
            tenant_id="t1",
        )
        approval_id = result.interruptions[0]["approval_id"]
        await app_with_governance.reject(approval_id, "manager", "Not allowed")

        resumed = await app_with_governance.resume(
            run_id=result.run_id,
            approval_id=approval_id,
        )
        assert resumed.status == "completed"
        assert "rejected" in resumed.final_output.lower()

    @pytest.mark.asyncio
    async def test_resume_pending_returns_interrupted(self, app_with_governance) -> None:
        _register_tool(
            app_with_governance, "test.high",
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            permissions=[],
        )
        app_with_governance.register_agent(
            AgentSpec(name="support", instructions="Help", tools=["test.high"])
        )
        app_with_governance.register_workflow(
            Workflow.single(agent="support", name="cs")
        )

        result = await app_with_governance.run(
            agent="support",
            input="do a high risk action",
            user_id="u1",
            tenant_id="t1",
        )
        approval_id = result.interruptions[0]["approval_id"]
        # Don't approve — just resume
        resumed = await app_with_governance.resume(
            run_id=result.run_id,
            approval_id=approval_id,
        )
        assert resumed.status == "interrupted"

    @pytest.mark.asyncio
    async def test_permission_denied_returns_failed(self, app_with_governance) -> None:
        """Tool with permissions but context has none → permission_denied."""
        _register_tool(
            app_with_governance, "test.restricted",
            permissions=["special:perm"],
        )
        app_with_governance.register_agent(
            AgentSpec(
                name="support", instructions="Help",
                tools=["test.restricted"],
            )
        )
        app_with_governance.register_workflow(
            Workflow.single(agent="support", name="cs")
        )

        result = await app_with_governance.run(
            agent="support",
            input="use the restricted tool",
            user_id="u1",
            tenant_id="t1",
            permissions=[],  # No permissions granted
        )
        assert result.status == "failed"
        assert result.error is not None
        assert result.error["type"] == "permission_denied"
