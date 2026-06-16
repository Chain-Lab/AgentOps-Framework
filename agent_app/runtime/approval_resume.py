"""Approval decision and run resume service."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from datetime import datetime, timezone

from agent_app.core.result import AppRunResult
from agent_app.governance.audit import AuditEvent, AuditLogger
from agent_app.governance.policy import PolicyEngine, PolicyEvaluationContext
from agent_app.governance.risk import ApprovalStatus

logger = logging.getLogger(__name__)


def _approval_status_value(status: Any) -> str:
    """Return an approval status as a plain string."""
    return str(getattr(status, "value", status))


class ApprovalResumeService:
    """Coordinates approval decisions with persisted run resume."""

    def __init__(
        self,
        *,
        app: Any,
        approval_store: Any,
        run_state_store: Any,
        backend: Any,
        agent_registry: Any,
        audit_logger: AuditLogger | None = None,
        policy_engine: PolicyEngine | None = None,
        policy_enforcement_service: Any | None = None,
    ) -> None:
        self.app = app
        self.approval_store = approval_store
        self.run_state_store = run_state_store
        self.backend = backend
        self.agent_registry = agent_registry
        self.audit_logger = audit_logger
        self.policy_engine = policy_engine
        self._policy_enforcement_service = policy_enforcement_service

    async def approve_and_resume(
        self,
        approval_id: str,
        decided_by: str,
        decision_note: str | None = None,
        tenant_id: str | None = None,
    ) -> AppRunResult:
        """Approve an approval request and resume its interrupted run."""
        try:
            approval = await self.approval_store.get(approval_id)
        except KeyError:
            return AppRunResult(
                run_id="",
                status="failed",
                error={
                    "type": "approval_not_found",
                    "message": f"Approval '{approval_id}' not found.",
                },
            )
        if tenant_id is not None and approval.tenant_id != tenant_id:
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "approval_forbidden",
                    "message": "Approval is not available for this tenant.",
                },
            )

        # Phase 21: TTL check — expired approval cannot be resumed
        if approval.expires_at is not None and datetime.now(timezone.utc) >= approval.expires_at:
            await self._audit(
                event_type="run.resume_blocked",
                run_id=approval.run_id,
                approval_id=approval.approval_id,
                tool_name=approval.tool_name,
                user_id=decided_by,
                tenant_id=approval.tenant_id,
                data={"reason": "approval_expired"},
            )
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "approval_expired",
                    "message": "This approval has expired. Please request a new approval.",
                },
            )

        # Phase 38: Runtime policy enforcement on resume
        if self._policy_enforcement_service is not None:
            from agent_app.core.context import RunContext
            from agent_app.governance.policy_enforcement import (
                PolicyActionType,
                PolicyDecisionStatus,
            )
            from agent_app.runtime.runtime_policy_evaluator import (
                RuntimePolicyEvaluationRequest,
            )

            resume_ctx = RunContext(
                run_id=approval.run_id or "",
                user_id=decided_by,
                tenant_id=approval.tenant_id or "default",
            )

            enforce_request = RuntimePolicyEvaluationRequest(
                action_type=PolicyActionType.TOOL_RESUME,
                subject=f"tool:{approval.tool_name}",
                tool_name=approval.tool_name,
                risk_level=str(approval.risk_level),
                context=resume_ctx,
            )
            enforce_decision = await self._policy_enforcement_service.enforce(
                enforce_request
            )

            if enforce_decision.status == PolicyDecisionStatus.DENIED:
                await self._audit(
                    event_type="run.resume_blocked",
                    run_id=approval.run_id,
                    approval_id=approval.approval_id,
                    tool_name=approval.tool_name,
                    user_id=decided_by,
                    tenant_id=approval.tenant_id,
                    data={
                        "reason": "runtime_policy_denied",
                        "decision_id": enforce_decision.decision_id,
                    },
                )
                return AppRunResult(
                    run_id=approval.run_id or "",
                    status="failed",
                    error={
                        "type": "runtime_policy_denied",
                        "message": enforce_decision.reason
                        or "Resume denied by runtime policy",
                    },
                )

            if enforce_decision.status == PolicyDecisionStatus.APPROVAL_REQUIRED:
                await self._audit(
                    event_type="run.resume_blocked",
                    run_id=approval.run_id,
                    approval_id=approval.approval_id,
                    tool_name=approval.tool_name,
                    user_id=decided_by,
                    tenant_id=approval.tenant_id,
                    data={
                        "reason": "runtime_policy_approval_required",
                        "decision_id": enforce_decision.decision_id,
                    },
                )
                return AppRunResult(
                    run_id=approval.run_id or "",
                    status="interrupted",
                    interruptions=[
                        {
                            "type": "approval_required",
                            "approval_id": approval.approval_id,
                            "tool_name": approval.tool_name,
                            "decision_id": enforce_decision.decision_id,
                        }
                    ],
                    latency_ms=0,
                )

            # ALLOWED -> continue to existing resume flow

        try:
            interrupted = await self.run_state_store.get(approval.run_id)
        except KeyError:
            await self._audit(
                event_type="run.resume_blocked",
                run_id=approval.run_id,
                approval_id=approval.approval_id,
                tool_name=approval.tool_name,
                user_id=decided_by,
                tenant_id=approval.tenant_id,
                data={"reason": "run_state_missing"},
            )
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "run_state_missing",
                    "message": "Run state is missing or no longer resumable.",
                },
            )
        if approval.approval_id not in interrupted.approval_ids:
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "approval_run_mismatch",
                    "message": "Approval is not associated with this interrupted run.",
                },
            )

        # Phase 23: policy evaluation on resume (after existing safety checks)
        if self.policy_engine is not None:
            from agent_app.governance.policy import PolicyAction
            resume_ctx = PolicyEvaluationContext(
                run_id=approval.run_id,
                agent_name=getattr(interrupted, "agent_name", None),
                tool_name=approval.tool_name,
                risk_level=str(approval.risk_level),
                user_id=decided_by,
                tenant_id=approval.tenant_id,
            )
            resume_decision = await self.policy_engine.evaluate_approval_resume(resume_ctx)

            await self._audit(
                event_type="policy.evaluated",
                run_id=approval.run_id,
                approval_id=approval.approval_id,
                tool_name=approval.tool_name,
                user_id=decided_by,
                tenant_id=approval.tenant_id,
                data={
                    "action": resume_decision.action.value,
                    "rule_name": resume_decision.metadata.get("rule_name"),
                    "reason": resume_decision.reason,
                },
            )

            if resume_decision.action == PolicyAction.DENY:
                await self._audit(
                    event_type="policy.denied",
                    run_id=approval.run_id,
                    approval_id=approval.approval_id,
                    tool_name=approval.tool_name,
                    user_id=decided_by,
                    tenant_id=approval.tenant_id,
                    data={
                        "reason": resume_decision.reason,
                        "rule_name": resume_decision.metadata.get("rule_name"),
                    },
                )
                return AppRunResult(
                    run_id=approval.run_id,
                    status="failed",
                    error={
                        "type": "policy_denied",
                        "message": resume_decision.reason or "Resume denied by policy",
                    },
                )

            if resume_decision.action == PolicyAction.REQUIRE_APPROVAL:
                await self._audit(
                    event_type="policy.approval_required",
                    run_id=approval.run_id,
                    approval_id=approval.approval_id,
                    tool_name=approval.tool_name,
                    user_id=decided_by,
                    tenant_id=approval.tenant_id,
                    data={
                        "reason": resume_decision.reason,
                        "rule_name": resume_decision.metadata.get("rule_name"),
                    },
                )
                return AppRunResult(
                    run_id=approval.run_id,
                    status="interrupted",
                    interruptions=interrupted.interruptions,
                    latency_ms=0,
                )

            # ALLOW / AUDIT_ONLY → continue to existing approval flow

        approval = await self.approval_store.approve(
            approval_id,
            decided_by,
            decision_note,
        )

        await self._audit(
            event_type="approval.approved",
            run_id=approval.run_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            user_id=decided_by,
            tenant_id=approval.tenant_id,
            data={"decision_note": decision_note, "risk_level": approval.risk_level},
        )
        await self._audit(
            event_type="run.resume_requested",
            run_id=approval.run_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            user_id=decided_by,
            tenant_id=approval.tenant_id,
            data={"decision": ApprovalStatus.APPROVED.value},
        )

        try:
            interrupted = await self.run_state_store.get(approval.run_id)
        except KeyError:
            await self._audit(
                event_type="run.resume_blocked",
                run_id=approval.run_id,
                approval_id=approval.approval_id,
                tool_name=approval.tool_name,
                user_id=decided_by,
                tenant_id=approval.tenant_id,
                data={"reason": "run_state_missing"},
            )
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "run_state_missing",
                    "message": "Run state is missing or no longer resumable.",
                },
            )

        if await self._pending_approvals(interrupted.approval_ids):
            return AppRunResult(
                run_id=approval.run_id,
                status="interrupted",
                interruptions=interrupted.interruptions,
                latency_ms=0,
            )

        if await self._has_rejection(interrupted.approval_ids):
            await self.run_state_store.mark_completed(approval.run_id)
            return AppRunResult(
                run_id=approval.run_id,
                status="completed",
                final_output=(
                    f"Run '{approval.run_id}' was rejected. "
                    "Reason: No reason provided."
                ),
                latency_ms=0,
            )

        if not interrupted.backend_state:
            await self._audit(
                event_type="run.resume_blocked",
                run_id=approval.run_id,
                approval_id=approval.approval_id,
                tool_name=approval.tool_name,
                user_id=decided_by,
                tenant_id=approval.tenant_id,
                data={"reason": "backend_state_missing"},
            )
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "resume_blocked",
                    "message": "Run state is missing or no longer resumable.",
                },
            )

        try:
            agent_spec = self.agent_registry.get(interrupted.agent_name)
            approvals = await self._approval_decisions(interrupted.approval_ids)
            result = await self.backend.resume(
                agent_spec=agent_spec,
                context=interrupted.context,
                backend_state=interrupted.backend_state,
                approvals=approvals,
                interruptions=interrupted.interruptions,
                rejection_message=None,
            )
        except Exception:
            logger.exception("Approval resume backend call failed")
            await self._audit(
                event_type="run.resume_failed",
                run_id=approval.run_id,
                approval_id=approval.approval_id,
                tool_name=approval.tool_name,
                user_id=decided_by,
                tenant_id=approval.tenant_id,
                data={"error_type": "backend_resume_failed"},
            )
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "backend_resume_failed",
                    "message": "Backend resume failed; check server logs for details.",
                },
            )

        await self.run_state_store.mark_resumed(approval.run_id)
        await self._audit(
            event_type="run.resumed",
            run_id=approval.run_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            user_id=decided_by,
            tenant_id=approval.tenant_id,
            data={"status": result.status},
        )
        return result

    async def reject(
        self,
        approval_id: str,
        decided_by: str,
        reason: str | None = None,
        tenant_id: str | None = None,
    ) -> AppRunResult:
        """Reject an approval request without resuming backend execution."""
        try:
            approval = await self.approval_store.get(approval_id)
        except KeyError:
            return AppRunResult(
                run_id="",
                status="failed",
                error={
                    "type": "approval_not_found",
                    "message": f"Approval '{approval_id}' not found.",
                },
            )
        if tenant_id is not None and approval.tenant_id != tenant_id:
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "approval_forbidden",
                    "message": "Approval is not available for this tenant.",
                },
            )
        try:
            interrupted = await self.run_state_store.get(approval.run_id)
        except KeyError:
            interrupted = None
        if interrupted is not None and approval.approval_id not in interrupted.approval_ids:
            return AppRunResult(
                run_id=approval.run_id,
                status="failed",
                error={
                    "type": "approval_run_mismatch",
                    "message": "Approval is not associated with this interrupted run.",
                },
            )

        approval = await self.approval_store.reject(approval_id, decided_by, reason)

        await self._audit(
            event_type="approval.rejected",
            run_id=approval.run_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            user_id=decided_by,
            tenant_id=approval.tenant_id,
            data={"reason": reason, "risk_level": approval.risk_level},
        )
        try:
            await self.run_state_store.mark_completed(approval.run_id)
        except KeyError:
            pass
        return AppRunResult(
            run_id=approval.run_id,
            status="completed",
            final_output=(
                f"Run '{approval.run_id}' was rejected. "
                f"Reason: {reason or 'No reason provided.'}"
            ),
            latency_ms=0,
        )

    async def _approval_decisions(self, approval_ids: list[str]) -> list[dict[str, str]]:
        decisions: list[dict[str, str]] = []
        for item_id in approval_ids:
            try:
                request = await self.approval_store.get(item_id)
            except KeyError:
                continue
            decisions.append({
                "approval_id": item_id,
                "status": _approval_status_value(request.status),
            })
        return decisions

    async def _pending_approvals(self, approval_ids: list[str]) -> bool:
        for item_id in approval_ids:
            try:
                request = await self.approval_store.get(item_id)
            except KeyError:
                continue
            if _approval_status_value(request.status) == ApprovalStatus.PENDING.value:
                return True
        return False

    async def _has_rejection(self, approval_ids: list[str]) -> bool:
        for item_id in approval_ids:
            try:
                request = await self.approval_store.get(item_id)
            except KeyError:
                continue
            if _approval_status_value(request.status) == ApprovalStatus.REJECTED.value:
                return True
        return False

    async def _audit(
        self,
        *,
        event_type: str,
        run_id: str | None,
        approval_id: str | None,
        tool_name: str | None,
        user_id: str | None,
        tenant_id: str | None,
        data: dict[str, Any],
    ) -> None:
        if self.audit_logger is None:
            return
        await self.audit_logger.log(AuditEvent(
            event_id=str(uuid.uuid4()),
            run_id=run_id,
            event_type=event_type,
            user_id=user_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
            approval_id=approval_id,
            data=data,
        ))
