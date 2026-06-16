"""Policy enforcement service -- wraps evaluator with audit logging.

Phase 38: Writes audit events for every enforcement decision.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agent_app.core.context import RunContext
from agent_app.governance.policy_enforcement import (
    PolicyDecisionStatus,
    PolicyEnforcementDecision,
)
from agent_app.runtime.runtime_policy_evaluator import (
    RuntimePolicyEvaluationRequest,
    RuntimePolicyEvaluator,
)


class PolicyEnforcementService:
    """Wraps RuntimePolicyEvaluator with audit logging."""

    def __init__(
        self,
        evaluator: RuntimePolicyEvaluator,
        audit_logger: Any | None = None,
        event_store: Any | None = None,
    ) -> None:
        self._evaluator = evaluator
        self._audit_logger = audit_logger
        self._event_store = event_store

    async def enforce(
        self,
        request: RuntimePolicyEvaluationRequest,
    ) -> PolicyEnforcementDecision:
        """Evaluate policy and write audit event for the decision."""
        try:
            decision = await self._evaluator.evaluate(request)
        except Exception as exc:
            await self._audit_error(request, str(exc))
            return PolicyEnforcementDecision(
                decision_id=f"ped_{uuid.uuid4().hex[:12]}",
                status=PolicyDecisionStatus.DENIED,
                action_type=request.action_type,
                subject=request.subject,
                reason=f"Evaluation error: {exc}",
                metadata={"error": str(exc)},
                created_at=datetime.now(timezone.utc),
            )

        await self._audit_decision(request, decision)

        if self._event_store is not None:
            await self._emit_change_event(request, decision)

        return decision

    async def _audit_decision(
        self,
        request: RuntimePolicyEvaluationRequest,
        decision: PolicyEnforcementDecision,
    ) -> None:
        """Write audit event for a policy decision."""
        if self._audit_logger is None:
            return

        event_type_map = {
            PolicyDecisionStatus.ALLOWED: "policy.runtime.enforcement.allowed",
            PolicyDecisionStatus.DENIED: "policy.runtime.enforcement.denied",
            PolicyDecisionStatus.APPROVAL_REQUIRED: "policy.runtime.enforcement.approval_required",
        }
        event_type = event_type_map.get(decision.status, "policy.runtime.evaluated")

        try:
            from agent_app.governance.audit import AuditEvent

            audit_event = AuditEvent(
                event_id=f"ae_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                run_id=getattr(request.context, "run_id", None),
                user_id=getattr(request.context, "user_id", None),
                tenant_id=getattr(request.context, "tenant_id", None),
                tool_name=request.tool_name,
                data={
                    "decision_id": decision.decision_id,
                    "action_type": decision.action_type.value,
                    "subject": decision.subject,
                    "reason": decision.reason,
                    "roles": list(getattr(request.context, "roles", [])),
                    "permissions": list(getattr(request.context, "permissions", [])),
                    "required_permissions": decision.required_permissions,
                    "required_roles": decision.required_roles,
                },
            )
            await self._audit_logger.log(audit_event)
        except Exception:
            pass

    async def _audit_error(
        self,
        request: RuntimePolicyEvaluationRequest,
        error: str,
    ) -> None:
        """Write audit event for an evaluator error."""
        if self._audit_logger is None:
            return
        try:
            from agent_app.governance.audit import AuditEvent

            audit_event = AuditEvent(
                event_id=f"ae_{uuid.uuid4().hex[:12]}",
                event_type="policy.runtime.enforcement.error",
                run_id=getattr(request.context, "run_id", None),
                user_id=getattr(request.context, "user_id", None),
                tenant_id=getattr(request.context, "tenant_id", None),
                tool_name=request.tool_name,
                data={
                    "error": error,
                    "action_type": request.action_type.value,
                },
            )
            await self._audit_logger.log(audit_event)
        except Exception:
            pass

    async def _emit_change_event(
        self,
        request: RuntimePolicyEvaluationRequest,
        decision: PolicyEnforcementDecision,
    ) -> None:
        """Emit change event (best-effort)."""
        try:
            from agent_app.governance.policy_change_event import (
                PolicyChangeEvent,
                PolicyChangeEventType,
            )

            event = PolicyChangeEvent(
                event_id=f"pce_{uuid.uuid4().hex[:12]}",
                event_type=PolicyChangeEventType.RUNTIME_POLICY_EVALUATED,
                actor_id=getattr(request.context, "user_id", None),
                data={
                    "decision_id": decision.decision_id,
                    "action_type": decision.action_type.value,
                    "status": decision.status.value,
                    "reason": decision.reason,
                },
                created_at=datetime.now(timezone.utc),
            )
            await self._event_store.append(event)
        except Exception:
            pass
