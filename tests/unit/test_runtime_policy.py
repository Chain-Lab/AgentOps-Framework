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
