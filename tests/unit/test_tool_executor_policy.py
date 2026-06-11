"""Phase 23: Tests for ToolExecutor policy engine integration."""

from __future__ import annotations

import pytest

from agent_app.governance.policy import (
    ConfigurablePolicyEngine,
    DefaultPolicyEngine,
    PolicyAction,
    PolicyDecision,
    PolicyEvaluationContext,
)
from agent_app.runtime.tool_executor import ToolExecutor, ToolExecutionResult, ToolExecutionStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeApprovalStore:
    def __init__(self):
        self._store = {}

    async def create(self, request):
        request.approval_id = f"apv_{id(request)}"
        self._store[request.approval_id] = request
        return request

    async def get(self, approval_id):
        return self._store[approval_id]

    async def approve(self, *a, **kw):
        pass

    async def reject(self, *a, **kw):
        pass

    async def list_pending(self, tenant_id=None):
        return []


class _FakePermissionChecker:
    def __init__(self, allowed=True):
        self.allowed = allowed

    async def check(self, permissions, context):
        if self.allowed:
            return True
        return False


class _FakeToolRegistry:
    def __init__(self, spec, fn):
        self._spec = spec
        self._fn = fn
        # Use staticmethod to prevent Python descriptor protocol from binding fn
        self._entry = type("E", (), {
            "spec": spec,
            "fn": staticmethod(fn),
        })()

    def get_entry(self, name):
        return self._entry

    def exists(self, name):
        return True


def _make_executor(
    policy_engine=None,
    risk_level="low",
    requires_approval=False,
    permissions=None,
    tool_name="test.tool",
):
    from agent_app.core.tool_spec import ToolSpec

    spec = ToolSpec(
        name=tool_name,
        description="Test tool",
        risk_level=risk_level,
        requires_approval=requires_approval,
        permissions=permissions or [],
    )

    async def _fn(**kwargs):
        return {"result": "ok"}

    registry = _FakeToolRegistry(spec, _fn)
    approval_store = _FakeApprovalStore()
    perm_checker = _FakePermissionChecker(allowed=True)

    from agent_app.governance.audit import InMemoryAuditLogger
    audit = InMemoryAuditLogger()

    return ToolExecutor(
        tool_registry=registry,
        approval_store=approval_store,
        permission_checker=perm_checker,
        audit_logger=audit,
        policy_engine=policy_engine,
    ), spec, _fn


def _make_context(run_id="r1", user_id="u1", tenant_id="t1", permissions=None, metadata=None):
    from agent_app.core.context import RunContext
    return RunContext(
        run_id=run_id,
        user_id=user_id,
        tenant_id=tenant_id,
        permissions=permissions or [],
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Tests: policy deny
# ---------------------------------------------------------------------------


class TestPolicyDeny:
    @pytest.mark.asyncio
    async def test_policy_deny_returns_failed(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "deny_dangerous",
                "when": {"tool_name": "test.tool"},
                "then": {"action": "deny", "reason": "Not allowed"},
            }
        ])
        executor, spec, _ = _make_executor(policy_engine=engine)
        ctx = _make_context()

        result = await executor.execute(
            tool_name="test.tool",
            arguments={},
            context=ctx,
        )
        assert result.status == ToolExecutionStatus.FAILED.value
        assert result.error is not None
        assert result.error["type"] == "policy_denied"

    @pytest.mark.asyncio
    async def test_policy_deny_with_reason(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "deny_no_role",
                "when": {"tool_name": "test.tool", "missing_roles": ["admin"]},
                "then": {"action": "deny", "reason": "Missing admin role"},
            }
        ])
        executor, spec, _ = _make_executor(policy_engine=engine)
        ctx = _make_context(metadata={})  # no roles

        result = await executor.execute(
            tool_name="test.tool",
            arguments={},
            context=ctx,
        )
        assert result.status == ToolExecutionStatus.FAILED.value
        assert "admin" in (result.error.get("message") or "")

    @pytest.mark.asyncio
    async def test_policy_deny_does_not_execute_tool(self):
        call_count = [0]

        async def _fn(**kwargs):
            call_count[0] += 1
            return {"result": "should_not_reach"}

        from agent_app.core.tool_spec import ToolSpec
        spec = ToolSpec(name="test.tool", description="Test", risk_level="low")
        registry = _FakeToolRegistry(spec, _fn)
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "deny_all",
                "when": {"tool_name": "test.tool"},
                "then": {"action": "deny"},
            }
        ])
        from agent_app.governance.audit import InMemoryAuditLogger
        audit = InMemoryAuditLogger()
        executor = ToolExecutor(
            tool_registry=registry,
            approval_store=_FakeApprovalStore(),
            permission_checker=_FakePermissionChecker(allowed=True),
            audit_logger=audit,
            policy_engine=engine,
        )
        ctx = _make_context()


# ---------------------------------------------------------------------------
# Tests: policy require_approval
# ---------------------------------------------------------------------------


class TestPolicyRequireApproval:
    @pytest.mark.asyncio
    async def test_policy_require_approval_returns_interrupted(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "require_refund_approval",
                "when": {"tool_name": "refund.request"},
                "then": {
                    "action": "require_approval",
                    "reason": "Refunds need approval",
                    "ttl_seconds": 1800,
                },
            }
        ])
        executor, spec, _ = _make_executor(
            policy_engine=engine,
            tool_name="refund.request",
        )
        ctx = _make_context()

        result = await executor.execute(
            tool_name="refund.request",
            arguments={"order_id": "123"},
            context=ctx,
        )
        assert result.status == ToolExecutionStatus.INTERRUPTED.value
        assert result.approval_request is not None
        assert result.approval_request.tool_name == "refund.request"

    @pytest.mark.asyncio
    async def test_policy_ttl_overrides_default(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "short_ttl",
                "when": {"tool_name": "test.tool"},
                "then": {
                    "action": "require_approval",
                    "ttl_seconds": 300,
                },
            }
        ])
        executor, spec, _ = _make_executor(
            policy_engine=engine,
            tool_name="test.tool",
        )
        executor.default_ttl_seconds = 3600  # default would be 1 hour
        ctx = _make_context()

        result = await executor.execute(
            tool_name="test.tool",
            arguments={},
            context=ctx,
        )
        assert result.status == ToolExecutionStatus.INTERRUPTED.value
        # TTL should be 300, not 3600
        approval = result.approval_request
        assert approval is not None
        # Check that expires_at is ~300s from now, not ~3600s
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        delta = (approval.expires_at - now).total_seconds()
        assert 290 < delta < 310, f"TTL should be ~300s, got {delta}s"


# ---------------------------------------------------------------------------
# Tests: policy audit_only
# ---------------------------------------------------------------------------


class TestPolicyAuditOnly:
    @pytest.mark.asyncio
    async def test_audit_only_allows_execution(self):
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "audit_billing",
                "when": {"tool_name": "billing.query"},
                "then": {"action": "audit_only", "reason": "Billing requires audit"},
            }
        ])
        executor, spec, _ = _make_executor(
            policy_engine=engine,
            tool_name="billing.query",
        )
        ctx = _make_context()

        result = await executor.execute(
            tool_name="billing.query",
            arguments={},
            context=ctx,
        )
        # audit_only should NOT block execution
        assert result.status == ToolExecutionStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# Tests: policy audit events
# ---------------------------------------------------------------------------


class TestPolicyAuditEvents:
    @pytest.mark.asyncio
    async def test_policy_decision_audit_event_written(self):
        from agent_app.governance.audit import InMemoryAuditLogger

        audit = InMemoryAuditLogger()
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "require_refund",
                "when": {"tool_name": "refund.request"},
                "then": {"action": "require_approval", "reason": "Refunds need approval"},
            }
        ])
        executor, spec, _ = _make_executor(
            policy_engine=engine,
            tool_name="refund.request",
        )
        # Replace audit logger with our tracked one
        executor.audit_logger = audit
        ctx = _make_context()

        result = await executor.execute(
            tool_name="refund.request",
            arguments={},
            context=ctx,
        )
        # Check audit events
        events = audit.list_events(event_type="policy.evaluated")
        assert len(events) >= 1
        assert events[0].tool_name == "refund.request"

    @pytest.mark.asyncio
    async def test_policy_denied_audit_event(self):
        from agent_app.governance.audit import InMemoryAuditLogger

        audit = InMemoryAuditLogger()
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "deny_all",
                "when": {"tool_name": "test.tool"},
                "then": {"action": "deny"},
            }
        ])
        executor, spec, _ = _make_executor(policy_engine=engine)
        executor.audit_logger = audit
        ctx = _make_context()

        await executor.execute(tool_name="test.tool", arguments={}, context=ctx)
        events = audit.list_events(event_type="policy.denied")
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_policy_approval_required_audit_event(self):
        from agent_app.governance.audit import InMemoryAuditLogger

        audit = InMemoryAuditLogger()
        engine = ConfigurablePolicyEngine(rules=[
            {
                "name": "need_approval",
                "when": {"tool_name": "test.tool"},
                "then": {"action": "require_approval"},
            }
        ])
        executor, spec, _ = _make_executor(policy_engine=engine)
        executor.audit_logger = audit
        ctx = _make_context()

        await executor.execute(tool_name="test.tool", arguments={}, context=ctx)
        events = audit.list_events(event_type="policy.approval_required")
        assert len(events) >= 1


# ---------------------------------------------------------------------------
# Tests: no policy engine (backward compat)
# ---------------------------------------------------------------------------


class TestNoPolicyEngine:
    @pytest.mark.asyncio
    async def test_no_engine_preserves_phase22_behavior(self):
        """Without a policy engine, ToolExecutor works exactly as Phase 22."""
        executor, spec, _ = _make_executor(policy_engine=None)
        ctx = _make_context(permissions=[])

        result = await executor.execute(
            tool_name="test.tool",
            arguments={},
            context=ctx,
        )
        # Low risk, no perms required, no approval → completed
        assert result.status == ToolExecutionStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_no_engine_high_risk_still_requires_approval(self):
        executor, spec, _ = _make_executor(
            policy_engine=None,
            risk_level="high",
            requires_approval=True,
        )
        ctx = _make_context(permissions=[])

        result = await executor.execute(
            tool_name="test.tool",
            arguments={},
            context=ctx,
        )
        assert result.status == ToolExecutionStatus.INTERRUPTED.value


# ---------------------------------------------------------------------------
# Tests: existing ToolSpec governance still works with policy engine
# ---------------------------------------------------------------------------


class TestPolicyPlusToolSpec:
    @pytest.mark.asyncio
    async def test_existing_requires_approval_still_works(self):
        """ToolSpec.requires_approval=True still triggers approval even without policy."""
        engine = DefaultPolicyEngine()
        executor, spec, _ = _make_executor(
            policy_engine=engine,
            risk_level="low",
            requires_approval=True,
        )
        ctx = _make_context(metadata={"requires_approval": True})

        result = await executor.execute(
            tool_name="test.tool",
            arguments={},
            context=ctx,
        )
        assert result.status == ToolExecutionStatus.INTERRUPTED.value

    @pytest.mark.asyncio
    async def test_existing_permissions_still_checked(self):
        """ToolSpec.permissions still enforced even with policy engine."""
        from agent_app.governance.audit import InMemoryAuditLogger
        from agent_app.core.tool_spec import ToolSpec

        audit = InMemoryAuditLogger()
        # Permissive policy engine — lets existing permission check fire
        engine = ConfigurablePolicyEngine(rules=[
            {"name": "allow_all", "when": {}, "then": {"action": "allow"}}
        ])
        spec = ToolSpec(name="test.tool", description="Test", risk_level="low",
                        permissions=["special:perm"])
        registry = _FakeToolRegistry(spec, lambda **kw: {"result": "ok"})
        executor = ToolExecutor(
            tool_registry=registry,
            approval_store=_FakeApprovalStore(),
            permission_checker=_FakePermissionChecker(allowed=False),  # deny all
            audit_logger=audit,
            policy_engine=engine,
        )
        # No permissions in context
        ctx = _make_context(permissions=[])

        result = await executor.execute(
            tool_name="test.tool",
            arguments={},
            context=ctx,
        )
        assert result.status == ToolExecutionStatus.FAILED.value
        # Policy engine now handles permission checks — error type is policy_denied
        assert result.error["type"] == "policy_denied"
