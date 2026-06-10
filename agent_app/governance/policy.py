"""Policy Engine — unified, configurable governance decision layer.

Phase 23: Replaces scattered governance checks with a single policy
evaluation step that produces auditable decisions.

Architecture:
  - PolicyAction / PolicyDecision / PolicyEvaluationContext: data models
  - PolicyEngine: Protocol for engine implementations
  - DefaultPolicyEngine: Replicates Phase 22 default behavior
  - ConfigurablePolicyEngine: Loads rules from YAML config
"""

from __future__ import annotations

import fnmatch
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class PolicyAction(StrEnum):
    """Possible policy decision outcomes."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    SET_TTL = "set_ttl"
    RATE_LIMIT = "rate_limit"
    AUDIT_ONLY = "audit_only"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class PolicyDecision(BaseModel):
    """Result of a policy evaluation.

    Attributes:
        action: The chosen policy action.
        allowed: Convenience flag — True unless action is DENY.
        requires_approval: True when the tool should pause for approval.
        reason: Human-readable explanation for audit logs.
        ttl_seconds: Override approval TTL when action is REQUIRE_APPROVAL.
        rate_limit: Rate-limit parameters when action is RATE_LIMIT.
        metadata: Extra decision metadata (rule name, matched conditions, etc.).
    """

    action: PolicyAction = Field(..., description="Chosen policy action")
    allowed: bool = Field(default=True, description="False when action is DENY")
    requires_approval: bool = Field(default=False, description="Requires human approval")
    reason: str | None = Field(default=None, description="Explanation for audit")
    ttl_seconds: int | None = Field(default=None, description="Approval TTL override")
    rate_limit: dict[str, Any] | None = Field(
        default=None, description="Rate-limit parameters"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Extra decision metadata"
    )


class PolicyEvaluationContext(BaseModel):
    """Context passed to the policy engine for evaluation.

    All fields are populated from the run context and tool spec before
    evaluation.  User-supplied metadata cannot override internal fields
    because internal fields are separate model attributes.
    """

    run_id: str | None = Field(default=None, description="Unique run identifier")
    workflow_name: str | None = Field(default=None, description="Workflow name")
    workflow_type: str | None = Field(default=None, description="Workflow type")
    agent_name: str | None = Field(default=None, description="Current agent name")
    source_agent: str | None = Field(default=None, description="Handoff source agent")
    target_agent: str | None = Field(default=None, description="Handoff target agent")
    tool_name: str | None = Field(default=None, description="Tool being called")
    risk_level: str | None = Field(default=None, description="Tool risk level")
    user_id: str | None = Field(default=None, description="End-user identifier")
    tenant_id: str | None = Field(default=None, description="Tenant identifier")
    roles: list[str] = Field(default_factory=list, description="User/agent roles")
    permissions: list[str] = Field(
        default_factory=list, description="Granted permissions"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Free-form metadata"
    )

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Policy engine protocol
# ---------------------------------------------------------------------------

class PolicyEngine(Protocol):
    """Protocol for policy evaluation engines."""

    async def evaluate_tool_call(
        self,
        context: PolicyEvaluationContext,
    ) -> PolicyDecision:
        """Evaluate whether a tool call should be allowed/denied/require approval."""
        ...

    async def evaluate_approval_resume(
        self,
        context: PolicyEvaluationContext,
    ) -> PolicyDecision:
        """Evaluate whether an approval resume should be allowed."""
        ...


# ---------------------------------------------------------------------------
# DefaultPolicyEngine — Phase 22 behavior compatibility
# ---------------------------------------------------------------------------

class DefaultPolicyEngine:
    """Policy engine that replicates Phase 22 default governance behavior.

    Rules (in order):
      1. Missing required permissions → DENY
      2. requires_approval flag or HIGH/CRITICAL risk → REQUIRE_APPROVAL
      3. Otherwise → ALLOW
    """

    async def evaluate_tool_call(
        self,
        context: PolicyEvaluationContext,
    ) -> PolicyDecision:
        # Check if tool requires approval via metadata (ToolSpec flag)
        tool_requires_approval = bool(context.metadata.get("requires_approval", False))

        # Check permissions
        required_perms: list[str] = list(context.metadata.get("required_permissions", []))
        if required_perms:
            missing = [p for p in required_perms if p not in context.permissions]
            if missing:
                return PolicyDecision(
                    action=PolicyAction.DENY,
                    allowed=False,
                    reason=f"Missing permissions: {', '.join(missing)}",
                    metadata={"missing_permissions": missing},
                )

        # Check risk level / approval flag
        if tool_requires_approval:
            return PolicyDecision(
                action=PolicyAction.REQUIRE_APPROVAL,
                requires_approval=True,
                reason="Tool requires approval (ToolSpec flag)",
            )

        risk = (context.risk_level or "low").lower()
        if risk in ("high", "critical"):
            return PolicyDecision(
                action=PolicyAction.REQUIRE_APPROVAL,
                requires_approval=True,
                reason=f"Risk level '{risk}' requires approval",
                metadata={"risk_level": risk},
            )

        return PolicyDecision(
            action=PolicyAction.ALLOW,
            reason="Default policy: allowed",
        )

    async def evaluate_approval_resume(
        self,
        context: PolicyEvaluationContext,
    ) -> PolicyDecision:
        """Default: always allow resume (existing security checks still apply)."""
        return PolicyDecision(
            action=PolicyAction.ALLOW,
            reason="Default policy: resume allowed",
        )


# ---------------------------------------------------------------------------
# ConfigurablePolicyEngine — YAML-driven rule matching
# ---------------------------------------------------------------------------

# Supported condition keys in `when` blocks
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

# Supported `then` fields
_SUPPORTED_THEN_KEYS = frozenset({
    "action",
    "reason",
    "ttl_seconds",
    "rate_limit",
})

_VALID_ACTIONS = {a.value for a in PolicyAction}


class _RuleValidationError(ValueError):
    pass


def _validate_rule(rule: dict) -> None:
    """Validate a single policy rule dict."""
    if "name" not in rule:
        raise _RuleValidationError("Policy rule missing 'name'.")
    if "when" not in rule:
        raise _RuleValidationError(f"Policy rule '{rule['name']}' missing 'when'.")
    if "then" not in rule:
        raise _RuleValidationError(f"Policy rule '{rule['name']}' missing 'then'.")

    when = rule["when"]
    if not isinstance(when, dict):
        raise _RuleValidationError(f"Policy rule '{rule['name']}': 'when' must be a dict.")

    invalid_conditions = set(when.keys()) - _SUPPORTED_CONDITIONS
    if invalid_conditions:
        raise _RuleValidationError(
            f"Policy rule '{rule['name']}': unsupported conditions: "
            f"{sorted(invalid_conditions)}. Supported: {sorted(_SUPPORTED_CONDITIONS)}."
        )

    then = rule["then"]
    if not isinstance(then, dict):
        raise _RuleValidationError(f"Policy rule '{rule['name']}': 'then' must be a dict.")

    if "action" not in then:
        raise _RuleValidationError(f"Policy rule '{rule['name']}': 'then' missing 'action'.")

    action_val = then["action"]
    if action_val not in _VALID_ACTIONS:
        raise _RuleValidationError(
            f"Policy rule '{rule['name']}': invalid action '{action_val}'. "
            f"Valid: {sorted(_VALID_ACTIONS)}."
        )


class ConfigurablePolicyEngine:
    """Policy engine that evaluates rules loaded from YAML config.

    Rules are matched in order; the first matching rule wins.
    If no rule matches, the ``default_action`` is returned (default: allow).

    Args:
        rules: List of policy rule dicts.  Each rule has:
            - name: str
            - when: dict of conditions
            - then: dict of actions
        default_action: Fallback action when no rule matches.
    """

    def __init__(
        self,
        rules: list[dict] | None = None,
        default_action: str = "allow",
    ) -> None:
        self._rules: list[dict] = []
        self.default_action = default_action

        if rules:
            for rule in rules:
                _validate_rule(rule)
            self._rules = list(rules)

    def add_rule(self, rule: dict) -> None:
        """Add and validate a single rule."""
        _validate_rule(rule)
        self._rules.append(rule)

    async def evaluate_tool_call(
        self,
        context: PolicyEvaluationContext,
    ) -> PolicyDecision:
        for rule in self._rules:
            if self._matches(rule["when"], context):
                then = rule["then"]
                action = PolicyAction(then["action"])
                return PolicyDecision(
                    action=action,
                    allowed=(action != PolicyAction.DENY),
                    requires_approval=(action == PolicyAction.REQUIRE_APPROVAL),
                    reason=then.get("reason"),
                    ttl_seconds=then.get("ttl_seconds"),
                    rate_limit=then.get("rate_limit"),
                    metadata={"rule_name": rule["name"]},
                )

        # No match — use default
        return PolicyDecision(
            action=PolicyAction(self.default_action),
            reason="No matching policy rule; using default action",
        )

    async def evaluate_approval_resume(
        self,
        context: PolicyEvaluationContext,
    ) -> PolicyDecision:
        for rule in self._rules:
            if self._matches(rule["when"], context):
                then = rule["then"]
                action = PolicyAction(then["action"])
                return PolicyDecision(
                    action=action,
                    allowed=(action != PolicyAction.DENY),
                    requires_approval=(action == PolicyAction.REQUIRE_APPROVAL),
                    reason=then.get("reason"),
                    metadata={"rule_name": rule["name"]},
                )

        return PolicyDecision(
            action=PolicyAction(self.default_action),
            reason="No matching resume policy rule; using default",
        )

    def _matches(self, conditions: dict, ctx: PolicyEvaluationContext) -> bool:
        """Return True when all conditions in the rule match the context."""
        for key, expected in conditions.items():
            if key == "tool_name":
                if ctx.tool_name != expected:
                    return False
            elif key == "tool_name_prefix":
                if not (ctx.tool_name or "").startswith(expected):
                    return False
            elif key == "risk_level":
                if (ctx.risk_level or "low").lower() != expected.lower():
                    return False
            elif key == "workflow_name":
                if ctx.workflow_name != expected:
                    return False
            elif key == "workflow_type":
                if ctx.workflow_type != expected:
                    return False
            elif key == "agent_name":
                if ctx.agent_name != expected:
                    return False
            elif key == "source_agent":
                if ctx.source_agent != expected:
                    return False
            elif key == "target_agent":
                if ctx.target_agent != expected:
                    return False
            elif key == "tenant_id":
                if ctx.tenant_id != expected:
                    return False
            elif key == "user_id":
                if ctx.user_id != expected:
                    return False
            elif key == "roles":
                # expected is a list — context must contain ALL expected roles
                if not all(r in ctx.roles for r in expected):
                    return False
            elif key == "missing_roles":
                # DENY if context is MISSING any of the listed roles
                if any(r in ctx.roles for r in expected):
                    return False
            elif key == "permissions":
                if not all(p in ctx.permissions for p in expected):
                    return False
            elif key == "missing_permissions":
                if any(p in ctx.permissions for p in expected):
                    return False
        return True
