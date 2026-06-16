"""Phase 38: Tests for policy enforcement and runtime policy models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_enforcement import (
    PolicyActionType,
    PolicyDecisionStatus,
    PolicyEnforcementDecision,
)
from agent_app.governance.policy_rollout_approval import (
    RolloutApprovalPolicy,
    RolloutApprovalPolicyType,
)
from agent_app.governance.runtime_policy import (
    RuntimePolicyEffect,
    RuntimePolicyRule,
    RuntimePolicyRuleStatus,
)


class TestPolicyActionType:
    def test_tool_execute(self):
        assert PolicyActionType.TOOL_EXECUTE.value == "tool.execute"

    def test_tool_resume(self):
        assert PolicyActionType.TOOL_RESUME.value == "tool.resume"

    def test_approval_approve(self):
        assert PolicyActionType.APPROVAL_APPROVE.value == "approval.approve"

    def test_approval_reject(self):
        assert PolicyActionType.APPROVAL_REJECT.value == "approval.reject"

    def test_rollout_step_execute(self):
        assert PolicyActionType.ROLLOUT_STEP_EXECUTE.value == "rollout.step.execute"

    def test_policy_promotion_execute(self):
        assert PolicyActionType.POLICY_PROMOTION_EXECUTE.value == "policy.promotion.execute"


class TestPolicyDecisionStatus:
    def test_allowed(self):
        assert PolicyDecisionStatus.ALLOWED.value == "allowed"

    def test_denied(self):
        assert PolicyDecisionStatus.DENIED.value == "denied"

    def test_approval_required(self):
        assert PolicyDecisionStatus.APPROVAL_REQUIRED.value == "approval_required"


class TestPolicyEnforcementDecision:
    def test_valid_decision(self):
        decision = PolicyEnforcementDecision(
            decision_id="ped_001",
            status=PolicyDecisionStatus.ALLOWED,
            action_type=PolicyActionType.TOOL_EXECUTE,
            subject="tool:refund",
            reason="no_matching_rule",
            created_at=datetime.now(timezone.utc),
        )
        assert decision.decision_id == "ped_001"
        assert decision.status == PolicyDecisionStatus.ALLOWED

    def test_prefix_validation(self):
        with pytest.raises(ValueError, match="ped_"):
            PolicyEnforcementDecision(
                decision_id="bad_001",
                status=PolicyDecisionStatus.ALLOWED,
                action_type=PolicyActionType.TOOL_EXECUTE,
                created_at=datetime.now(timezone.utc),
            )

    def test_timezone_aware_required(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            PolicyEnforcementDecision(
                decision_id="ped_002",
                status=PolicyDecisionStatus.DENIED,
                action_type=PolicyActionType.TOOL_EXECUTE,
                created_at=datetime.now(),  # naive
            )

    def test_approval_required_with_policy(self):
        policy = RolloutApprovalPolicy(
                    policy_type=RolloutApprovalPolicyType.QUORUM,
                    required_approvals=2,
                )
        decision = PolicyEnforcementDecision(
            decision_id="ped_003",
            status=PolicyDecisionStatus.APPROVAL_REQUIRED,
            action_type=PolicyActionType.TOOL_EXECUTE,
            approval_policy=policy,
            created_at=datetime.now(timezone.utc),
        )
        assert decision.approval_policy is not None
        assert decision.approval_policy.required_approvals == 2

    def test_defaults(self):
        decision = PolicyEnforcementDecision(
            decision_id="ped_004",
            status=PolicyDecisionStatus.ALLOWED,
            action_type=PolicyActionType.TOOL_EXECUTE,
            created_at=datetime.now(timezone.utc),
        )
        assert decision.subject is None
        assert decision.reason is None
        assert decision.required_permissions == []
        assert decision.required_roles == []
        assert decision.approval_policy is None
        assert decision.metadata == {}


class TestRuntimePolicyRuleStatus:
    def test_enabled(self):
        assert RuntimePolicyRuleStatus.ENABLED.value == "enabled"

    def test_disabled(self):
        assert RuntimePolicyRuleStatus.DISABLED.value == "disabled"


class TestRuntimePolicyEffect:
    def test_allow(self):
        assert RuntimePolicyEffect.ALLOW.value == "allow"

    def test_deny(self):
        assert RuntimePolicyEffect.DENY.value == "deny"

    def test_require_approval(self):
        assert RuntimePolicyEffect.REQUIRE_APPROVAL.value == "require_approval"


class TestRuntimePolicyRule:
    def test_valid_rule(self):
        rule = RuntimePolicyRule(
            rule_id="rpr_001",
            name="require_quorum_for_refunds",
            action_type=PolicyActionType.TOOL_EXECUTE,
            effect=RuntimePolicyEffect.REQUIRE_APPROVAL,
            tool_name="refund.request",
            required_permissions=["refund:create"],
            approval_policy=RolloutApprovalPolicy(
                    policy_type=RolloutApprovalPolicyType.QUORUM,
                    required_approvals=2,
                ),
        )
        assert rule.rule_id == "rpr_001"
        assert rule.effect == RuntimePolicyEffect.REQUIRE_APPROVAL
        assert rule.status == RuntimePolicyRuleStatus.ENABLED

    def test_prefix_validation(self):
        with pytest.raises(ValueError, match="rpr_"):
            RuntimePolicyRule(
                rule_id="bad_001",
                name="bad_rule",
                action_type=PolicyActionType.TOOL_EXECUTE,
                effect=RuntimePolicyEffect.ALLOW,
            )

    def test_default_enabled(self):
        rule = RuntimePolicyRule(
            rule_id="rpr_002",
            name="default_status",
            action_type=PolicyActionType.TOOL_EXECUTE,
            effect=RuntimePolicyEffect.ALLOW,
        )
        assert rule.status == RuntimePolicyRuleStatus.ENABLED

    def test_with_deny_effect(self):
        rule = RuntimePolicyRule(
            rule_id="rpr_003",
            name="deny_dangerous_delete",
            action_type=PolicyActionType.TOOL_EXECUTE,
            effect=RuntimePolicyEffect.DENY,
            tool_name="data.delete",
            reason="Deletion is disabled",
        )
        assert rule.effect == RuntimePolicyEffect.DENY
        assert rule.reason == "Deletion is disabled"

    def test_disabled_rule(self):
        rule = RuntimePolicyRule(
            rule_id="rpr_004",
            name="disabled_rule",
            action_type=PolicyActionType.TOOL_EXECUTE,
            effect=RuntimePolicyEffect.ALLOW,
            status=RuntimePolicyRuleStatus.DISABLED,
        )
        assert rule.status == RuntimePolicyRuleStatus.DISABLED


# ---------------------------------------------------------------------------
# Phase 38 Task 2: RuntimePolicyStore tests
# ---------------------------------------------------------------------------

from tests.conftest import _run_async
from agent_app.runtime.runtime_policy_store import (
    InMemoryRuntimePolicyStore,
    SQLiteRuntimePolicyStore,
    create_runtime_policy_store,
)


def _sample_rule(rule_id: str = "rpr_store_001", **overrides) -> RuntimePolicyRule:
    """Build a sample RuntimePolicyRule for store tests."""
    defaults = dict(
        rule_id=rule_id,
        name=f"rule_{rule_id}",
        action_type=PolicyActionType.TOOL_EXECUTE,
        effect=RuntimePolicyEffect.DENY,
        tool_name="data.delete",
        risk_level="high",
        required_permissions=["data:delete"],
        required_roles=["admin"],
        reason="deny by default",
        metadata={"source": "test"},
    )
    defaults.update(overrides)
    return RuntimePolicyRule(**defaults)


class TestInMemoryRuntimePolicyStore:

    def test_create_and_get(self):
        store = InMemoryRuntimePolicyStore()
        rule = _sample_rule()
        created = _run_async(store.create(rule))
        assert created.rule_id == "rpr_store_001"
        fetched = _run_async(store.get("rpr_store_001"))
        assert fetched is not None
        assert fetched.name == rule.name
        assert fetched.effect == rule.effect

    def test_create_duplicate_raises(self):
        store = InMemoryRuntimePolicyStore()
        rule = _sample_rule()
        _run_async(store.create(rule))
        with pytest.raises(ValueError, match="already exists"):
            _run_async(store.create(rule))

    def test_get_nonexistent_returns_none(self):
        store = InMemoryRuntimePolicyStore()
        assert _run_async(store.get("rpr_missing")) is None

    def test_list_all(self):
        store = InMemoryRuntimePolicyStore()
        _run_async(store.create(_sample_rule("rpr_list_01")))
        _run_async(store.create(_sample_rule("rpr_list_02")))
        results = _run_async(store.list())
        assert len(results) == 2

    def test_list_by_action_type(self):
        store = InMemoryRuntimePolicyStore()
        _run_async(store.create(_sample_rule("rpr_at_01", action_type=PolicyActionType.TOOL_EXECUTE)))
        _run_async(store.create(_sample_rule("rpr_at_02", action_type=PolicyActionType.TOOL_RESUME)))
        results = _run_async(store.list(action_type=PolicyActionType.TOOL_EXECUTE))
        assert len(results) == 1
        assert results[0].rule_id == "rpr_at_01"

    def test_list_by_status(self):
        store = InMemoryRuntimePolicyStore()
        _run_async(store.create(_sample_rule("rpr_st_01")))
        _run_async(store.create(_sample_rule("rpr_st_02")))
        _run_async(store.disable("rpr_st_02"))
        results = _run_async(store.list(status=RuntimePolicyRuleStatus.DISABLED))
        assert len(results) == 1
        assert results[0].rule_id == "rpr_st_02"

    def test_enable_rule(self):
        store = InMemoryRuntimePolicyStore()
        rule = _sample_rule("rpr_en_01", status=RuntimePolicyRuleStatus.DISABLED)
        _run_async(store.create(rule))
        updated = _run_async(store.enable("rpr_en_01"))
        assert updated.status == RuntimePolicyRuleStatus.ENABLED

    def test_disable_rule(self):
        store = InMemoryRuntimePolicyStore()
        _run_async(store.create(_sample_rule("rpr_dis_01")))
        updated = _run_async(store.disable("rpr_dis_01"))
        assert updated.status == RuntimePolicyRuleStatus.DISABLED

    def test_disable_nonexistent_raises(self):
        store = InMemoryRuntimePolicyStore()
        with pytest.raises(KeyError):
            _run_async(store.disable("rpr_missing"))


class TestSQLiteRuntimePolicyStore:

    def test_sqlite_create_and_get(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = SQLiteRuntimePolicyStore(db_path=db)
        rule = _sample_rule("rpr_sql_01")
        _run_async(store.create(rule))

        # Verify persistence with a new store instance on the same file
        store2 = SQLiteRuntimePolicyStore(db_path=db)
        fetched = _run_async(store2.get("rpr_sql_01"))
        assert fetched is not None
        assert fetched.name == rule.name
        assert fetched.effect == rule.effect
        assert fetched.tool_name == "data.delete"
        assert fetched.required_permissions == ["data:delete"]
        assert fetched.required_roles == ["admin"]
        assert fetched.metadata == {"source": "test"}

    def test_sqlite_list_filters(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = SQLiteRuntimePolicyStore(db_path=db)
        _run_async(store.create(_sample_rule("rpr_sqf_01", action_type=PolicyActionType.TOOL_EXECUTE)))
        _run_async(store.create(_sample_rule("rpr_sqf_02", action_type=PolicyActionType.TOOL_RESUME)))
        _run_async(store.create(_sample_rule("rpr_sqf_03", action_type=PolicyActionType.TOOL_EXECUTE)))

        results = _run_async(store.list(action_type=PolicyActionType.TOOL_RESUME))
        assert len(results) == 1
        assert results[0].rule_id == "rpr_sqf_02"

    def test_sqlite_enable_disable(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = SQLiteRuntimePolicyStore(db_path=db)
        _run_async(store.create(_sample_rule("rpr_sqed_01")))

        updated = _run_async(store.disable("rpr_sqed_01"))
        assert updated.status == RuntimePolicyRuleStatus.DISABLED

        # Persisted in a fresh instance
        store2 = SQLiteRuntimePolicyStore(db_path=db)
        fetched = _run_async(store2.get("rpr_sqed_01"))
        assert fetched.status == RuntimePolicyRuleStatus.DISABLED

        updated2 = _run_async(store2.enable("rpr_sqed_01"))
        assert updated2.status == RuntimePolicyRuleStatus.ENABLED

    def test_sqlite_approval_policy_persists(self, tmp_path):
        db = str(tmp_path / "test.db")
        store = SQLiteRuntimePolicyStore(db_path=db)
        rule = _sample_rule(
            "rpr_sqap_01",
            effect=RuntimePolicyEffect.REQUIRE_APPROVAL,
            approval_policy=RolloutApprovalPolicy(
                policy_type=RolloutApprovalPolicyType.QUORUM,
                required_approvals=3,
            ),
        )
        _run_async(store.create(rule))

        store2 = SQLiteRuntimePolicyStore(db_path=db)
        fetched = _run_async(store2.get("rpr_sqap_01"))
        assert fetched.approval_policy is not None
        assert fetched.approval_policy.policy_type == RolloutApprovalPolicyType.QUORUM
        assert fetched.approval_policy.required_approvals == 3


# ---------------------------------------------------------------------------
# Phase 38 Task 3: RuntimePolicyEvaluator and PolicyEnforcementService tests
# ---------------------------------------------------------------------------

from agent_app.core.context import RunContext
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.runtime.runtime_policy_evaluator import (
    RuntimePolicyEvaluationRequest,
    RuntimePolicyEvaluator,
)
from agent_app.runtime.policy_enforcement_service import PolicyEnforcementService


def _eval_context(
    *,
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
    user_id: str = "user_001",
    tenant_id: str = "tenant_001",
) -> RunContext:
    """Build a minimal RunContext for evaluator tests."""
    return RunContext(
        run_id="run_eval_001",
        user_id=user_id,
        tenant_id=tenant_id,
        roles=roles or [],
        permissions=permissions or [],
    )


class TestRuntimePolicyEvaluator:

    def test_no_matching_rule_returns_allowed(self):
        """No rules in store -> ALLOWED with reason 'no_matching_rule'."""
        store = InMemoryRuntimePolicyStore()
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        ctx = _eval_context()
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req))
        assert result.status == PolicyDecisionStatus.ALLOWED
        assert result.reason == "no_matching_rule"

    def test_no_store_returns_allowed(self):
        """Evaluator with None store -> ALLOWED."""
        evaluator = RuntimePolicyEvaluator(policy_store=None)
        ctx = _eval_context()
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req))
        assert result.status == PolicyDecisionStatus.ALLOWED
        assert result.reason == "no_policy_store"

    def test_deny_rule_blocks(self):
        """DENY rule matches -> DENIED."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_eval_deny_01",
                    name="deny_refund",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.DENY,
                    reason="Refunds are disabled",
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        ctx = _eval_context()
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req))
        assert result.status == PolicyDecisionStatus.DENIED
        assert "Refunds are disabled" in (result.reason or "")

    def test_require_approval_returns_approval_required(self):
        """REQUIRE_APPROVAL rule with satisfied permissions/roles -> APPROVAL_REQUIRED."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_eval_ra_01",
                    name="require_approval_refund",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.REQUIRE_APPROVAL,
                    required_permissions=["refund:create"],
                    required_roles=["finance"],
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        ctx = _eval_context(roles=["finance"], permissions=["refund:create"])
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req))
        assert result.status == PolicyDecisionStatus.APPROVAL_REQUIRED

    def test_require_approval_missing_permission_denied(self):
        """REQUIRE_APPROVAL rule with missing permission -> DENIED."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_eval_ra_02",
                    name="require_approval_refund",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.REQUIRE_APPROVAL,
                    required_permissions=["refund:create"],
                    required_roles=["finance"],
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        ctx = _eval_context(roles=["finance"], permissions=[])
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req))
        assert result.status == PolicyDecisionStatus.DENIED
        assert "Missing required permission" in (result.reason or "")

    def test_require_approval_missing_role_denied(self):
        """REQUIRE_APPROVAL rule with missing role -> DENIED."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_eval_ra_03",
                    name="require_approval_refund",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.REQUIRE_APPROVAL,
                    required_permissions=["refund:create"],
                    required_roles=["finance"],
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        ctx = _eval_context(roles=["viewer"], permissions=["refund:create"])
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req))
        assert result.status == PolicyDecisionStatus.DENIED
        assert "Missing required role" in (result.reason or "")

    def test_allow_rule_allows(self):
        """ALLOW rule with satisfied permissions -> ALLOWED."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_eval_allow_01",
                    name="allow_query",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.ALLOW,
                    required_permissions=["order:read"],
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        ctx = _eval_context(permissions=["order:read"])
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req))
        assert result.status == PolicyDecisionStatus.ALLOWED

    def test_allow_rule_missing_permission_denied(self):
        """ALLOW rule with missing permission -> DENIED."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_eval_allow_02",
                    name="allow_query",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.ALLOW,
                    required_permissions=["order:read"],
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        ctx = _eval_context(permissions=[])
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req))
        assert result.status == PolicyDecisionStatus.DENIED
        assert "Missing required permission" in (result.reason or "")

    def test_allow_rule_missing_role_denied(self):
        """ALLOW rule with missing role -> DENIED."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_eval_allow_03",
                    name="allow_admin_action",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.ALLOW,
                    required_roles=["admin"],
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        ctx = _eval_context(roles=["viewer"])
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req))
        assert result.status == PolicyDecisionStatus.DENIED
        assert "Missing required role" in (result.reason or "")

    def test_tool_name_matching(self):
        """Rule with tool_name='refund' only matches requests with that tool_name."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_eval_tn_01",
                    name="deny_refund",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.DENY,
                    tool_name="refund",
                    reason="Refund tool blocked",
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        ctx = _eval_context()

        # Matching tool_name -> DENIED
        req_match = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            tool_name="refund",
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req_match))
        assert result.status == PolicyDecisionStatus.DENIED

        # Non-matching tool_name -> ALLOWED (no matching rule)
        req_no_match = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            tool_name="query",
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req_no_match))
        assert result.status == PolicyDecisionStatus.ALLOWED

    def test_risk_level_matching(self):
        """Rule with risk_level='high' only matches requests with that risk_level."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_eval_rl_01",
                    name="deny_high_risk",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.DENY,
                    risk_level="high",
                    reason="High risk blocked",
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        ctx = _eval_context()

        # Matching risk_level -> DENIED
        req_match = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            risk_level="high",
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req_match))
        assert result.status == PolicyDecisionStatus.DENIED

        # Non-matching risk_level -> ALLOWED (no matching rule)
        req_no_match = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            risk_level="low",
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req_no_match))
        assert result.status == PolicyDecisionStatus.ALLOWED

    def test_most_restrictive_wins(self):
        """DENY rule + ALLOW rule both match -> DENIED wins."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_eval_mrw_allow",
                    name="allow_execute",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.ALLOW,
                )
            )
        )
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_eval_mrw_deny",
                    name="deny_execute",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.DENY,
                    reason="Deny overrides allow",
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        ctx = _eval_context()
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(evaluator.evaluate(req))
        assert result.status == PolicyDecisionStatus.DENIED


class TestPolicyEnforcementService:

    def test_allowed_decision_audited(self):
        """ALLOWED decision produces audit event with 'policy.runtime.enforcement.allowed'."""
        store = InMemoryRuntimePolicyStore()
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        audit = InMemoryAuditLogger()
        service = PolicyEnforcementService(evaluator=evaluator, audit_logger=audit)

        ctx = _eval_context()
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(service.enforce(req))
        assert result.status == PolicyDecisionStatus.ALLOWED

        allowed_events = audit.list_events(event_type="policy.runtime.enforcement.allowed")
        assert len(allowed_events) == 1
        assert allowed_events[0].data["decision_id"] == result.decision_id

    def test_denied_decision_audited(self):
        """DENIED decision produces audit event with 'policy.runtime.enforcement.denied'."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_svc_deny_01",
                    name="deny_all",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.DENY,
                    reason="Blocked",
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        audit = InMemoryAuditLogger()
        service = PolicyEnforcementService(evaluator=evaluator, audit_logger=audit)

        ctx = _eval_context()
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(service.enforce(req))
        assert result.status == PolicyDecisionStatus.DENIED

        denied_events = audit.list_events(event_type="policy.runtime.enforcement.denied")
        assert len(denied_events) == 1
        assert denied_events[0].data["decision_id"] == result.decision_id

    def test_approval_required_audited(self):
        """APPROVAL_REQUIRED produces audit event with 'policy.runtime.enforcement.approval_required'."""
        store = InMemoryRuntimePolicyStore()
        _run_async(
            store.create(
                RuntimePolicyRule(
                    rule_id="rpr_svc_ar_01",
                    name="require_approval",
                    action_type=PolicyActionType.TOOL_EXECUTE,
                    effect=RuntimePolicyEffect.REQUIRE_APPROVAL,
                    required_roles=["admin"],
                )
            )
        )
        evaluator = RuntimePolicyEvaluator(policy_store=store)
        audit = InMemoryAuditLogger()
        service = PolicyEnforcementService(evaluator=evaluator, audit_logger=audit)

        ctx = _eval_context(roles=["admin"])
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(service.enforce(req))
        assert result.status == PolicyDecisionStatus.APPROVAL_REQUIRED

        ar_events = audit.list_events(
            event_type="policy.runtime.enforcement.approval_required"
        )
        assert len(ar_events) == 1
        assert ar_events[0].data["decision_id"] == result.decision_id

    def test_evaluator_error_audited(self):
        """Evaluator exception produces audit event with 'policy.runtime.enforcement.error'."""
        evaluator = RuntimePolicyEvaluator(policy_store=None)
        audit = InMemoryAuditLogger()

        # Monkey-patch evaluate to raise
        async def _boom(req):
            raise RuntimeError("boom")

        evaluator.evaluate = _boom  # type: ignore[assignment]
        service = PolicyEnforcementService(evaluator=evaluator, audit_logger=audit)

        ctx = _eval_context()
        req = RuntimePolicyEvaluationRequest(
            action_type=PolicyActionType.TOOL_EXECUTE,
            context=ctx,
        )
        result = _run_async(service.enforce(req))
        assert result.status == PolicyDecisionStatus.DENIED
        assert "boom" in (result.reason or "")

        error_events = audit.list_events(event_type="policy.runtime.enforcement.error")
        assert len(error_events) == 1
        assert error_events[0].data["error"] == "boom"
