"""Federation approval store -- persists FederationApprovalRequest with Protocol + InMemory + SQLite."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalDashboardSummary,
    FederationApprovalRequest,
    FederationApprovalStatus,
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FederationApprovalStore(Protocol):
    """Protocol for persisting federation approval requests."""

    async def create(self, request: FederationApprovalRequest) -> FederationApprovalRequest: ...
    async def get(self, approval_id: str) -> FederationApprovalRequest | None: ...
    async def list(
        self,
        federation_id: str | None = None,
        status: FederationApprovalStatus | None = None,
        tenant_id: str | None = None,
        target_id: str | None = None,
        action: str | None = None,
        environment: str | None = None,
        ring: str | None = None,
    ) -> list[FederationApprovalRequest]: ...
    async def approve(self, approval_id: str, approved_by: str, reason: str | None = None) -> FederationApprovalRequest: ...
    async def reject(self, approval_id: str, rejected_by: str, reason: str | None = None) -> FederationApprovalRequest: ...
    async def escalate(self, approval_id: str, escalated_by: str | None = None, new_required_approvers: list[str] | None = None, reason: str | None = None) -> FederationApprovalRequest: ...
    async def cancel(self, approval_id: str, cancelled_by: str, reason: str | None = None) -> FederationApprovalRequest: ...
    async def expire_pending(self, now: datetime | None = None) -> list[FederationApprovalRequest]: ...
    async def get_dashboard_summary(self, tenant_id: str | None = None) -> FederationApprovalDashboardSummary: ...


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class InMemoryFederationApprovalStore:
    """In-memory federation approval request store."""

    def __init__(self) -> None:
        self._requests: dict[str, FederationApprovalRequest] = {}

    async def create(self, request: FederationApprovalRequest) -> FederationApprovalRequest:
        self._requests[request.approval_id] = request
        return request

    async def get(self, approval_id: str) -> FederationApprovalRequest | None:
        return self._requests.get(approval_id)

    async def list(
        self,
        federation_id: str | None = None,
        status: FederationApprovalStatus | None = None,
        tenant_id: str | None = None,
        target_id: str | None = None,
        action: str | None = None,
        environment: str | None = None,
        ring: str | None = None,
    ) -> list[FederationApprovalRequest]:
        results: list[FederationApprovalRequest] = []
        for req in self._requests.values():
            if federation_id is not None and req.federation_id != federation_id:
                continue
            if status is not None and req.status != status:
                continue
            if tenant_id is not None and req.tenant_id != tenant_id:
                continue
            if target_id is not None and req.target_id != target_id:
                continue
            if action is not None and req.action != action:
                continue
            if environment is not None and req.environment != environment:
                continue
            if ring is not None and req.ring != ring:
                continue
            results.append(req)
        results.sort(key=lambda r: (r.created_at, r.approval_id))
        return results

    async def approve(self, approval_id: str, approved_by: str, reason: str | None = None) -> FederationApprovalRequest:
        req = self._requests.get(approval_id)
        if req is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")
        req.status = FederationApprovalStatus.APPROVED
        if approved_by not in req.approvers_who_approved:
            req.approvers_who_approved.append(approved_by)
        req.resolved_by = approved_by
        req.resolved_at = datetime.now(timezone.utc)
        if reason is not None:
            req.reason = reason
        return req

    async def reject(self, approval_id: str, rejected_by: str, reason: str | None = None) -> FederationApprovalRequest:
        req = self._requests.get(approval_id)
        if req is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")
        req.status = FederationApprovalStatus.REJECTED
        if rejected_by not in req.approvers_who_rejected:
            req.approvers_who_rejected.append(rejected_by)
        req.resolved_by = rejected_by
        req.resolved_at = datetime.now(timezone.utc)
        if reason is not None:
            req.rejection_reason = reason
        return req

    async def escalate(
        self,
        approval_id: str,
        escalated_by: str | None = None,
        new_required_approvers: list[str] | None = None,
        reason: str | None = None,
    ) -> FederationApprovalRequest:
        req = self._requests.get(approval_id)
        if req is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")
        req.escalation_level += 1
        if new_required_approvers:
            for approver in new_required_approvers:
                if approver not in req.required_approvers:
                    req.required_approvers.append(approver)
        if reason is not None:
            req.escalation_reason = reason
        req.status = FederationApprovalStatus.ESCALATED
        return req

    async def cancel(self, approval_id: str, cancelled_by: str, reason: str | None = None) -> FederationApprovalRequest:
        req = self._requests.get(approval_id)
        if req is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")
        req.status = FederationApprovalStatus.CANCELLED
        req.resolved_by = cancelled_by
        req.resolved_at = datetime.now(timezone.utc)
        if reason is not None:
            req.reason = reason
        return req

    async def expire_pending(self, now: datetime | None = None) -> list[FederationApprovalRequest]:
        if now is None:
            now = datetime.now(timezone.utc)
        expired: list[FederationApprovalRequest] = []
        for req in self._requests.values():
            if (
                req.status == FederationApprovalStatus.PENDING
                and req.expires_at is not None
                and now >= req.expires_at
            ):
                req.status = FederationApprovalStatus.EXPIRED
                req.resolved_at = now
                expired.append(req)
        return expired

    async def get_dashboard_summary(self, tenant_id: str | None = None) -> FederationApprovalDashboardSummary:
        requests = await self.list(tenant_id=tenant_id)
        total_pending = 0
        total_approved = 0
        total_rejected = 0
        total_expired = 0
        total_escalated = 0
        total_cancelled = 0
        approval_latencies: list[float] = []
        by_tenant: dict[str, int] = {}
        by_action: dict[str, int] = {}

        for req in requests:
            if req.status == FederationApprovalStatus.PENDING:
                total_pending += 1
                if req.tenant_id is not None:
                    by_tenant[req.tenant_id] = by_tenant.get(req.tenant_id, 0) + 1
                by_action[req.action] = by_action.get(req.action, 0) + 1
            elif req.status == FederationApprovalStatus.APPROVED:
                total_approved += 1
                if req.resolved_at is not None:
                    latency = (req.resolved_at - req.created_at).total_seconds()
                    approval_latencies.append(latency)
            elif req.status == FederationApprovalStatus.REJECTED:
                total_rejected += 1
            elif req.status == FederationApprovalStatus.EXPIRED:
                total_expired += 1
            elif req.status == FederationApprovalStatus.ESCALATED:
                total_escalated += 1
            elif req.status == FederationApprovalStatus.CANCELLED:
                total_cancelled += 1

        average_latency = None
        if approval_latencies:
            average_latency = sum(approval_latencies) / len(approval_latencies)

        return FederationApprovalDashboardSummary(
            total_pending=total_pending,
            total_approved=total_approved,
            total_rejected=total_rejected,
            total_expired=total_expired,
            total_escalated=total_escalated,
            total_cancelled=total_cancelled,
            average_approval_latency_seconds=average_latency,
            by_tenant=by_tenant,
            by_action=by_action,
            blocked_federation_actions=total_pending,
        )


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class SQLiteFederationApprovalStore:
    """SQLite-backed federation approval request store."""

    def __init__(self, db_path: str = ".agent_app/federation_approvals.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS federation_approval (
                approval_id TEXT PRIMARY KEY,
                federation_id TEXT NOT NULL,
                rollout_id TEXT,
                target_id TEXT,
                wave_id TEXT,
                tenant_id TEXT,
                environment TEXT,
                region TEXT,
                ring TEXT,
                action TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                required_approvers_json TEXT NOT NULL DEFAULT '[]',
                delegated_approvers_json TEXT NOT NULL DEFAULT '[]',
                approvers_who_approved_json TEXT NOT NULL DEFAULT '[]',
                approvers_who_rejected_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL,
                reason TEXT,
                rejection_reason TEXT,
                escalation_level INTEGER NOT NULL DEFAULT 0,
                escalation_reason TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by TEXT,
                expires_at TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_fa_federation ON federation_approval(federation_id);
            CREATE INDEX IF NOT EXISTS idx_fa_status ON federation_approval(status);
            CREATE INDEX IF NOT EXISTS idx_fa_tenant ON federation_approval(tenant_id);
            CREATE INDEX IF NOT EXISTS idx_fa_action ON federation_approval(action);
        """)
        self._conn.commit()

    async def create(self, request: FederationApprovalRequest) -> FederationApprovalRequest:
        self._conn.execute(
            """INSERT INTO federation_approval
               (approval_id, federation_id, rollout_id, target_id, wave_id,
                tenant_id, environment, region, ring, action,
                requested_by, required_approvers_json, delegated_approvers_json,
                approvers_who_approved_json, approvers_who_rejected_json,
                status, reason, rejection_reason,
                escalation_level, escalation_reason,
                created_at, resolved_at, resolved_by, expires_at,
                metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request.approval_id,
                request.federation_id,
                request.rollout_id,
                request.target_id,
                request.wave_id,
                request.tenant_id,
                request.environment,
                request.region,
                request.ring,
                request.action,
                request.requested_by,
                json.dumps(request.required_approvers),
                json.dumps(request.delegated_approvers),
                json.dumps(request.approvers_who_approved),
                json.dumps(request.approvers_who_rejected),
                request.status.value,
                request.reason,
                request.rejection_reason,
                request.escalation_level,
                request.escalation_reason,
                request.created_at.isoformat(),
                request.resolved_at.isoformat() if request.resolved_at else None,
                request.resolved_by,
                request.expires_at.isoformat() if request.expires_at else None,
                json.dumps(request.metadata),
            ),
        )
        self._conn.commit()
        return request

    async def get(self, approval_id: str) -> FederationApprovalRequest | None:
        row = self._conn.execute(
            "SELECT * FROM federation_approval WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_request(row)

    async def list(
        self,
        federation_id: str | None = None,
        status: FederationApprovalStatus | None = None,
        tenant_id: str | None = None,
        target_id: str | None = None,
        action: str | None = None,
        environment: str | None = None,
        ring: str | None = None,
    ) -> list[FederationApprovalRequest]:
        clauses: list[str] = []
        params: list[object] = []
        if federation_id is not None:
            clauses.append("federation_id=?")
            params.append(federation_id)
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        if tenant_id is not None:
            clauses.append("tenant_id=?")
            params.append(tenant_id)
        if target_id is not None:
            clauses.append("target_id=?")
            params.append(target_id)
        if action is not None:
            clauses.append("action=?")
            params.append(action)
        if environment is not None:
            clauses.append("environment=?")
            params.append(environment)
        if ring is not None:
            clauses.append("ring=?")
            params.append(ring)
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM federation_approval{where} ORDER BY created_at ASC, approval_id ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_request(row) for row in rows]

    async def approve(self, approval_id: str, approved_by: str, reason: str | None = None) -> FederationApprovalRequest:
        row = self._conn.execute(
            "SELECT * FROM federation_approval WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")
        req = self._row_to_request(row)
        req.status = FederationApprovalStatus.APPROVED
        if approved_by not in req.approvers_who_approved:
            req.approvers_who_approved.append(approved_by)
        req.resolved_by = approved_by
        req.resolved_at = datetime.now(timezone.utc)
        if reason is not None:
            req.reason = reason
        self._conn.execute(
            """UPDATE federation_approval
               SET status=?, approvers_who_approved_json=?, resolved_by=?, resolved_at=?, reason=?
               WHERE approval_id=?""",
            (
                FederationApprovalStatus.APPROVED.value,
                json.dumps(req.approvers_who_approved),
                approved_by,
                req.resolved_at.isoformat(),
                req.reason,
                approval_id,
            ),
        )
        self._conn.commit()
        return req

    async def reject(self, approval_id: str, rejected_by: str, reason: str | None = None) -> FederationApprovalRequest:
        row = self._conn.execute(
            "SELECT * FROM federation_approval WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")
        req = self._row_to_request(row)
        req.status = FederationApprovalStatus.REJECTED
        if rejected_by not in req.approvers_who_rejected:
            req.approvers_who_rejected.append(rejected_by)
        req.resolved_by = rejected_by
        req.resolved_at = datetime.now(timezone.utc)
        if reason is not None:
            req.rejection_reason = reason
        self._conn.execute(
            """UPDATE federation_approval
               SET status=?, approvers_who_rejected_json=?, resolved_by=?, resolved_at=?, rejection_reason=?
               WHERE approval_id=?""",
            (
                FederationApprovalStatus.REJECTED.value,
                json.dumps(req.approvers_who_rejected),
                rejected_by,
                req.resolved_at.isoformat(),
                req.rejection_reason,
                approval_id,
            ),
        )
        self._conn.commit()
        return req

    async def escalate(
        self,
        approval_id: str,
        escalated_by: str | None = None,
        new_required_approvers: list[str] | None = None,
        reason: str | None = None,
    ) -> FederationApprovalRequest:
        row = self._conn.execute(
            "SELECT * FROM federation_approval WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")
        req = self._row_to_request(row)
        req.escalation_level += 1
        if new_required_approvers:
            for approver in new_required_approvers:
                if approver not in req.required_approvers:
                    req.required_approvers.append(approver)
        if reason is not None:
            req.escalation_reason = reason
        req.status = FederationApprovalStatus.ESCALATED
        self._conn.execute(
            """UPDATE federation_approval
               SET escalation_level=?, required_approvers_json=?, escalation_reason=?, status=?
               WHERE approval_id=?""",
            (
                req.escalation_level,
                json.dumps(req.required_approvers),
                req.escalation_reason,
                FederationApprovalStatus.ESCALATED.value,
                approval_id,
            ),
        )
        self._conn.commit()
        return req

    async def cancel(self, approval_id: str, cancelled_by: str, reason: str | None = None) -> FederationApprovalRequest:
        row = self._conn.execute(
            "SELECT * FROM federation_approval WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Federation approval request '{approval_id}' not found")
        req = self._row_to_request(row)
        req.status = FederationApprovalStatus.CANCELLED
        req.resolved_by = cancelled_by
        req.resolved_at = datetime.now(timezone.utc)
        if reason is not None:
            req.reason = reason
        self._conn.execute(
            """UPDATE federation_approval
               SET status=?, resolved_by=?, resolved_at=?, reason=?
               WHERE approval_id=?""",
            (
                FederationApprovalStatus.CANCELLED.value,
                cancelled_by,
                req.resolved_at.isoformat(),
                req.reason,
                approval_id,
            ),
        )
        self._conn.commit()
        return req

    async def expire_pending(self, now: datetime | None = None) -> list[FederationApprovalRequest]:
        if now is None:
            now = datetime.now(timezone.utc)
        rows = self._conn.execute(
            "SELECT * FROM federation_approval WHERE status=? AND expires_at IS NOT NULL",
            (FederationApprovalStatus.PENDING.value,),
        ).fetchall()
        expired: list[FederationApprovalRequest] = []
        for row in rows:
            req = self._row_to_request(row)
            if now >= req.expires_at:
                self._conn.execute(
                    "UPDATE federation_approval SET status=?, resolved_at=? WHERE approval_id=?",
                    (
                        FederationApprovalStatus.EXPIRED.value,
                        now.isoformat(),
                        req.approval_id,
                    ),
                )
                req.status = FederationApprovalStatus.EXPIRED
                req.resolved_at = now
                expired.append(req)
        if expired:
            self._conn.commit()
        return expired

    async def get_dashboard_summary(self, tenant_id: str | None = None) -> FederationApprovalDashboardSummary:
        requests = await self.list(tenant_id=tenant_id)
        total_pending = 0
        total_approved = 0
        total_rejected = 0
        total_expired = 0
        total_escalated = 0
        total_cancelled = 0
        approval_latencies: list[float] = []
        by_tenant: dict[str, int] = {}
        by_action: dict[str, int] = {}

        for req in requests:
            if req.status == FederationApprovalStatus.PENDING:
                total_pending += 1
                if req.tenant_id is not None:
                    by_tenant[req.tenant_id] = by_tenant.get(req.tenant_id, 0) + 1
                by_action[req.action] = by_action.get(req.action, 0) + 1
            elif req.status == FederationApprovalStatus.APPROVED:
                total_approved += 1
                if req.resolved_at is not None:
                    latency = (req.resolved_at - req.created_at).total_seconds()
                    approval_latencies.append(latency)
            elif req.status == FederationApprovalStatus.REJECTED:
                total_rejected += 1
            elif req.status == FederationApprovalStatus.EXPIRED:
                total_expired += 1
            elif req.status == FederationApprovalStatus.ESCALATED:
                total_escalated += 1
            elif req.status == FederationApprovalStatus.CANCELLED:
                total_cancelled += 1

        average_latency = None
        if approval_latencies:
            average_latency = sum(approval_latencies) / len(approval_latencies)

        return FederationApprovalDashboardSummary(
            total_pending=total_pending,
            total_approved=total_approved,
            total_rejected=total_rejected,
            total_expired=total_expired,
            total_escalated=total_escalated,
            total_cancelled=total_cancelled,
            average_approval_latency_seconds=average_latency,
            by_tenant=by_tenant,
            by_action=by_action,
            blocked_federation_actions=total_pending,
        )

    def _row_to_request(self, row: sqlite3.Row) -> FederationApprovalRequest:
        data = dict(row)
        data["status"] = FederationApprovalStatus(data["status"])
        data["required_approvers"] = json.loads(data.pop("required_approvers_json"))
        data["delegated_approvers"] = json.loads(data.pop("delegated_approvers_json"))
        data["approvers_who_approved"] = json.loads(data.pop("approvers_who_approved_json"))
        data["approvers_who_rejected"] = json.loads(data.pop("approvers_who_rejected_json"))
        data["metadata"] = json.loads(data.pop("metadata_json"))
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data["resolved_at"] is not None:
            data["resolved_at"] = datetime.fromisoformat(data["resolved_at"])
        if data["expires_at"] is not None:
            data["expires_at"] = datetime.fromisoformat(data["expires_at"])
        return FederationApprovalRequest(**data)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_federation_approval_store(
    type: str = "memory",
    path: str | None = None,
) -> FederationApprovalStore:
    """Factory for creating federation approval store instances."""
    if type == "memory":
        return InMemoryFederationApprovalStore()
    elif type == "sqlite":
        return SQLiteFederationApprovalStore(db_path=path or ".agent_app/federation_approvals.db")
    else:
        raise ValueError(f"Unknown federation approval store type: {type}")
