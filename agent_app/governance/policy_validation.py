"""Policy config validation — catches misconfigurations before runtime.

Phase 24: Validates PolicyEngineConfig for common errors:
  - Duplicate rule names
  - Invalid actions
  - Unsupported condition fields
  - Conflicting tool_name / tool_name_prefix
  - missing_roles / missing_permissions type mismatches
  - Invalid default_action
  - Negative/zero ttl_seconds
  - enabled=true with no rules (warning)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


_SUPPORTED_CONDITIONS = frozenset({
    "tool_name",
    "tool_name_prefix",
    "risk_level",
    "workflow_name",
    "workflow_type",
    "agent_name",
    "source_agent",
    "target_agent",
    "tenant_id",
    "user_id",
    "roles",
    "missing_roles",
    "permissions",
    "missing_permissions",
})

_VALID_ACTIONS = {"allow", "deny", "require_approval", "audit_only", "rate_limit", "set_ttl"}
_VALID_DEFAULT_ACTIONS = {"allow", "deny", "require_approval", "audit_only"}


class PolicyValidationIssue(BaseModel):
    """A single validation issue found in policy config."""

    level: str = Field(..., description="'error' or 'warning'")
    rule_name: str | None = Field(default=None, description="Rule name if applicable")
    message: str = Field(..., description="Human-readable description")
    path: str | None = Field(default=None, description="JSON path to the issue")


class PolicyValidationResult(BaseModel):
    """Result of validating a policy configuration."""

    valid: bool = Field(..., description="True when no errors (warnings are OK)")
    issues: list[PolicyValidationIssue] = Field(
        default_factory=list, description="All found issues"
    )


def validate_policy_config(cfg: Any) -> PolicyValidationResult:
    """Validate a PolicyEngineConfig instance.

    Args:
        cfg: A PolicyEngineConfig (or dict that can be treated as one).

    Returns:
        PolicyValidationResult with all errors and warnings.
    """
    from agent_app.config.schema import PolicyEngineConfig, PolicyRuleConfig

    # Normalize dict → model
    if isinstance(cfg, dict):
        try:
            cfg = PolicyEngineConfig(**cfg)
        except Exception as exc:
            return PolicyValidationResult(
                valid=False,
                issues=[
                    PolicyValidationIssue(
                        level="error",
                        message=f"Config failed Pydantic validation: {exc}",
                    )
                ],
            )

    issues: list[PolicyValidationIssue] = []

    # 1. Validate default_action
    default_action = getattr(cfg, "default_action", "allow")
    if default_action not in _VALID_DEFAULT_ACTIONS:
        issues.append(PolicyValidationIssue(
            level="error",
            message=(
                f"Invalid default_action '{default_action}'. "
                f"Must be one of: {sorted(_VALID_DEFAULT_ACTIONS)}."
            ),
            path="default_action",
        ))

    # 2. Validate rules
    rules = getattr(cfg, "rules", []) or []
    rule_names: list[str] = []
    for rule in rules:
        # Normalize: might be PolicyRuleConfig or dict
        if isinstance(rule, dict):
            rname = rule.get("name", "?")
            rwhen = rule.get("when", {})
            rthen = rule.get("then", {})
        else:
            rname = rule.name
            rwhen = rule.when
            rthen = rule.then

        rule_names.append(rname)

        # 2a. Rule name uniqueness
        if rule_names.count(rname) > 1:
            issues.append(PolicyValidationIssue(
                level="error",
                rule_name=rname,
                message=f"Duplicate rule name '{rname}'.",
                path=f"rules[{rule_names.index(rname)}].name",
            ))

        # 2b. Validate action in then
        action = rthen.get("action") if isinstance(rthen, dict) else None
        if action not in _VALID_ACTIONS:
            issues.append(PolicyValidationIssue(
                level="error",
                rule_name=rname,
                message=f"Invalid action '{action}' in rule '{rname}'. "
                        f"Must be one of: {sorted(_VALID_ACTIONS)}.",
                path=f"rules[{rule_names.index(rname)}].then.action",
            ))

        # 2c. Validate condition fields
        if isinstance(rwhen, dict):
            invalid_conds = set(rwhen.keys()) - _SUPPORTED_CONDITIONS
            if invalid_conds:
                issues.append(PolicyValidationIssue(
                    level="error",
                    rule_name=rname,
                    message=(
                        f"Unsupported conditions in rule '{rname}': "
                        f"{sorted(invalid_conds)}. Supported: {sorted(_SUPPORTED_CONDITIONS)}."
                    ),
                    path=f"rules[{rule_names.index(rname)}].when",
                ))

            # 2d. tool_name vs tool_name_prefix conflict
            if "tool_name" in rwhen and "tool_name_prefix" in rwhen:
                issues.append(PolicyValidationIssue(
                    level="error",
                    rule_name=rname,
                    message=(
                        f"Rule '{rname}' has both 'tool_name' and 'tool_name_prefix'. "
                        "Use one or the other."
                    ),
                    path=f"rules[{rule_names.index(rname)}].when",
                ))

            # 2e. missing_roles type check
            if "missing_roles" in rwhen and not isinstance(rwhen["missing_roles"], list):
                issues.append(PolicyValidationIssue(
                    level="error",
                    rule_name=rname,
                    message=f"'missing_roles' must be a list in rule '{rname}'.",
                    path=f"rules[{rule_names.index(rname)}].when.missing_roles",
                ))

            # 2f. missing_permissions type check
            if "missing_permissions" in rwhen and not isinstance(rwhen["missing_permissions"], list):
                issues.append(PolicyValidationIssue(
                    level="error",
                    rule_name=rname,
                    message=f"'missing_permissions' must be a list in rule '{rname}'.",
                    path=f"rules[{rule_names.index(rname)}].when.missing_permissions",
                ))

        # 2g. ttl_seconds validation
        if isinstance(rthen, dict) and "ttl_seconds" in rthen:
            ttl = rthen["ttl_seconds"]
            if not isinstance(ttl, int) or ttl <= 0:
                issues.append(PolicyValidationIssue(
                    level="error",
                    rule_name=rname,
                    message=f"'ttl_seconds' must be a positive integer in rule '{rname}'. Got: {ttl}",
                    path=f"rules[{rule_names.index(rname)}].then.ttl_seconds",
                ))

    # 3. enabled=true with no rules → warning
    enabled = getattr(cfg, "enabled", False)
    if enabled and len(rules) == 0:
        issues.append(PolicyValidationIssue(
            level="warning",
            message="Policy engine is enabled but has no rules. All calls will use default_action.",
            path="rules",
        ))

    has_errors = any(i.level == "error" for i in issues)
    return PolicyValidationResult(valid=not has_errors, issues=issues)
