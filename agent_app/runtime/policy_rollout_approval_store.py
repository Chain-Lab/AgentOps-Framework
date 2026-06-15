"""Rollout step approval store -- persists RolloutStepApproval instances with Protocol + InMemory + SQLite."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agent_app.governance.policy_rollout_approval import (
    RolloutStepApproval,
    RolloutStepApprovalStatus,
)

try:
    from typing import runtime_checkable
except ImportError:
    def runtime_checkable(cls):  # type: ignore[misc]
        return cls


@runtime_checkable
class RolloutStepApprovalStore(Protocol):
    """Protocol for persisting rollout step approvals."""

    async def create(self, approval: RolloutStepApproval) -> RolloutStepApproval: ...
    async def get(self, approval_id: str) -> RolloutStepApproval | None: ...
    async def get_pending_for_step(self, rollout_id: str, step_id: str) -> RolloutStepApproval | None: ...
    async def approve(self, approval_id: str, approved_by: str, reason: str | None = None) -> RolloutStepApproval: ...
    async def reject(self, approval_id: str, rejected_by: str, reason: str | None = None) -> RolloutStepApproval: ...
    async def cancel_for_step(self, rollout_id: str, step_id: str, cancelled_by: str, reason: str | None = None) -> RolloutStepApproval | None: ...
    async def list(
        self,
        status: RolloutStepApprovalStatus | None = None,
        rollout_id: str | None = None,
    ) -> list[RolloutStepApproval]: ...


class InMemoryRolloutStepApprovalStore:
    """In-memory rollout step approval store."""

    def __init__(self) -> None:
        self._approvals: dict[str, RolloutStepApproval] = {}

    async def create(self, approval: RolloutStepApproval) -> RolloutStepApproval:
        existing = await self.get_pending_for_step(approval.rollout_id, approval.step_id)
        if existing is not None:
            return existing
        self._approvals[approval.approval_id] = approval
        return approval

    async def get(self, approval_id: str) -> RolloutStepApproval | None:
        return self._approvals.get(approval_id)

    async def get_pending_for_step(self, rollout_id: str, step_id: str) -> RolloutStepApproval | None:
        for approval in self._approvals.values():
            if (
                approval.rollout_id == rollout_id
                and approval.step_id == step_id
                and approval.status == RolloutStepApprovalStatus.PENDING
            ):
                return approval
        return None

    async def approve(self, approval_id: str, approved_by: str, reason: str | None = None) -> RolloutStepApproval:
        approval = self._approvals.get(approval_id)
        if approval is None:
            raise KeyError(f"Rollout step approval '{approval_id}' not found")
        if approval.status != RolloutStepApprovalStatus.PENDING:
            raise ValueError(f"Cannot approve approval '{approval_id}': status is {approval.status.value}, expected PENDING")
        approval.status = RolloutStepApprovalStatus.APPROVED
        approval.resolved_by = approved_by
        approval.resolved_reason = reason
        approval.resolved_at = datetime.now()
        return approval

    async def reject(self, approval_id: str, rejected_by: str, reason: str | None = None) -> RolloutStepApproval:
        approval = self._approvals.get(approval_id)
        if approval is None:
            raise KeyError(f"Rollout step approval '{approval_id}' not found")
        if approval.status != RolloutStepApprovalStatus.PENDING:
            raise ValueError(f"Cannot reject approval '{approval_id}': status is {approval.status.value}, expected PENDING")
        approval.status = RolloutStepApprovalStatus.REJECTED
        approval.resolved_by = rejected_by
        approval.resolved_reason = reason
        approval.resolved_at = datetime.now()
        return approval

    async def cancel_for_step(self, rollout_id: str, step_id: str, cancelled_by: str, reason: str | None = None) -> RolloutStepApproval | None:
        approval = await self.get_pending_for_step(rollout_id, step_id)
        if approval is None:
            return None
        approval.status = RolloutStepApprovalStatus.CANCELLED
        approval.resolved_by = cancelled_by
        approval.resolved_reason = reason
        approval.resolved_at = datetime.now()
        return approval

    async def list(
        self,
        status: RolloutStepApprovalStatus | None = None,
        rollout_id: str | None = None,
    ) -> list[RolloutStepApproval]:
        results: list[RolloutStepApproval] = []
        for approval in self._approvals.values():
            if status is not None and approval.status != status:
                continue
            if rollout_id is not None and approval.rollout_id != rollout_id:
                continue
            results.append(approval)
        return results


class SQLiteRolloutStepApprovalStore:
    """SQLite-backed rollout step approval store."""

    def __init__(self, db_path: str = ".agent_app/policy_rollout_step_approvals.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_rollout_step_approvals (
                approval_id TEXT PRIMARY KEY,
                rollout_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                bundle_id TEXT NOT NULL,
                environment TEXT NOT NULL,
                ring_name TEXT,
                requested_by TEXT NOT NULL,
                requested_reason TEXT,
                status TEXT NOT NULL,
                resolved_by TEXT,
                resolved_reason TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_rsa_status ON policy_rollout_step_approvals(status);
            CREATE INDEX IF NOT EXISTS idx_rsa_rollout ON policy_rollout_step_approvals(rollout_id);
        """)
        self._conn.commit()

    async def create(self, approval: RolloutStepApproval) -> RolloutStepApproval:
        existing = await self.get_pending_for_step(approval.rollout_id, approval.step_id)
        if existing is not None:
            return existing
        self._conn.execute(
            """INSERT INTO policy_rollout_step_approvals
               (approval_id, rollout_id, step_id, bundle_id, environment,
                ring_name, requested_by, requested_reason, status,
                resolved_by, resolved_reason, created_at, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                approval.approval_id,
                approval.rollout_id,
                approval.step_id,
                approval.bundle_id,
                approval.environment,
                approval.ring_name,
                approval.requested_by,
                approval.requested_reason,
                approval.status.value,
                approval.resolved_by,
                approval.resolved_reason,
                approval.created_at.isoformat(),
                approval.resolved_at.isoformat() if approval.resolved_at else None,
            ),
        )
        self._conn.commit()
        return approval

    async def get(self, approval_id: str) -> RolloutStepApproval | None:
        row = self._conn.execute(
            "SELECT * FROM policy_rollout_step_approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_approval(row)

    async def get_pending_for_step(self, rollout_id: str, step_id: str) -> RolloutStepApproval | None:
        row = self._conn.execute(
            """SELECT * FROM policy_rollout_step_approvals
               WHERE rollout_id=? AND step_id=? AND status=?""",
            (rollout_id, step_id, RolloutStepApprovalStatus.PENDING.value),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_approval(row)

    async def approve(self, approval_id: str, approved_by: str, reason: str | None = None) -> RolloutStepApproval:
        row = self._conn.execute(
            "SELECT * FROM policy_rollout_step_approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Rollout step approval '{approval_id}' not found")
        approval = self._row_to_approval(row)
        if approval.status != RolloutStepApprovalStatus.PENDING:
            raise ValueError(f"Cannot approve approval '{approval_id}': status is {approval.status.value}, expected PENDING")
        now = datetime.now()
        self._conn.execute(
            """UPDATE policy_rollout_step_approvals
               SET status=?, resolved_by=?, resolved_reason=?, resolved_at=?
               WHERE approval_id=?""",
            (
                RolloutStepApprovalStatus.APPROVED.value,
                approved_by,
                reason,
                now.isoformat(),
                approval_id,
            ),
        )
        self._conn.commit()
        approval.status = RolloutStepApprovalStatus.APPROVED
        approval.resolved_by = approved_by
        approval.resolved_reason = reason
        approval.resolved_at = now
        return approval

    async def reject(self, approval_id: str, rejected_by: str, reason: str = None) -> RolloutStepApproval:
        row = self._conn.execute(
            "SELECT * FROM policy_rollout_step_approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Rollout step approval '{approval_id}' not found")
        approval = self._row_to_approval(row)
        if approval.status != RolloutStepApprovalStatus.PENDING:
            raise ValueError(f"Cannot reject approval '{approval_id}': status is {approval.status.value}, expected PENDING")
        now = datetime.now()
        self._conn.execute(
            """UPDATE policy_rollout_step_approvals
               SET status=?, resolved_by=?, resolved_reason=?, resolved_at=?
               WHERE approval_id=?""",
            (
                RolloutStepApprovalStatus.REJECTED.value,
                rejected_by,
                reason,
                now.isoformat(),
                approval_id,
            ),
        )
        self._conn.commit()
        approval.status = RolloutStepApprovalStatus.REJECTED
        approval.resolved_by = rejected_by
        approval.resolved_reason = reason
        approval.resolved_at = now
        return approval

    async def cancel_for_step(self, rollout_id: str, step_id: str, cancelled_by: str, reason: str | None = None) -> RolloutStepApproval | None:
        approval = await self.get_pending_for_step(rollout_id, step_id)
        if approval is None:
            return None
        now = datetime.now()
        self._conn.execute(
            """UPDATE policy_rollout_step_approvals
               SET status=?, resolved_by=?, resolved_reason=?, resolved_at=?
               WHERE approval_id=?""",
            (
                RolloutStepApprovalStatus.CANCELLED.value,
                cancelled_by,
                reason,
                now.isoformat(),
                approval.approval_id,
            ),
        )
        self._conn.commit()
        approval.status = RolloutStepApprovalStatus.CANCELLED
        approval.resolved_by = cancelled_by
        approval.resolved_reason = reason
        approval.resolved_at = now
        return approval

    async def list(
        self,
        status: RolloutStepApprovalStatus | None = None,
        rollout_id: str | None = None,
    ) -> list[RolloutStepApproval]:
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        if rollout_id is not None:
            clauses.append("rollout_id=?")
            params.append(rollout_id)
        where = ""
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM policy_rollout_step_approvals{where} ORDER BY created_at ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_approval(row) for row in rows]

    def _row_to_approval(self, row: sqlite3.Row) -> RolloutStepApproval:
        data = dict(row)
        data["status"] = RolloutStepApprovalStatus(data["status"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data["resolved_at"] is not None:
            data["resolved_at"] = datetime.fromisoformat(data["resolved_at"])
        return RolloutStepApproval(**data)

    def close(self) -> None:
        self._conn.close()


def create_rollout_step_approval_store(
    store_type: str = "memory",
    db_path: str | None = None,
) -> RolloutStepApprovalStore:
    if store_type == "memory":
        return InMemoryRolloutStepApprovalStore()
    if store_type == "sqlite":
        if not db_path:
            raise ValueError("db_path is required when store_type='sqlite'")
        return SQLiteRolloutStepApprovalStore(db_path=db_path)
    raise ValueError(f"Unknown rollout step approval store type '{store_type}'. Supported: 'memory', 'sqlite'.")
