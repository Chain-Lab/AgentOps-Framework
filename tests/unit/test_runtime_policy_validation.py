"""Tests for runtime policy validation (Phase 40)."""
from __future__ import annotations

import pytest

from agent_app.governance.runtime_policy import RuntimePolicyRule, RuntimePolicyEffect, RuntimePolicyRuleStatus
from agent_app.governance.policy_enforcement import PolicyActionType
from agent_app.runtime.policy_validation import (
    PolicyValidationIssue,
    PolicyValidationReport,
    PolicyValidationSeverity,
    RuntimePolicyValidator,
)


def _make_rule(name: str, effect: RuntimePolicyEffect, **kwargs) -> RuntimePolicyRule:
    return RuntimePolicyRule(
        rule_id=f"rpr_{name}",
        name=name,
        action_type=kwargs.get("action_type", PolicyActionType.TOOL_EXECUTE),
        effect=effect,
        tool_name=kwargs.get("tool_name"),
        risk_level=kwargs.get("risk_level"),
        status=kwargs.get("status", RuntimePolicyRuleStatus.ENABLED),
        approval_policy=kwargs.get("approval_policy"),
    )


class TestPolicyValidationSeverity:
    def test_enum_values(self):
        assert PolicyValidationSeverity.ERROR == "error"
        assert PolicyValidationSeverity.WARNING == "warning"
        assert PolicyValidationSeverity.INFO == "info"


class TestPolicyValidationIssue:
    def test_issue(self):
        issue = PolicyValidationIssue(
            severity=PolicyValidationSeverity.WARNING,
            code="broad_rule",
            message="Rule has no tool_name or risk_level",
            rule_id="rpr_abc",
        )
        assert issue.severity == PolicyValidationSeverity.WARNING
        assert issue.code == "broad_rule"
        assert issue.rule_id == "rpr_abc"


class TestPolicyValidationReport:
    def test_valid_report(self):
        report = PolicyValidationReport(valid=True)
        assert report.valid is True
        assert report.issues == []

    def test_invalid_report(self):
        report = PolicyValidationReport(
            valid=False,
            issues=[
                PolicyValidationIssue(
                    severity=PolicyValidationSeverity.ERROR,
                    code="duplicate_name",
                    message="Duplicate rule name",
                ),
            ],
        )
        assert report.valid is False
        assert len(report.issues) == 1


class TestRuntimePolicyValidator:
    def test_valid_rules_pass(self):
        rules = [
            _make_rule("allow_payments", RuntimePolicyEffect.ALLOW, tool_name="payment.process"),
            _make_rule("deny_refunds", RuntimePolicyEffect.DENY, tool_name="refund.request"),
        ]
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        assert report.valid is True

    def test_duplicate_names_warning(self):
        rules = [
            _make_rule("same_name", RuntimePolicyEffect.ALLOW, tool_name="tool_a"),
            _make_rule("same_name", RuntimePolicyEffect.DENY, tool_name="tool_b"),
        ]
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        dup_issues = [i for i in report.issues if i.code == "duplicate_name"]
        assert len(dup_issues) > 0

    def test_broad_rule_warning(self):
        rules = [
            _make_rule("broad_rule", RuntimePolicyEffect.DENY),
            # No tool_name, no risk_level — should be broad_rule warning
        ]
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        broad_issues = [i for i in report.issues if i.code == "broad_rule"]
        assert len(broad_issues) > 0

    def test_deny_with_approval_policy_warning(self):
        from agent_app.governance.policy_rollout_approval import (
            RolloutApprovalPolicy,
            RolloutApprovalPolicyType,
        )
        rules = [
            RuntimePolicyRule(
                rule_id="rpr_deny_ap",
                name="deny_with_ap",
                action_type=PolicyActionType.TOOL_EXECUTE,
                effect=RuntimePolicyEffect.DENY,
                approval_policy=RolloutApprovalPolicy(
                    policy_type=RolloutApprovalPolicyType.SINGLE,
                    required_approvals=1,
                ),
            ),
        ]
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        ap_issues = [i for i in report.issues if i.code == "deny_with_approval_policy"]
        assert len(ap_issues) > 0

    def test_require_approval_without_policy_warning(self):
        rules = [
            _make_rule("req_ap_no_policy", RuntimePolicyEffect.REQUIRE_APPROVAL, tool_name="some.tool"),
        ]
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        nap_issues = [i for i in report.issues if i.code == "require_approval_without_policy"]
        assert len(nap_issues) > 0

    def test_conflicting_rules_warning(self):
        rules = [
            _make_rule("allow_refunds", RuntimePolicyEffect.ALLOW, tool_name="refund.request"),
            _make_rule("deny_refunds", RuntimePolicyEffect.DENY, tool_name="refund.request"),
        ]
        validator = RuntimePolicyValidator()
        report = validator.validate_rules(rules)
        conflict_issues = [i for i in report.issues if i.code == "conflicting_rules"]
        assert len(conflict_issues) > 0
