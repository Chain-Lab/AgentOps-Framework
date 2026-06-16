"""Runtime policy validation — checks candidate rules for issues before simulation.

Phase 40: Pre-simulation validation of runtime policy rules.
"""
from __future__ import annotations

from enum import StrEnum
from collections import Counter, defaultdict

from pydantic import BaseModel, Field

from agent_app.governance.runtime_policy import RuntimePolicyEffect, RuntimePolicyRule


class PolicyValidationSeverity(StrEnum):
    """Severity level for validation issues."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class PolicyValidationIssue(BaseModel):
    """A single validation issue found in candidate rules."""
    severity: PolicyValidationSeverity
    code: str
    message: str
    rule_id: str | None = None
    field: str | None = None


class PolicyValidationReport(BaseModel):
    """Report of validation issues found in candidate rules."""
    valid: bool
    issues: list[PolicyValidationIssue] = Field(default_factory=list)


class RuntimePolicyValidator:
    """Validates candidate runtime policy rules before simulation."""

    def validate_rules(
        self,
        rules: list[RuntimePolicyRule],
    ) -> PolicyValidationReport:
        """Validate candidate rules and return a report of issues.

        Checks:
        - Duplicate rule names (warning)
        - DENY rule with approval_policy (warning)
        - REQUIRE_APPROVAL rule without approval_policy (warning)
        - Broad rule: no tool_name and no risk_level (warning)
        - Conflicting rules: same action_type/tool/risk with different effects (warning)
        """
        issues: list[PolicyValidationIssue] = []

        # Check duplicate names
        name_counts = Counter(r.name for r in rules)
        for name, count in name_counts.items():
            if count > 1:
                issues.append(PolicyValidationIssue(
                    severity=PolicyValidationSeverity.WARNING,
                    code="duplicate_name",
                    message=f"Duplicate rule name '{name}' appears {count} times",
                ))

        for rule in rules:
            # DENY with approval_policy
            if rule.effect == RuntimePolicyEffect.DENY and rule.approval_policy is not None:
                issues.append(PolicyValidationIssue(
                    severity=PolicyValidationSeverity.WARNING,
                    code="deny_with_approval_policy",
                    message=f"DENY rule '{rule.name}' has approval_policy — approval is never triggered for DENY",
                    rule_id=rule.rule_id,
                ))

            # REQUIRE_APPROVAL without approval_policy
            if rule.effect == RuntimePolicyEffect.REQUIRE_APPROVAL and rule.approval_policy is None:
                issues.append(PolicyValidationIssue(
                    severity=PolicyValidationSeverity.WARNING,
                    code="require_approval_without_policy",
                    message=f"REQUIRE_APPROVAL rule '{rule.name}' has no approval_policy — approval flow may be ambiguous",
                    rule_id=rule.rule_id,
                ))

            # Broad rule (no tool_name and no risk_level)
            if rule.tool_name is None and rule.risk_level is None:
                issues.append(PolicyValidationIssue(
                    severity=PolicyValidationSeverity.WARNING,
                    code="broad_rule",
                    message=f"Rule '{rule.name}' has no tool_name or risk_level — will match all requests for this action_type",
                    rule_id=rule.rule_id,
                ))

        # Check conflicting rules
        _check_conflicts(rules, issues)

        # Report validity — only ERROR severity issues make it invalid
        has_errors = any(i.severity == PolicyValidationSeverity.ERROR for i in issues)
        return PolicyValidationReport(valid=not has_errors, issues=issues)


def _check_conflicts(
    rules: list[RuntimePolicyRule],
    issues: list[PolicyValidationIssue],
) -> None:
    """Check for conflicting rules with same scope but different effects."""
    groups: dict[tuple, list[RuntimePolicyRule]] = defaultdict(list)
    for rule in rules:
        key = (rule.action_type, rule.tool_name, rule.risk_level)
        groups[key].append(rule)

    for key, group in groups.items():
        effects = {r.effect for r in group}
        if len(effects) > 1 and RuntimePolicyEffect.DENY in effects:
            rule_names = [r.name for r in group]
            issues.append(PolicyValidationIssue(
                severity=PolicyValidationSeverity.WARNING,
                code="conflicting_rules",
                message=(
                    f"Conflicting effects for action_type={key[0]}, "
                    f"tool_name={key[1]}, risk_level={key[2]}: "
                    f"{', '.join(rule_names)} — most restrictive (DENY) wins"
                ),
            ))
