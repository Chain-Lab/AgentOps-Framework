"""Phase 38 Task 7: Tests for runtime policy CLI serialization helpers."""

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

# Import the helpers from cli module
from agent_app.cli import _rule_to_dict, _decision_to_dict


class TestRuleToDict:
    """Test _rule_to_dict serialization helper."""

    def test_rule_to_dict_basic(self):
        """_rule_to_dict includes all basic fields."""
        rule = RuntimePolicyRule(
            rule_id="rpr_abc123",
            name="Block dangerous tools",
            action_type=PolicyActionType.TOOL_EXECUTE,
            effect=RuntimePolicyEffect.DENY,
            status=RuntimePolicyRuleStatus.ENABLED,
            tool_name="shell_exec",
            risk_level="high",
            required_permissions=["admin"],
            required_roles=["operator"],
            reason="Too dangerous",
        )
        result = _rule_to_dict(rule)

        assert result["rule_id"] == "rpr_abc123"
        assert result["name"] == "Block dangerous tools"
        assert result["action_type"] == "tool.execute"
        assert result["effect"] == "deny"
        assert result["status"] == "enabled"
        assert result["tool_name"] == "shell_exec"
        assert result["risk_level"] == "high"
        assert result["required_permissions"] == ["admin"]
        assert result["required_roles"] == ["operator"]
        assert result["reason"] == "Too dangerous"

    def test_rule_to_dict_optional_fields_none(self):
        """_rule_to_dict handles None optional fields."""
        rule = RuntimePolicyRule(
            rule_id="rpr_xyz789",
            name="Allow all",
            action_type=PolicyActionType.TOOL_EXECUTE,
            effect=RuntimePolicyEffect.ALLOW,
        )
        result = _rule_to_dict(rule)

        assert result["rule_id"] == "rpr_xyz789"
        assert result["name"] == "Allow all"
        assert result["action_type"] == "tool.execute"
        assert result["effect"] == "allow"
        assert result["status"] == "enabled"  # default
        assert result["tool_name"] is None
        assert result["risk_level"] is None
        assert result["required_permissions"] == []
        assert result["required_roles"] == []
        assert result["reason"] is None
        assert "approval_policy" not in result

    def test_rule_to_dict_with_approval_policy(self):
        """_rule_to_dict includes approval_policy nested dict when present."""
        approval = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
            allowed_approver_roles=["admin", "lead"],
            allowed_approver_permissions=["approve"],
            prohibit_requester_approval=True,
            expires_after_seconds=3600,
        )
        rule = RuntimePolicyRule(
            rule_id="rpr_approval1",
            name="Require quorum",
            action_type=PolicyActionType.TOOL_EXECUTE,
            effect=RuntimePolicyEffect.REQUIRE_APPROVAL,
            approval_policy=approval,
            reason="High-risk action",
        )
        result = _rule_to_dict(rule)

        assert result["effect"] == "require_approval"
        assert "approval_policy" in result
        ap = result["approval_policy"]
        assert ap["policy_type"] == "quorum"
        assert ap["required_approvals"] == 2
        assert ap["allowed_approver_roles"] == ["admin", "lead"]
        assert ap["allowed_approver_permissions"] == ["approve"]
        assert ap["prohibit_requester_approval"] is True
        assert ap["expires_after_seconds"] == 3600


class TestDecisionToDict:
    """Test _decision_to_dict serialization helper."""

    def test_decision_to_dict_basic(self):
        """_decision_to_dict includes all basic fields."""
        decision = PolicyEnforcementDecision(
            decision_id="ped_dec001",
            status=PolicyDecisionStatus.ALLOWED,
            action_type=PolicyActionType.TOOL_EXECUTE,
            subject="tool:deploy",
            reason="Allowed by rule",
            required_permissions=["run_tool"],
            required_roles=["operator"],
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        result = _decision_to_dict(decision)

        assert result["decision_id"] == "ped_dec001"
        assert result["status"] == "allowed"
        assert result["action_type"] == "tool.execute"
        assert result["subject"] == "tool:deploy"
        assert result["reason"] == "Allowed by rule"
        assert result["required_permissions"] == ["run_tool"]
        assert result["required_roles"] == ["operator"]
        assert result["approval_policy"] is None

    def test_decision_to_dict_denied(self):
        """_decision_to_dict handles DENIED status."""
        decision = PolicyEnforcementDecision(
            decision_id="ped_dec002",
            status=PolicyDecisionStatus.DENIED,
            action_type=PolicyActionType.TOOL_RESUME,
            reason="Missing permission",
            created_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
        )
        result = _decision_to_dict(decision)

        assert result["decision_id"] == "ped_dec002"
        assert result["status"] == "denied"
        assert result["action_type"] == "tool.resume"
        assert result["subject"] is None
        assert result["approval_policy"] is None

    def test_decision_to_dict_with_approval_policy(self):
        """_decision_to_dict includes approval_policy nested dict when present."""
        approval = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.SINGLE,
            required_approvals=1,
        )
        decision = PolicyEnforcementDecision(
            decision_id="ped_dec003",
            status=PolicyDecisionStatus.APPROVAL_REQUIRED,
            action_type=PolicyActionType.TOOL_EXECUTE,
            subject="tool:shell_exec",
            reason="Approval required",
            approval_policy=approval,
            created_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
        )
        result = _decision_to_dict(decision)

        assert result["status"] == "approval_required"
        assert result["approval_policy"] is not None
        assert result["approval_policy"]["policy_type"] == "single"
        assert result["approval_policy"]["required_approvals"] == 1
