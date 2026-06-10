"""Approval decision and run resume service."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from agent_app.core.result import AppRunResult
from agent_app.governance.audit import AuditEvent, AuditLogger
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
    ) -> None:
        self.app = app
        self.approval_store = approval_store
        self.run_state_store = run_state_store
        self.backend = backend
        self.agent_registry = agent_registry
        self.audit_logger = audit_logger

    async def approve_and_resume(
        self,
        approval_id: str,
        decided_by: str,
        decision_note: str | None = None,
    ) -> AppRunResult:
        """Approve an approval request and resume its interrupted run."""
        try:
            approval = await self.approval_store.approve(
                approval_id,
                decided_by,
                decision_note,
            )
        except KeyError:
            return AppRunResult(
                run_id="",
                status="failed",
                error={
                    "type": "approval_not_found",
                    "message": f"Approval '{approval_id}' not found.",
                },
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
    ) -> AppRunResult:
        """Reject an approval request without resuming backend execution."""
        try:
            approval = await self.approval_store.reject(approval_id, decided_by, reason)
        except KeyError:
            return AppRunResult(
                run_id="",
                status="failed",
                error={
                    "type": "approval_not_found",
                    "message": f"Approval '{approval_id}' not found.",
                },
            )

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
