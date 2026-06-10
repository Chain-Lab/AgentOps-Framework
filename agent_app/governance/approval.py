"""Approval models and store — human-in-the-loop approval workflow.

Phase 3: in-memory implementation with full lifecycle support.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field

from agent_app.governance.risk import ApprovalStatus, RiskLevel


class ApprovalRequest(BaseModel):
    """Represents a pending human approval for a tool call.

    Attributes:
        approval_id: Unique approval identifier (apv_ prefix).
        run_id: Associated run ID.
        agent_name: Agent that triggered the approval.
        tool_name: Tool awaiting approval.
        arguments: Tool call arguments.
        risk_level: Risk classification.
        requested_by: Who/what requested the approval.
        tenant_id: Tenant identifier.
        status: Current approval status.
        reason: Optional reason (especially for rejections).
        created_at: When the approval was created.
        resolved_at: When the approval was resolved (approved/rejected).
        resolved_by: Who resolved the approval.
    """

    approval_id: str = Field(..., description="Unique approval ID")
    run_id: str = Field(..., description="Associated run ID")
    agent_name: str | None = Field(default=None, description="Triggering agent")
    tool_name: str = Field(..., description="Tool awaiting approval")
    arguments: dict = Field(default_factory=dict, description="Tool arguments")
    risk_level: str = Field(default=RiskLevel.LOW, description="Risk level")
    requested_by: str | None = Field(default=None, description="Requester")
    tenant_id: str | None = Field(default=None, description="Tenant ID")
    status: str = Field(default=ApprovalStatus.PENDING, description="Approval status")
    reason: str | None = Field(default=None, description="Rejection reason")
    decision_note: str | None = Field(default=None, description="Approval decision note")
    expires_at: datetime | None = Field(default=None, description="Approval expiry time")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Extra metadata")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp",
    )
    resolved_at: datetime | None = Field(default=None, description="Resolution timestamp")
    resolved_by: str | None = Field(default=None, description="Resolver identity")


class ApprovalStore(Protocol):
    """Protocol for persisting approval requests."""

    async def create(self, request: ApprovalRequest) -> ApprovalRequest:
        """Create a new approval request."""
        ...

    async def get(self, approval_id: str) -> ApprovalRequest:
        """Retrieve an approval by ID."""
        ...

    async def approve(
        self,
        approval_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        """Mark an approval as approved."""
        ...

    async def reject(
        self,
        approval_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        """Mark an approval as rejected."""
        ...

    async def list_pending(self, tenant_id: str | None = None) -> list[ApprovalRequest]:
        """List pending approvals, optionally filtered by tenant."""
        ...


class InMemoryApprovalStore:
    """In-memory approval store.

    Suitable for development and testing.  Approval state is lost
    when the process exits.
    """

    def __init__(self, audit_logger: Any = None) -> None:
        self._store: dict[str, ApprovalRequest] = {}
        self._audit_logger = audit_logger

    def _is_expired(self, req: ApprovalRequest) -> bool:
        if req.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= req.expires_at

    async def create(self, request: ApprovalRequest) -> ApprovalRequest:
        if request.approval_id in self._store:
            raise ValueError(f"Approval '{request.approval_id}' already exists.")
        self._store[request.approval_id] = request
        return request

    async def get(self, approval_id: str) -> ApprovalRequest:
        if approval_id not in self._store:
            raise KeyError(f"Approval '{approval_id}' not found.")
        return self._store[approval_id]

    async def approve(
        self,
        approval_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        req = await self.get(approval_id)
        if req.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"Cannot approve: approval '{approval_id}' is already "
                f"{req.status.value}."
            )
        if self._is_expired(req):
            req.status = ApprovalStatus.EXPIRED
            req.resolved_at = datetime.now(timezone.utc)
            req.decision_note = reason or "expired"
            self._store[approval_id] = req
            await self._log_expired_audit(req)
            raise ValueError(
                f"Cannot approve: approval '{approval_id}' has expired."
            )
        req.status = ApprovalStatus.APPROVED
        req.resolved_at = datetime.now(timezone.utc)
        req.resolved_by = approved_by
        req.reason = reason
        req.decision_note = reason
        self._store[approval_id] = req
        return req

    async def reject(
        self,
        approval_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        req = await self.get(approval_id)
        if req.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"Cannot reject: approval '{approval_id}' is already "
                f"{req.status.value}."
            )
        if self._is_expired(req):
            req.status = ApprovalStatus.EXPIRED
            req.resolved_at = datetime.now(timezone.utc)
            req.decision_note = reason or "expired"
            self._store[approval_id] = req
            await self._log_expired_audit(req)
            raise ValueError(
                f"Cannot reject: approval '{approval_id}' has expired."
            )
        req.status = ApprovalStatus.REJECTED
        req.resolved_at = datetime.now(timezone.utc)
        req.resolved_by = rejected_by
        req.reason = reason
        req.decision_note = reason
        self._store[approval_id] = req
        return req

    async def list_pending(self, tenant_id: str | None = None) -> list[ApprovalRequest]:
        pending = [
            req for req in self._store.values()
            if req.status == ApprovalStatus.PENDING and not self._is_expired(req)
        ]
        if tenant_id is not None:
            pending = [req for req in pending if req.tenant_id == tenant_id]
        return sorted(pending, key=lambda r: r.created_at)

    async def _log_expired_audit(self, req: ApprovalRequest) -> None:
        if self._audit_logger is None:
            return
        try:
            from agent_app.governance.audit import AuditEvent
            event = AuditEvent(
                event_id=str(uuid.uuid4()),
                run_id=req.run_id,
                event_type="approval.expired",
                user_id=getattr(req, "requested_by", None),
                tenant_id=req.tenant_id,
                tool_name=req.tool_name,
                approval_id=req.approval_id,
                data={"risk_level": req.risk_level},
            )
            await self._audit_logger.log(event)
        except Exception:
            pass
