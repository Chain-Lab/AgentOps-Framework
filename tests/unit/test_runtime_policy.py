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
