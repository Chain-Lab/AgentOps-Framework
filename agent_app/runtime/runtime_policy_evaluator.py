"""Runtime policy evaluator -- matches requests against rules and produces enforcement decisions.

Phase 38: Rule matching with deny > require_approval > allow priority.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agent_app.core.context import RunContext
from agent_app.governance.policy_enforcement import (
    PolicyActionType,
    PolicyDecisionStatus,
    PolicyEnforcementDecision,
)
from agent_app.governance.runtime_policy import (
    RuntimePolicyEffect,
    RuntimePolicyRule,
    RuntimePolicyRuleStatus,
)
from agent_app.runtime.runtime_policy_store import RuntimePolicyStore


class RuntimePolicyEvaluationRequest(BaseModel):
    """Request for runtime policy evaluation."""

    action_type: PolicyActionType
    subject: str | None = None
    tool_name: str | None = None
    risk_level: str | None = None
    context: RunContext
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimePolicyEvaluator:
    """Evaluates runtime policy rules against requests."""

    def __init__(
        self,
        policy_store: RuntimePolicyStore | None = None,
    ) -> None:
        self._policy_store = policy_store

    async def evaluate(
        self,
        request: RuntimePolicyEvaluationRequest,
    ) -> PolicyEnforcementDecision:
        """Evaluate rules and return a decision.

        Rules:
        1. Load enabled rules matching action_type
        2. Match by tool_name and risk_level if provided
        3. No matching rule -> ALLOWED (no_matching_rule)
        4. Multiple matches -> most restrictive wins:
           a. DENY
           b. REQUIRE_APPROVAL
           c. ALLOW
        5. DENY -> DENIED
        6. REQUIRE_APPROVAL -> check permissions/roles; if missing -> DENIED, if present -> APPROVAL_REQUIRED
        7. ALLOW -> check permissions/roles; if satisfied -> ALLOWED, if missing -> DENIED
        """
        if self._policy_store is None:
            return self._make_decision(
                request=request,
                status=PolicyDecisionStatus.ALLOWED,
                reason="no_policy_store",
            )

        # 1. Load enabled rules matching action_type
        enabled_rules = await self._policy_store.list(
            action_type=request.action_type,
            status=RuntimePolicyRuleStatus.ENABLED,
        )

        # 2. Match by tool_name and risk_level
        matching = self._filter_matching(enabled_rules, request)

        # 3. No matching rule -> ALLOWED
        if not matching:
            return self._make_decision(
                request=request,
                status=PolicyDecisionStatus.ALLOWED,
                reason="no_matching_rule",
            )

        # 4. Most restrictive wins: deny > require_approval > allow
        priority = {
            RuntimePolicyEffect.DENY: 0,
            RuntimePolicyEffect.REQUIRE_APPROVAL: 1,
            RuntimePolicyEffect.ALLOW: 2,
        }
        matching.sort(key=lambda r: priority.get(r.effect, 99))
        winner = matching[0]

        # 5. DENY
        if winner.effect == RuntimePolicyEffect.DENY:
            return self._make_decision(
                request=request,
                status=PolicyDecisionStatus.DENIED,
                reason=winner.reason or f"Denied by rule '{winner.name}'",
                rule=winner,
            )

        # 6. REQUIRE_APPROVAL
        if winner.effect == RuntimePolicyEffect.REQUIRE_APPROVAL:
            perm_ok = self._check_permissions(winner, request.context)
            role_ok = self._check_roles(winner, request.context)
            if not perm_ok:
                return self._make_decision(
                    request=request,
                    status=PolicyDecisionStatus.DENIED,
                    reason=f"Missing required permission(s): {winner.required_permissions}",
                    rule=winner,
                )
            if not role_ok:
                return self._make_decision(
                    request=request,
                    status=PolicyDecisionStatus.DENIED,
                    reason=f"Missing required role. Need one of: {winner.required_roles}",
                    rule=winner,
                )
            return self._make_decision(
                request=request,
                status=PolicyDecisionStatus.APPROVAL_REQUIRED,
                reason=winner.reason or f"Approval required by rule '{winner.name}'",
                rule=winner,
            )

        # 7. ALLOW -- check permissions/roles
        if winner.effect == RuntimePolicyEffect.ALLOW:
            perm_ok = self._check_permissions(winner, request.context)
            role_ok = self._check_roles(winner, request.context)
            if not perm_ok:
                return self._make_decision(
                    request=request,
                    status=PolicyDecisionStatus.DENIED,
                    reason=f"Missing required permission(s): {winner.required_permissions}",
                    rule=winner,
                )
            if not role_ok:
                return self._make_decision(
                    request=request,
                    status=PolicyDecisionStatus.DENIED,
                    reason=f"Missing required role. Need one of: {winner.required_roles}",
                    rule=winner,
                )
            return self._make_decision(
                request=request,
                status=PolicyDecisionStatus.ALLOWED,
                reason=f"Allowed by rule '{winner.name}'",
                rule=winner,
            )

        # Fallback (should never reach here)
        return self._make_decision(
            request=request,
            status=PolicyDecisionStatus.ALLOWED,
            reason="fallback",
        )

    def _filter_matching(
        self,
        rules: list[RuntimePolicyRule],
        request: RuntimePolicyEvaluationRequest,
    ) -> list[RuntimePolicyRule]:
        """Filter rules by tool_name and risk_level if set on the rule."""
        matching = []
        for rule in rules:
            if rule.tool_name is not None and rule.tool_name != request.tool_name:
                continue
            if rule.risk_level is not None and rule.risk_level != request.risk_level:
                continue
            matching.append(rule)
        return matching

    def _check_permissions(
        self,
        rule: RuntimePolicyRule,
        context: RunContext,
    ) -> bool:
        """Check if context has all required permissions."""
        if not rule.required_permissions:
            return True
        return all(perm in context.permissions for perm in rule.required_permissions)

    def _check_roles(
        self,
        rule: RuntimePolicyRule,
        context: RunContext,
    ) -> bool:
        """Check if context has at least one required role."""
        if not rule.required_roles:
            return True
        return any(role in context.roles for role in rule.required_roles)

    def _make_decision(
        self,
        request: RuntimePolicyEvaluationRequest,
        status: PolicyDecisionStatus,
        reason: str | None = None,
        rule: RuntimePolicyRule | None = None,
    ) -> PolicyEnforcementDecision:
        """Build a PolicyEnforcementDecision."""
        return PolicyEnforcementDecision(
            decision_id=f"ped_{uuid.uuid4().hex[:12]}",
            status=status,
            action_type=request.action_type,
            subject=request.subject,
            reason=reason,
            required_permissions=rule.required_permissions if rule else [],
            required_roles=rule.required_roles if rule else [],
            approval_policy=rule.approval_policy if rule else None,
            metadata={
                "rule_id": rule.rule_id if rule else None,
                "rule_name": rule.name if rule else None,
                "tool_name": request.tool_name,
                "risk_level": request.risk_level,
            },
            created_at=datetime.now(timezone.utc),
        )
