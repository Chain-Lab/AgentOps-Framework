"""Federation approval policy evaluation service.

Phase 48: Decides whether federated rollout actions require approval,
creates approval requests, enforces required/delegated approvers, and
supports escalation policies.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalPolicy,
    FederationApprovalRequest,
    FederationApprovalStatus,
)
from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
from agent_app.runtime.policy_rollout_federation_approval_store import FederationApprovalStore

logger = logging.getLogger(__name__)


class FederationApprovalService:
    """Evaluates federation approval policy and manages approval lifecycle.

    Responsibilities:
    - Decide whether a federated rollout action requires approval
    - Create approval requests before sensitive actions
    - Enforce required approvers and delegated approvers
    - Support escalation policies
    - Integrate audit events, change events, and federation history recorder
    """

    def __init__(
        self,
        approval_store: FederationApprovalStore,
        approval_policy: FederationApprovalPolicy,
        audit_logger: Any | None = None,
        change_event_store: Any | None = None,
        federation_history_recorder: Any | None = None,
    ) -> None:
        self._store = approval_store
        self._policy = approval_policy
        self._audit_logger = audit_logger
        self._change_event_store = change_event_store
        self._history_recorder = federation_history_recorder

    # ------------------------------------------------------------------
    # Policy evaluation
    # ------------------------------------------------------------------

    async def requires_approval(self, action: str) -> bool:
        """Check if the given action requires approval based on policy."""
        if not self._policy.enabled:
            return False
        return action in self._policy.require_approval_for

    # ------------------------------------------------------------------
    # Approval request creation
    # ------------------------------------------------------------------

    async def create_approval_request(
        self,
        *,
        federation_id: str,
        action: str,
        requested_by: str,
        rollout_id: str | None = None,
        target_id: str | None = None,
        wave_id: str | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        region: str | None = None,
        ring: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FederationApprovalRequest:
        """Create a new approval request for a federation action."""
        now = datetime.now(timezone.utc)
        approval_id = f"fap_{uuid.uuid4().hex[:16]}"

        expires_at: datetime | None = None
        if self._policy.escalation_enabled:
            expires_at = now + timedelta(minutes=self._policy.escalation_after_minutes)

        request = FederationApprovalRequest(
            approval_id=approval_id,
            federation_id=federation_id,
            rollout_id=rollout_id,
            target_id=target_id,
            wave_id=wave_id,
            tenant_id=tenant_id,
            environment=environment,
            region=region,
            ring=ring,
            action=action,
            requested_by=requested_by,
            required_approvers=list(self._policy.default_required_approvers),
            delegated_approvers=[],
            approvers_who_approved=[],
            approvers_who_rejected=[],
            status=FederationApprovalStatus.PENDING,
            reason=reason,
            created_at=now,
            expires_at=expires_at,
            metadata=metadata or {},
        )

        result = await self._store.create(request)

        # Audit event
        await self._audit(
            event_type="policy.federation.approval.created",
            data={
                "approval_id": approval_id,
                "federation_id": federation_id,
                "action": action,
                "requested_by": requested_by,
            },
        )

        # Federation history event
        await self._record_history(
            event_type=FederationHistoryEventType.APPROVAL_CREATED,
            federation_id=federation_id,
            target_id=target_id,
            rollout_id=rollout_id,
            wave_id=wave_id,
            tenant_id=tenant_id,
            environment=environment,
            region=region,
            ring_name=ring,
            actor_id=requested_by,
            message=f"Approval request created for {action}",
            metadata={"approval_id": approval_id},
        )

        return result

    # ------------------------------------------------------------------
    # Approve / Reject / Escalate / Cancel
    # ------------------------------------------------------------------

    async def approve(
        self,
        approval_id: str,
        actor_id: str,
        reason: str | None = None,
    ) -> FederationApprovalRequest:
        """Approve a pending request. Validates actor is in required_approvers or delegated_approvers."""
        request = await self._store.get(approval_id)
        if request is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")

        if request.status not in (FederationApprovalStatus.PENDING, FederationApprovalStatus.ESCALATED):
            raise ValueError(
                f"Cannot approve request in status {request.status.value!r}"
            )

        if not self._is_authorized_approver(request, actor_id):
            await self._audit(
                event_type="policy.federation.approval.permission_denied",
                data={
                    "approval_id": approval_id,
                    "actor_id": actor_id,
                    "action": "approve",
                },
            )
            raise PermissionError(
                f"Actor '{actor_id}' is not authorized to approve request '{approval_id}'"
            )

        result = await self._store.approve(approval_id, actor_id, reason=reason)

        await self._audit(
            event_type="policy.federation.approval.approved",
            data={
                "approval_id": approval_id,
                "federation_id": result.federation_id,
                "action": result.action,
                "approved_by": actor_id,
            },
        )

        await self._record_history(
            event_type=FederationHistoryEventType.APPROVAL_APPROVED,
            federation_id=result.federation_id,
            target_id=result.target_id,
            rollout_id=result.rollout_id,
            wave_id=result.wave_id,
            tenant_id=result.tenant_id,
            environment=result.environment,
            region=result.region,
            ring_name=result.ring,
            actor_id=actor_id,
            message=f"Approval request approved for {result.action}",
            metadata={"approval_id": approval_id},
        )

        return result

    async def reject(
        self,
        approval_id: str,
        actor_id: str,
        reason: str | None = None,
    ) -> FederationApprovalRequest:
        """Reject a pending request. Validates actor is authorized."""
        request = await self._store.get(approval_id)
        if request is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")

        if request.status not in (FederationApprovalStatus.PENDING, FederationApprovalStatus.ESCALATED):
            raise ValueError(
                f"Cannot reject request in status {request.status.value!r}"
            )

        if not self._is_authorized_approver(request, actor_id):
            await self._audit(
                event_type="policy.federation.approval.permission_denied",
                data={
                    "approval_id": approval_id,
                    "actor_id": actor_id,
                    "action": "reject",
                },
            )
            raise PermissionError(
                f"Actor '{actor_id}' is not authorized to reject request '{approval_id}'"
            )

        result = await self._store.reject(approval_id, actor_id, reason=reason)

        await self._audit(
            event_type="policy.federation.approval.rejected",
            data={
                "approval_id": approval_id,
                "federation_id": result.federation_id,
                "action": result.action,
                "rejected_by": actor_id,
            },
        )

        await self._record_history(
            event_type=FederationHistoryEventType.APPROVAL_REJECTED,
            federation_id=result.federation_id,
            target_id=result.target_id,
            rollout_id=result.rollout_id,
            wave_id=result.wave_id,
            tenant_id=result.tenant_id,
            environment=result.environment,
            region=result.region,
            ring_name=result.ring,
            actor_id=actor_id,
            message=f"Approval request rejected for {result.action}",
            metadata={"approval_id": approval_id},
        )

        return result

    async def escalate(
        self,
        approval_id: str,
        escalated_by: str | None = None,
        reason: str | None = None,
    ) -> FederationApprovalRequest:
        """Escalate a pending request. Adds escalate_to approvers if configured."""
        request = await self._store.get(approval_id)
        if request is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")

        if request.status != FederationApprovalStatus.PENDING:
            raise ValueError(
                f"Cannot escalate request in status {request.status.value!r}"
            )

        new_approvers = list(self._policy.escalate_to) if self._policy.escalate_to else None

        result = await self._store.escalate(
            approval_id,
            escalated_by=escalated_by,
            new_required_approvers=new_approvers,
            reason=reason,
        )

        await self._audit(
            event_type="policy.federation.approval.escalated",
            data={
                "approval_id": approval_id,
                "federation_id": result.federation_id,
                "action": result.action,
                "escalated_by": escalated_by,
                "escalation_level": result.escalation_level,
                "new_approvers": new_approvers,
            },
        )

        await self._record_history(
            event_type=FederationHistoryEventType.APPROVAL_ESCALATED,
            federation_id=result.federation_id,
            target_id=result.target_id,
            rollout_id=result.rollout_id,
            wave_id=result.wave_id,
            tenant_id=result.tenant_id,
            environment=result.environment,
            region=result.region,
            ring_name=result.ring,
            actor_id=escalated_by,
            message=f"Approval request escalated for {result.action}",
            metadata={
                "approval_id": approval_id,
                "escalation_level": result.escalation_level,
            },
        )

        return result

    async def cancel(
        self,
        approval_id: str,
        cancelled_by: str,
        reason: str | None = None,
    ) -> FederationApprovalRequest:
        """Cancel a pending request."""
        request = await self._store.get(approval_id)
        if request is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")

        result = await self._store.cancel(approval_id, cancelled_by, reason=reason)

        await self._audit(
            event_type="policy.federation.approval.cancelled",
            data={
                "approval_id": approval_id,
                "federation_id": result.federation_id,
                "action": result.action,
                "cancelled_by": cancelled_by,
            },
        )

        await self._record_history(
            event_type=FederationHistoryEventType.APPROVAL_CANCELLED,
            federation_id=result.federation_id,
            target_id=result.target_id,
            rollout_id=result.rollout_id,
            wave_id=result.wave_id,
            tenant_id=result.tenant_id,
            environment=result.environment,
            region=result.region,
            ring_name=result.ring,
            actor_id=cancelled_by,
            message=f"Approval request cancelled for {result.action}",
            metadata={"approval_id": approval_id},
        )

        return result

    # ------------------------------------------------------------------
    # Status checks
    # ------------------------------------------------------------------

    async def check_approval_status(
        self,
        federation_id: str,
        action: str,
    ) -> FederationApprovalRequest | None:
        """Find the latest approval request for a federation action. Returns None if no request exists."""
        requests = await self._store.list(federation_id=federation_id, action=action)
        if not requests:
            return None
        # Return the latest (last in sorted-by-created_at list)
        return requests[-1]

    async def is_action_approved(
        self,
        federation_id: str,
        action: str,
    ) -> bool:
        """Check if a federation action has been approved.

        Returns True if no approval is required or if approved.
        """
        if not await self.requires_approval(action):
            return True

        latest = await self.check_approval_status(federation_id, action)
        if latest is None:
            return False

        return latest.status == FederationApprovalStatus.APPROVED

    # ------------------------------------------------------------------
    # Delegation
    # ------------------------------------------------------------------

    async def delegate_approval(
        self,
        approval_id: str,
        delegated_by: str,
        delegated_to: str,
        reason: str | None = None,
    ) -> FederationApprovalRequest:
        """Delegate approval authority to another actor. Only works if delegation_enabled in policy."""
        if not self._policy.delegation_enabled:
            raise ValueError("Delegation is not enabled in the approval policy")

        request = await self._store.get(approval_id)
        if request is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")

        if delegated_by not in request.required_approvers:
            raise PermissionError(
                f"Actor '{delegated_by}' is not a required approver and cannot delegate"
            )

        if delegated_to not in request.delegated_approvers:
            request.delegated_approvers.append(delegated_to)

        return request

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_authorized_approver(
        self,
        request: FederationApprovalRequest,
        actor_id: str,
    ) -> bool:
        """Check if actor is in required_approvers or delegated_approvers."""
        return (
            actor_id in request.required_approvers
            or actor_id in request.delegated_approvers
        )

    async def _audit(
        self,
        *,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Record an audit event (best-effort, never raises)."""
        if self._audit_logger is None:
            return
        try:
            from agent_app.governance.audit import AuditEvent
            event = AuditEvent(
                event_id=f"ae_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                data=data,
            )
            await self._audit_logger.log(event)
        except Exception:
            logger.debug("Audit log failed for %s", event_type, exc_info=True)

    async def _record_history(
        self,
        *,
        event_type: FederationHistoryEventType,
        federation_id: str | None = None,
        target_id: str | None = None,
        rollout_id: str | None = None,
        wave_id: str | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        region: str | None = None,
        ring_name: str | None = None,
        actor_id: str | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a federation history event (best-effort, never raises)."""
        if self._history_recorder is None:
            return
        try:
            await self._history_recorder.record(
                event_type=event_type,
                federation_id=federation_id,
                target_id=target_id,
                rollout_id=rollout_id,
                wave_id=wave_id,
                tenant_id=tenant_id,
                environment=environment,
                region=region,
                ring_name=ring_name,
                actor_id=actor_id,
                message=message,
                metadata=metadata,
            )
        except Exception:
            logger.debug("History record failed for %s", event_type.value, exc_info=True)
