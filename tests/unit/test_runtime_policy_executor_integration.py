"""Phase 38: ToolExecutor integration with runtime policy enforcement."""

from __future__ import annotations

import pytest

from conftest import _run_async

from agent_app.core.context import RunContext
from agent_app.core.tool_spec import ToolSpec
from agent_app.governance.audit import AuditEvent, InMemoryAuditLogger
from agent_app.governance.policy_enforcement import (
    PolicyActionType,
    PolicyDecisionStatus,
)
from agent_app.governance.risk import RiskLevel, requires_tool_approval
from agent_app.governance.runtime_policy import (
    RuntimePolicyEffect,
    RuntimePolicyRule,
    RuntimePolicyRuleStatus,
)
from agent_app.runtime.approval_store import InMemoryApprovalStore
from agent_app.runtime.policy_enforcement_service import PolicyEnforcementService
from agent_app.runtime.runtime_policy_evaluator import (
    RuntimePolicyEvaluationRequest,
    RuntimePolicyEvaluator,
)
from agent_app.runtime.runtime_policy_store import InMemoryRuntimePolicyStore
from agent_app.runtime.tool_executor import ToolExecutor, ToolExecutionStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AllowAllChecker:
    async def check(self, permissions, context):
        return True


class _DenyAllChecker:
    async def check(self, permissions, context):
        return False


class _FakeToolRegistry:
    """Minimal tool registry that holds one tool entry."""

    def __init__(self, spec, fn):
        self._entry = type(
            "Entry",
            (),
            {"spec": spec, "fn": staticmethod(fn)},
        )()

    def get_entry(self, name):
        return self._entry

    def exists(self, name):
        return True


def _make_context(**kwargs):
    defaults = {
        "run_id": "r1",
        "user_id": "u1",
        "tenant_id": "t1",
    }
    defaults.update(kwargs)
    return RunContext(**defaults)


def _make_tool_spec(tool_name="test.tool", risk_level="low", requires_approval=False, permissions=None):
    return ToolSpec(
        name=tool_name,
        description="Test tool",
        risk_level=risk_level,
        requires_approval=requires_approval,
        permissions=permissions or [],
    )


def _make_async_fn():
    async def _fn(**kwargs):
        return {"result": "ok"}
    return _fn


def _make_executor(
    *,
    tool_name="test.tool",
    risk_level="low",
    requires_approval=False,
    permissions=None,
    policy_enforcement_service=None,
    permission_checker=None,
    approval_store=None,
    audit_logger=None,
):
    spec = _make_tool_spec(
        tool_name=tool_name,
        risk_level=risk_level,
        requires_approval=requires_approval,
        permissions=permissions,
    )
    fn = _make_async_fn()
    registry = _FakeToolRegistry(spec, fn)
    store = approval_store or InMemoryApprovalStore()
    checker = permission_checker or _AllowAllChecker()
    logger = audit_logger or InMemoryAuditLogger()

    executor = ToolExecutor(
        tool_registry=registry,
        approval_store=store,
        permission_checker=checker,
        audit_logger=logger,
        policy_enforcement_service=policy_enforcement_service,
    )
    return executor, spec, fn, store, logger


async def _build_enforcement_service(*, rules=None):
    """Build a PolicyEnforcementService with the given rules."""
    store = InMemoryRuntimePolicyStore()
    if rules:
        for rule in rules:
            await store.create(rule)
    evaluator = RuntimePolicyEvaluator(policy_store=store)
    audit = InMemoryAuditLogger()
    service = PolicyEnforcementService(evaluator=evaluator, audit_logger=audit)
    return service, store, audit


def _deny_rule(tool_name="test.tool"):
    return RuntimePolicyRule(
        rule_id="rpr_001",
        name="deny_tool",
        action_type=PolicyActionType.TOOL_EXECUTE,
        effect=RuntimePolicyEffect.DENY,
        status=RuntimePolicyRuleStatus.ENABLED,
        tool_name=tool_name,
        reason="Tool blocked by runtime policy",
    )


def _require_approval_rule(tool_name="test.tool"):
    return RuntimePolicyRule(
        rule_id="rpr_002",
        name="require_approval_tool",
        action_type=PolicyActionType.TOOL_EXECUTE,
        effect=RuntimePolicyEffect.REQUIRE_APPROVAL,
        status=RuntimePolicyRuleStatus.ENABLED,
        tool_name=tool_name,
        reason="Approval required by runtime policy",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolExecutorRuntimePolicyEnforcement:
    """Phase 38: ToolExecutor + runtime policy enforcement integration."""

    # -- 1. backward compat ------------------------------------------------

    def test_no_enforcement_service_backward_compat(self) -> None:
        """ToolExecutor without enforcement service works exactly as before."""
        async def _run():
            executor, spec, _, store, logger = _make_executor()
            ctx = _make_context()
            result = await executor.execute("test.tool", {"x": 1}, ctx)
            assert result.status == ToolExecutionStatus.COMPLETED.value
            assert result.output == {"result": "ok"}

        _run_async(_run())

    # -- 2. existing ToolSpec approval still works --------------------------

    def test_existing_tool_spec_requires_approval_still_works(self) -> None:
        """ToolSpec.requires_approval=True still interrupts without enforcement service."""
        async def _run():
            executor, spec, _, store, logger = _make_executor(
                risk_level="high",
                requires_approval=True,
            )
            ctx = _make_context()
            result = await executor.execute("test.tool", {"x": 1}, ctx)
            assert result.status == ToolExecutionStatus.INTERRUPTED.value
            assert result.approval_request is not None

        _run_async(_run())

    # -- 3. runtime DENY blocks tool ---------------------------------------

    def test_runtime_deny_blocks_tool(self) -> None:
        """Enforcement service with DENY rule -> FAILED result."""
        async def _run():
            service, _, _ = await _build_enforcement_service(
                rules=[_deny_rule("test.tool")]
            )
            executor, spec, _, store, logger = _make_executor(
                policy_enforcement_service=service,
            )
            ctx = _make_context()
            result = await executor.execute("test.tool", {"x": 1}, ctx)
            assert result.status == ToolExecutionStatus.FAILED.value
            assert result.error is not None
            assert result.error["type"] == "policy_enforcement_denied"
            assert "runtime policy" in result.error["message"].lower()

        _run_async(_run())

    # -- 4. runtime REQUIRE_APPROVAL interrupts ----------------------------

    def test_runtime_require_approval_interrupts(self) -> None:
        """Enforcement service with REQUIRE_APPROVAL rule -> INTERRUPTED result."""
        async def _run():
            service, _, _ = await _build_enforcement_service(
                rules=[_require_approval_rule("test.tool")]
            )
            executor, spec, _, store, logger = _make_executor(
                policy_enforcement_service=service,
            )
            ctx = _make_context()
            result = await executor.execute("test.tool", {"x": 1}, ctx)
            assert result.status == ToolExecutionStatus.INTERRUPTED.value
            assert result.approval_request is not None

        _run_async(_run())

    # -- 5. INTERRUPTED result includes policy_decision_id -----------------

    def test_runtime_require_approval_includes_decision_id(self) -> None:
        """INTERRUPTED result's approval_request has policy_decision_id in metadata."""
        async def _run():
            service, _, _ = await _build_enforcement_service(
                rules=[_require_approval_rule("test.tool")]
            )
            executor, spec, _, store, logger = _make_executor(
                policy_enforcement_service=service,
            )
            ctx = _make_context()
            result = await executor.execute("test.tool", {"x": 1}, ctx)
            assert result.status == ToolExecutionStatus.INTERRUPTED.value
            approval = result.approval_request
            assert approval is not None
            assert "policy_decision_id" in approval.metadata
            assert approval.metadata["policy_decision_id"].startswith("ped_")
            assert "enforcement_reason" in approval.metadata

        _run_async(_run())

    # -- 6. no duplicate approval ------------------------------------------

    def test_no_duplicate_approval(self) -> None:
        """When ToolSpec.requires_approval AND runtime policy REQUIRE_APPROVAL
        both trigger, ToolSpec approval takes precedence (no duplicate)."""
        async def _run():
            service, _, _ = await _build_enforcement_service(
                rules=[_require_approval_rule("test.tool")]
            )
            store = InMemoryApprovalStore()
            executor, spec, _, _, logger = _make_executor(
                risk_level="high",
                requires_approval=True,
                policy_enforcement_service=service,
                approval_store=store,
            )
            ctx = _make_context()
            result = await executor.execute("test.tool", {"x": 1}, ctx)

            # Should have interrupted
            assert result.status == ToolExecutionStatus.INTERRUPTED.value
            assert result.approval_request is not None

            # Only one approval should be in the store
            pending = await store.list_pending(tenant_id="t1")
            assert len(pending) == 1

            # The approval should be the ToolSpec one (not runtime policy one),
            # meaning it should NOT have policy_decision_id in metadata
            assert "policy_decision_id" not in pending[0].metadata

        _run_async(_run())

    # -- 7. permission denied still fails ----------------------------------

    def test_permission_denied_still_fails(self) -> None:
        """Permission denial still results in FAILED even with enforcement service."""
        async def _run():
            # Allow-all enforcement service
            allow_rule = RuntimePolicyRule(
                rule_id="rpr_003",
                name="allow_all",
                action_type=PolicyActionType.TOOL_EXECUTE,
                effect=RuntimePolicyEffect.ALLOW,
                status=RuntimePolicyRuleStatus.ENABLED,
            )
            service, _, _ = await _build_enforcement_service(rules=[allow_rule])

            executor, spec, _, store, logger = _make_executor(
                policy_enforcement_service=service,
                permission_checker=_DenyAllChecker(),
                permissions=["admin:write"],
            )
            ctx = _make_context()
            result = await executor.execute("test.tool", {"x": 1}, ctx)
            assert result.status == ToolExecutionStatus.FAILED.value
            assert result.error is not None
            assert result.error["type"] == "permission_denied"

        _run_async(_run())

    # -- 8. low-risk tool still executes -----------------------------------

    def test_low_risk_tool_still_executes(self) -> None:
        """Low-risk tool with no rules executes normally (ALLOWED by no_matching_rule)."""
        async def _run():
            # Empty policy store — no rules at all
            service, _, _ = await _build_enforcement_service(rules=[])
            executor, spec, _, store, logger = _make_executor(
                policy_enforcement_service=service,
            )
            ctx = _make_context()
            result = await executor.execute("test.tool", {"x": 1}, ctx)
            assert result.status == ToolExecutionStatus.COMPLETED.value
            assert result.output == {"result": "ok"}

        _run_async(_run())
